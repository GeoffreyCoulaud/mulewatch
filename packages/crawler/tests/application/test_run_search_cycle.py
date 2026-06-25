import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from catalog_matching.engine import MatchingEngine
from catalog_matching.models import TargetSegment
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_local
from emule_indexer.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from emule_indexer.application.edge_state import EdgeState
from emule_indexer.application.run_search_cycle import run_search_cycle
from emule_indexer.application.search_worker import (
    BackoffRegistry,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from emule_indexer.domain.observability.events import AllInstancesBlind
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus
from tests.application.fakes import (
    FakeClock,
    FakeMuleClient,
    FakeRng,
    RecordingSignal,
    RecordingTelemetry,
    UnreachableStatusClient,
)

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_TARGETS = (TargetSegment(season=2, number=62, segment="A", title="Les demoiselles cambrioleuses"),)

# keyword_pause 1.0..1.0 (min == max) → pause FIXE de 1.0s (jitter span 0) : chaque pause
# inter-mots-clés ajoute EXACTEMENT 1.0s aux clock.sleeps, ce qui rend la pause OBSERVABLE
# et l'assertion « between not after » exacte.
_POLICY = WorkerPolicy(
    backoff_base_seconds=2.0,
    backoff_cap_seconds=60.0,
    backoff_factor=2.0,
    backoff_jitter_ratio=0.0,
    poll_budget_seconds=10.0,
    poll_interval_seconds=5.0,
    keyword_pause_min_seconds=1.0,
    keyword_pause_max_seconds=1.0,
)


class _NoopRng:
    """Rng identité : conserve l'ordre + jitter nul (déterminisme du test)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        return 0.0


def _obs() -> FileObservation:
    return FileObservation(
        ed2k_hash=_HASH,
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


@pytest.fixture
def local_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_local(tmp_path / "local.db")
    yield connection
    connection.close()


def _worker(name: str, client: FakeMuleClient, deps: WorkerDeps) -> SearchWorker:
    return SearchWorker(name, client, deps)


def _deps(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    clock: FakeClock,
    backoff: BackoffRegistry,
    *,
    telemetry: RecordingTelemetry | None = None,
) -> WorkerDeps:
    return WorkerDeps(
        catalog=catalog,
        engine=engine,
        signal=RecordingSignal(),
        clock=clock,
        rng=_NoopRng(),
        policy=_POLICY,
        backoff=backoff,
        telemetry=telemetry or RecordingTelemetry(),
    )


@pytest.mark.asyncio
async def test_single_instance_cycle_records_and_advances(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient(results=[(_obs(),)])  # le download apparaît sur le 1er fetch
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert scheduler_state.read_cycle_index() == 1  # index = N+1, persisté en fin de cycle


@pytest.mark.asyncio
async def test_two_workers_drain_the_same_queue(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=3,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    total_searches = len(client_a.searches) + len(client_b.searches)
    assert total_searches >= 2  # toutes les tâches distribuées entre les deux travailleurs
    assert scheduler_state.read_cycle_index() == 4


@pytest.mark.asyncio
async def test_one_instance_blind_still_runs_others(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    blind = NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)
    healthy = NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)
    client_a = FakeMuleClient(status=blind)
    client_b = FakeMuleClient(status=healthy)
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    assert scheduler_state.read_cycle_index() == 1  # le cycle tourne (DEGRADED), aucune exception


@pytest.mark.asyncio
async def test_cycle_logs_blind_coverage(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    blind = NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)
    client = FakeMuleClient(status=blind)
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    with caplog.at_level(logging.INFO, logger="emule_indexer.application.run_search_cycle"):
        await run_search_cycle(
            workers=[worker],
            clients=[client],
            targets=_TARGETS,
            rng=_NoopRng(),
            node_id="node-A",
            cycle_index=0,
            scheduler_state=scheduler_state,
            backoff=backoff,
            clock=clock,
            telemetry=RecordingTelemetry(),
            edge=EdgeState(),
        )
    assert "blind" in caplog.text


@pytest.mark.asyncio
async def test_unreachable_status_makes_instance_not_capable_and_logs_blind(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # network_status lève MuleUnreachableError (instance injoignable, p.ex. non connectée au
    # moment du relevé de coverage) → l'instance est traitée NON search-capable au lieu de
    # faire tomber tout le cycle. Une seule instance, toutes injoignables → BLIND loggé, et le
    # cycle AVANCE quand même (résilience, spec §7).
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = UnreachableStatusClient()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    with caplog.at_level(logging.WARNING, logger="emule_indexer.application.run_search_cycle"):
        await run_search_cycle(
            workers=[worker],
            clients=[client],
            targets=_TARGETS,
            rng=_NoopRng(),
            node_id="node-A",
            cycle_index=0,
            scheduler_state=scheduler_state,
            backoff=backoff,
            clock=clock,
            telemetry=RecordingTelemetry(),
            edge=EdgeState(),
        )
    assert "injoignable" in caplog.text  # warning au relevé de statut de l'instance down
    assert "blind" in caplog.text  # effective_coverage=BLIND (aucune instance capable)
    assert scheduler_state.read_cycle_index() == 1  # le cycle a quand même avancé


@pytest.mark.asyncio
async def test_unreachable_instance_does_not_blind_a_healthy_peer(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Une instance injoignable (status lève) + une instance saine → DEGRADED (pas BLIND) :
    # la branche tolérante ne contamine PAS le pair sain (couvre le côté capable=True du for).
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    down = UnreachableStatusClient()
    healthy = FakeMuleClient()  # status par défaut : HighID + Kad CONNECTED → capable
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", down, deps), _worker("amule-2", healthy, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    with caplog.at_level(logging.INFO, logger="emule_indexer.application.run_search_cycle"):
        await run_search_cycle(
            workers=workers,
            clients=[down, healthy],
            targets=_TARGETS,
            rng=_NoopRng(),
            node_id="node-A",
            cycle_index=0,
            scheduler_state=scheduler_state,
            backoff=backoff,
            clock=clock,
            telemetry=RecordingTelemetry(),
            edge=EdgeState(),
        )
    assert "blind" not in caplog.text  # un pair reste capable → pas aveugle
    assert scheduler_state.read_cycle_index() == 1


@pytest.mark.asyncio
async def test_channel_backoff_is_persisted_at_cycle_end(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Une recherche échoue (EC_OP_FAILED) → le canal entre en backoff DANS le registre
    # partagé ; le cycle PERSISTE le snapshot en fin de cycle (spec §3/§7). Une nouvelle
    # instance de repo (simulant un redémarrage) relit ce backoff.
    from emule_indexer.ports.mule_client import MuleSearchFailedError, SearchChannel

    class _AlwaysFails(FakeMuleClient):
        """Échoue à CHAQUE recherche → les canaux RESTENT en backoff (jamais reset)."""

        async def start_search(self, keyword: str, channel: SearchChannel) -> None:
            raise MuleSearchFailedError("EC_OP_FAILED")

    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = _AlwaysFails()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    persisted = SqliteSchedulerStateRepository(local_connection).load_channel_backoff()
    # Les deux canaux de amule-1 sont en backoff persisté (toutes les recherches échouent).
    assert any(key.startswith("amule-1:") for key in persisted)


@pytest.mark.asyncio
async def test_one_worker_pauses_between_items_not_after_the_last(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # UN seul travailleur draine TOUS les items → la pause inter-mots-clés (fixe 1.0s,
    # min==max ; search_progress=100 → aucun sleep de polling) tombe ENTRE deux items et
    # JAMAIS après le dernier : exactement (N_items - 1) pauses de 1.0s.
    from emule_indexer.application.run_search_cycle import _CHANNELS
    from emule_indexer.domain.search.keywords import generate_keywords

    n_items = len(generate_keywords(_TARGETS)) * len(_CHANNELS)
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient()  # search_progress=100 → pas de sleep de polling
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    assert clock.sleeps == [1.0] * (n_items - 1)  # entre chaque item, pas après le dernier


@pytest.mark.asyncio
async def test_drained_queue_skips_the_final_pause_with_two_workers(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Deux travailleurs PARTAGENT l'horloge fausse : la pause n'est dormie qu'entre deux
    # items réels (le garde « queue non vide »). Le total des pauses est STRICTEMENT inférieur
    # au nombre d'items (au moins le dernier item de chaque drain ne déclenche pas de pause).
    from emule_indexer.application.run_search_cycle import _CHANNELS
    from emule_indexer.domain.search.keywords import generate_keywords

    n_items = len(generate_keywords(_TARGETS)) * len(_CHANNELS)
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    # Toutes les pauses valent 1.0s ; il y en a STRICTEMENT moins que d'items (la dernière de
    # chaque travailleur est sautée car la file est vidée).
    assert all(s == 1.0 for s in clock.sleeps)
    assert 0 < len(clock.sleeps) < n_items


@pytest.mark.asyncio
async def test_worker_in_backoff_does_not_consume_peers_tasks(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Régression logic-search#0 (spec §14 « PAS DE PERTE ») : un worker dont l'instance est en
    # backoff ne doit PAS drainer/jeter les tâches restantes. La queue est partagée → si A
    # est en backoff et B sain, B doit traiter TOUTES les tâches (pas la moitié).
    from emule_indexer.application.run_search_cycle import _CHANNELS
    from emule_indexer.domain.search.keywords import generate_keywords

    n_items = len(generate_keywords(_TARGETS)) * len(_CHANNELS)
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    # 4 échecs cumulés → base × factor^3 = 2×8 = 16 s, retry_after très au-delà des pauses
    # cumulées du cycle (au plus 9s avec 10 items × pause 1.0s, hors dernier).
    for _ in range(4):
        backoff.record_failure("amule-1")
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    assert client_a.searches == []  # A en backoff → n'exécute aucune recherche
    assert len(client_b.searches) == n_items  # B traite TOUTES les tâches (zéro perte)


@pytest.mark.asyncio
async def test_all_workers_in_backoff_drop_tasks_with_telemetry(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Cas terminal : si TOUTES les instances sont en backoff, plus personne ne peut traiter →
    # les tâches sont DROP avec une trace de télémétrie (visibilité), et le cycle se termine
    # quand même (queue drainée, index avance).
    from emule_indexer.application.run_search_cycle import _CHANNELS
    from emule_indexer.domain.observability.events import SearchTaskDropped
    from emule_indexer.domain.search.keywords import generate_keywords

    n_items = len(generate_keywords(_TARGETS)) * len(_CHANNELS)
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    # 4 échecs par instance → backoff au-delà de la durée du cycle (cf. test précédent).
    for _ in range(4):
        backoff.record_failure("amule-1")
        backoff.record_failure("amule-2")
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    telemetry = RecordingTelemetry()
    deps = _deps(catalog, engine, clock, backoff, telemetry=telemetry)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=telemetry,
        edge=EdgeState(),
    )
    assert client_a.searches == []
    assert client_b.searches == []
    drops = [e for e in telemetry.events if isinstance(e, SearchTaskDropped)]
    assert len(drops) == n_items  # une trace par tâche perdue (visibilité opérationnelle)
    assert scheduler_state.read_cycle_index() == 1  # le cycle se termine quand même


@pytest.mark.asyncio
async def test_repository_error_on_write_cycle_state_is_absorbed(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Régression error-boundary#1 : une RepositoryError sur write_cycle_state ne doit PAS
    # propager hors de run_search_cycle (le TaskGroup superviseur annulerait les 3 autres
    # boucles → crash de l'app). Alignée sur run_download/verify (« NE LÈVE JAMAIS »).
    from emule_indexer.ports.repository_errors import RepositoryError
    from emule_indexer.ports.scheduler_state_repository import ChannelBackoff

    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    inner = SqliteSchedulerStateRepository(local_connection)

    class _SchedulerWriteRaises:
        def read_cycle_index(self) -> int:
            return inner.read_cycle_index()

        def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
            return inner.load_channel_backoff()

        def write_cycle_state(self, cycle_index: int, last_full_cycle_at: object) -> None:
            raise RepositoryError("disque plein")

        def save_channel_backoff(self, snapshot: dict[str, ChannelBackoff]) -> None:
            inner.save_channel_backoff(snapshot)

    with caplog.at_level(logging.ERROR, logger="emule_indexer.application.run_search_cycle"):
        await run_search_cycle(
            workers=[worker],
            clients=[client],
            targets=_TARGETS,
            rng=_NoopRng(),
            node_id="node-A",
            cycle_index=0,
            scheduler_state=_SchedulerWriteRaises(),
            backoff=backoff,
            clock=clock,
            telemetry=RecordingTelemetry(),
            edge=EdgeState(),
        )
    assert "repo" in caplog.text.lower()
    assert inner.read_cycle_index() == 0  # index n'a PAS avancé : rejouera le cycle


@pytest.mark.asyncio
async def test_repository_error_on_save_channel_backoff_is_absorbed(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Régression error-boundary#1 (symétrique) : une RepositoryError sur save_channel_backoff
    # est absorbée de la même façon. L'index ne doit PAS avancer (atomicité §3/§7 : si l'un
    # des deux échoue, le cycle est rejouable au prochain démarrage).
    from emule_indexer.ports.repository_errors import RepositoryError
    from emule_indexer.ports.scheduler_state_repository import ChannelBackoff

    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    inner = SqliteSchedulerStateRepository(local_connection)

    class _SchedulerSaveRaises:
        def read_cycle_index(self) -> int:
            return inner.read_cycle_index()

        def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
            return inner.load_channel_backoff()

        def write_cycle_state(self, cycle_index: int, last_full_cycle_at: object) -> None:
            inner.write_cycle_state(cycle_index, last_full_cycle_at)  # type: ignore[arg-type]

        def save_channel_backoff(self, snapshot: dict[str, ChannelBackoff]) -> None:
            raise RepositoryError("local.db verrouillé")

    with caplog.at_level(logging.ERROR, logger="emule_indexer.application.run_search_cycle"):
        await run_search_cycle(
            workers=[worker],
            clients=[client],
            targets=_TARGETS,
            rng=_NoopRng(),
            node_id="node-A",
            cycle_index=0,
            scheduler_state=_SchedulerSaveRaises(),
            backoff=backoff,
            clock=clock,
            telemetry=RecordingTelemetry(),
            edge=EdgeState(),
        )
    assert "repo" in caplog.text.lower()


@pytest.mark.asyncio
async def test_emits_cycle_completed_and_connected_gauges(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient()  # status par défaut: ed2k_high=True, kad CONNECTED → capable
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    types = [type(e).__name__ for e in telemetry.events]
    assert "ConnectedInstancesSampled" in types
    assert types[-1] == "SearchCycleCompleted"


@pytest.mark.asyncio
async def test_blind_coverage_is_edge_triggered(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = UnreachableStatusClient()  # tous non search-capable → BLIND
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    blind = [e for e in telemetry.events if isinstance(e, AllInstancesBlind)]
    assert blind and blind[0].first_occurrence is True
    # 2e cycle aveugle consécutif → first_occurrence False (anti-spam)
    telemetry.events.clear()
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=1,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    blind = [e for e in telemetry.events if isinstance(e, AllInstancesBlind)]
    assert blind and blind[0].first_occurrence is False
