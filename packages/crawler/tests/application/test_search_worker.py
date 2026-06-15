import dataclasses
import sqlite3

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.application.search_worker import (
    BackoffRegistry,
    SearchTask,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.observability.events import (
    InstanceUnreachable,
    SearchExecuted,
    SearchFailed,
)
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import SearchChannel
from emule_indexer.ports.scheduler_state_repository import ChannelBackoff
from tests.application.fakes import (
    FakeClock,
    FakeMuleClient,
    FakeRng,
    RecordingSignal,
    RecordingTelemetry,
    make_search_failed,
    make_unreachable,
)

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"

# jitter_ratio 0.0 + FakeRng(jitter_value=0.0) → backoff = délai NOMINAL exact (assertions nettes).
# keyword_pause 1.0..3.0 : pause inter-mots-clés = 1.0 + jitter(2.0) (1.0 fixe avec FakeRng(0.0)).
_POLICY = WorkerPolicy(
    backoff_base_seconds=2.0,
    backoff_cap_seconds=60.0,
    backoff_factor=2.0,
    backoff_jitter_ratio=0.0,
    poll_budget_seconds=10.0,
    poll_interval_seconds=5.0,
    keyword_pause_min_seconds=1.0,
    keyword_pause_max_seconds=3.0,
)


def _obs() -> FileObservation:
    return FileObservation(
        ed2k_hash=_HASH,
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


def _registry(clock: FakeClock, rng: FakeRng | None = None) -> BackoffRegistry:
    return BackoffRegistry(_POLICY, clock, rng or FakeRng())


def _deps(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    clock: FakeClock,
    backoff: BackoffRegistry,
    *,
    rng: FakeRng | None = None,
    policy: WorkerPolicy = _POLICY,
    telemetry: RecordingTelemetry | None = None,
) -> WorkerDeps:
    return WorkerDeps(
        catalog=catalog,
        engine=engine,
        signal=RecordingSignal(),
        clock=clock,
        rng=rng or FakeRng(),
        policy=policy,
        backoff=backoff,
        telemetry=telemetry or RecordingTelemetry(),
    )


# --- BackoffRegistry (logique, déterministe via clock/rng faux) ---


def test_backoff_registry_grows_then_resets() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    assert registry.record_failure("k") == 2.0  # 1re tentative = base
    assert registry.record_failure("k") == 4.0  # × factor
    registry.reset("k")
    assert registry.record_failure("k") == 2.0  # repart à la base


def test_backoff_registry_keys_are_independent() -> None:
    registry = _registry(FakeClock())
    assert registry.record_failure("a") == 2.0
    assert registry.record_failure("b") == 2.0  # 'b' n'a pas hérité du compteur de 'a'


def test_backoff_registry_reset_unknown_key_is_a_noop() -> None:
    _registry(FakeClock()).reset("jamais-vu")  # ne lève pas


def test_backoff_registry_sets_retry_after_in_the_future() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")
    assert registry.is_in_backoff("amule-1:kad") is True
    clock.advance(1.9)  # encore avant retry_after (2.0s)
    assert registry.is_in_backoff("amule-1:kad") is True
    clock.advance(0.2)  # désormais après retry_after
    assert registry.is_in_backoff("amule-1:kad") is False


def test_backoff_registry_unknown_key_is_not_in_backoff() -> None:
    assert _registry(FakeClock()).is_in_backoff("inconnu") is False


def test_backoff_registry_jitter_extends_the_delay() -> None:
    clock = FakeClock()
    policy = dataclasses.replace(_POLICY, backoff_jitter_ratio=0.5)  # jitter dans [0, 0.5*délai)
    rng = FakeRng(jitter_value=1.0)  # jitter constant de 1.0s
    registry = BackoffRegistry(policy, clock, rng)
    delay = registry.record_failure("k")
    assert delay == 3.0  # base 2.0 + jitter 1.0
    assert rng.jitter_spans == [1.0]  # span = jitter_ratio (0.5) * délai (2.0)


def test_backoff_registry_snapshot_and_load_round_trip() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")
    snapshot = registry.snapshot()
    assert "amule-1:kad" in snapshot
    assert isinstance(snapshot["amule-1:kad"], ChannelBackoff)
    # Recharge dans un registre NEUF (simule un redémarrage) → même skip appliqué.
    reborn = _registry(clock)
    assert reborn.is_in_backoff("amule-1:kad") is False  # vide avant load
    reborn.load_from(snapshot)
    assert reborn.is_in_backoff("amule-1:kad") is True


# --- pause inter-mots-clés (anti-rate-limit, spec §5/§7) ---


@pytest.mark.asyncio
async def test_pause_between_items_sleeps_min_plus_jitter(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # span = max - min = 3 - 1 = 2 ; FakeRng(jitter_value=0.5) → pause = 1.0 + 0.5 = 1.5.
    clock = FakeClock()
    rng = FakeRng(jitter_value=0.5)
    deps = _deps(catalog, engine, clock, _registry(clock), rng=rng)
    worker = SearchWorker("amule-1", FakeMuleClient(), deps)
    await worker.pause_between_items()
    assert clock.sleeps == [1.5]
    assert rng.jitter_spans == [2.0]  # span passé au jitter = max - min


@pytest.mark.asyncio
async def test_pause_with_equal_bounds_is_a_fixed_pause(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # min == max → span 0 → jitter(0) == 0 (contrat du port, respecté par FakeRng/SeededRng)
    # → pause FIXE = min, indépendante du jitter (même un jitter énorme n'a aucun effet).
    clock = FakeClock()
    fixed = dataclasses.replace(
        _POLICY, keyword_pause_min_seconds=2.0, keyword_pause_max_seconds=2.0
    )
    rng = FakeRng(jitter_value=99.0)
    deps = _deps(catalog, engine, clock, _registry(clock), rng=rng, policy=fixed)
    worker = SearchWorker("amule-1", FakeMuleClient(), deps)
    await worker.pause_between_items()
    assert clock.sleeps == [2.0]  # pause fixe = min, jitter inopérant
    assert rng.jitter_spans == [0.0]


# --- SearchWorker ---


@pytest.mark.asyncio
async def test_successful_task_records_observation(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert client.searches == [("keroro", SearchChannel.GLOBAL)]
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_multiple_observations_some_unchanged_are_all_processed(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    discarded = FileObservation(
        ed2k_hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        filename="random.txt",  # écarté par le moteur → record_observation rend False
        size_bytes=10,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )
    # Deux observations dans le même relevé : la 1re est écartée (False → loop back), la 2e
    # change un verdict. Couvre l'arête « if False → observation suivante ».
    client = FakeMuleClient(results=[(discarded, _obs())])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert catalog_connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 2
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_connect_failure_arms_instance_backoff_and_skips_the_item(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    client = FakeMuleClient(connect_failures=[make_unreachable()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert client.searches == []  # item abandonné, jamais recherché
    assert registry.is_in_backoff("amule-1") is True  # instance en backoff (skip jusqu'à retry)
    assert clock.sleeps == []  # plus de sleep du backoff : on SKIP, on n'attend pas


@pytest.mark.asyncio
async def test_instance_in_backoff_skips_without_connecting(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1")  # instance déjà en backoff
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert client.connect_calls == 0  # ni connexion ni recherche : sauté
    assert client.searches == []


@pytest.mark.asyncio
async def test_channel_in_backoff_skips_that_item(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")  # canal kad en backoff
    client = FakeMuleClient(results=[(_obs(),), (_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="k", channel=SearchChannel.KAD))  # sauté
    assert client.searches == []
    await worker.run_task(SearchTask(keyword="k", channel=SearchChannel.GLOBAL))  # autre canal OK
    assert client.searches == [("k", SearchChannel.GLOBAL)]


@pytest.mark.asyncio
async def test_backoff_expires_and_item_runs_again(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")  # retry_after = +2.0s
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    clock.advance(3.0)  # retry_after dépassé → le canal n'est plus en backoff
    await worker.run_task(SearchTask(keyword="k", channel=SearchChannel.KAD))
    assert client.searches == [("k", SearchChannel.KAD)]


@pytest.mark.asyncio
async def test_already_connected_does_not_reconnect(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    client = FakeMuleClient(results=[(), ()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="k1", channel=SearchChannel.GLOBAL))
    await worker.run_task(SearchTask(keyword="k2", channel=SearchChannel.GLOBAL))
    assert client.connect_calls == 1  # connecté une seule fois pour deux tâches


@pytest.mark.asyncio
async def test_search_failure_arms_channel_backoff(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    client = FakeMuleClient(search_failures=[make_search_failed()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert registry.is_in_backoff("amule-1:global") is True  # canal en backoff
    assert registry.is_in_backoff("amule-1") is False  # mais pas l'instance entière
    assert client.fetch_calls == 0  # pas de fetch après l'échec de start_search


@pytest.mark.asyncio
async def test_transport_failure_marks_instance_down(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    # start_search lève une panne de transport (flux mort) → instance down + backoff instance.
    client = FakeMuleClient(search_failures=[make_unreachable()], results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="k1", channel=SearchChannel.GLOBAL))
    assert registry.is_in_backoff("amule-1") is True
    # Après expiration du backoff, la tâche suivante FORCE une reconnexion (down marqué).
    clock.advance(3.0)
    await worker.run_task(SearchTask(keyword="k2", channel=SearchChannel.GLOBAL))
    assert client.connect_calls == 2


@pytest.mark.asyncio
async def test_poll_budget_is_respected_when_progress_never_completes(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()

    class _NeverDone(FakeMuleClient):
        async def search_progress(self) -> int | None:
            return 10  # jamais 100 % → on poll jusqu'au budget

    client = _NeverDone(results=[()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    # budget 10 / pas 5 → deux pas de polling, puis fetch.
    assert clock.sleeps == [5.0, 5.0]
    assert client.fetch_calls == 1


@pytest.mark.asyncio
async def test_poll_loops_once_then_completes(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()

    class _ThenDone(FakeMuleClient):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)  # type: ignore[arg-type]
            self._calls = 0

        async def search_progress(self) -> int | None:
            self._calls += 1
            return 100 if self._calls >= 2 else 10  # 1er relevé : pas fini ; 2e : fini

    client = _ThenDone(results=[()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert clock.sleeps == [5.0]  # un pas de polling, puis break au 2e relevé


@pytest.mark.asyncio
async def test_poll_stops_when_progress_is_none_but_budget_bounds_it(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()

    class _NoProgress(FakeMuleClient):
        async def search_progress(self) -> int | None:
            return None  # EC n'expose pas la progression → on poll jusqu'au budget

    client = _NoProgress(results=[()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert clock.sleeps == [5.0, 5.0]


# --- émission d'événements d'observabilité (Plan E.2) ---


@pytest.mark.asyncio
async def test_successful_search_emits_search_executed_with_network_and_count(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # Une recherche GLOBAL réussie émet SearchExecuted(network="ed2k", n_results=1) en TÊTE,
    # avant les événements par observation (ObservationRecorded/DecisionRecorded).
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(results=[(_obs(),)])
    deps = _deps(catalog, engine, clock, _registry(clock), telemetry=telemetry)
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert telemetry.events[0] == SearchExecuted(network="ed2k", n_results=1)
    kinds = [type(e).__name__ for e in telemetry.events]
    assert kinds == ["SearchExecuted", "ObservationRecorded", "DecisionRecorded"]


@pytest.mark.asyncio
async def test_search_failure_emits_search_failed(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # Un échec applicatif de canal (MuleSearchFailedError) émet SearchFailed(instance, network).
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(search_failures=[make_search_failed()])
    deps = _deps(catalog, engine, clock, _registry(clock), telemetry=telemetry)
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert telemetry.events == [SearchFailed(instance="amule-1", network="ed2k")]


@pytest.mark.asyncio
async def test_connect_failure_emits_instance_unreachable(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # Un échec de connexion (instance injoignable) émet InstanceUnreachable(instance).
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(connect_failures=[make_unreachable()])
    deps = _deps(catalog, engine, clock, _registry(clock), telemetry=telemetry)
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert telemetry.events == [InstanceUnreachable(instance="amule-1")]


@pytest.mark.asyncio
async def test_transport_failure_during_search_emits_instance_unreachable(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # Une panne de transport pendant start_search (flux mort) émet InstanceUnreachable.
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(search_failures=[make_unreachable()], results=[(_obs(),)])
    deps = _deps(catalog, engine, clock, _registry(clock), telemetry=telemetry)
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert telemetry.events == [InstanceUnreachable(instance="amule-1")]
