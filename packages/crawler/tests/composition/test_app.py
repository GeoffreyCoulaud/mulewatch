import asyncio
import datetime
import logging
import sqlite3
from pathlib import Path

import pytest

from emule_indexer.adapters.config.crawler_config import BackoffConfig, CrawlerConfig
from emule_indexer.adapters.config.local_config import AmuleEndpoint, LocalConfig
from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.composition.app import CrawlerApp, default_client_factory
from emule_indexer.domain.matching.config import MatcherConfig
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.matching.validation import parse_matcher_config
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import MuleUnreachableError, NetworkStatus
from tests.application.fakes import FakeClock, FakeMuleClient, RecordingSignal

_TARGETS = (
    TargetSegment(
        season=2,
        number=62,
        segment="A",
        title="Les demoiselles cambrioleuses",
        broadcast_date=datetime.date(2008, 9, 21),
    ),
)
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"


class _NoopRng:
    """Rng identité : conserve l'ordre + jitter nul (déterminisme du test)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        return 0.0


@pytest.fixture
def matcher_config() -> MatcherConfig:
    return parse_matcher_config(load_yaml(_FIXTURES / "canonical_config.yaml"))


def _crawler_config(shutdown_deadline: float = 30.0) -> CrawlerConfig:
    return CrawlerConfig(
        cycle_interval_seconds=300.0,
        search_poll_budget_seconds=10.0,
        search_poll_interval_seconds=5.0,
        keyword_pause_min_seconds=1.0,
        keyword_pause_max_seconds=2.0,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=60.0, factor=2.0, jitter_ratio=0.0),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=shutdown_deadline,
    )


def _local_config(tmp_path: Path, *, count: int = 1, node_id: str | None = None) -> LocalConfig:
    return LocalConfig(
        amules=tuple(
            AmuleEndpoint(name=f"amule-{i}", host="h", port=4712 + i, password="p")
            for i in range(count)
        ),
        catalog_db_path=str(tmp_path / "catalog.db"),
        local_db_path=str(tmp_path / "local.db"),
        node_id=node_id,
    )


def _make_app(
    tmp_path: Path,
    matcher_config: MatcherConfig,
    *,
    factory: object,
    clock: FakeClock | None = None,
    node_id: str | None = None,
    shutdown_deadline: float = 30.0,
) -> CrawlerApp:
    return CrawlerApp(
        crawler_config=_crawler_config(shutdown_deadline),
        local_config=_local_config(tmp_path, node_id=node_id),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=clock or FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=factory,  # type: ignore[arg-type]
    )


class _ShutdownOnStatusClient(FakeMuleClient):
    """Client qui déclenche l'arrêt de l'app au PREMIER relevé de statut (1 cycle puis stop)."""

    def __init__(
        self,
        app_holder: dict[str, CrawlerApp],
        results: list[tuple[FileObservation, ...]] | None = None,
    ) -> None:
        super().__init__(results=results)
        self._app_holder = app_holder
        self._fired = False

    async def network_status(self) -> NetworkStatus:
        if not self._fired:
            self._fired = True
            self._app_holder["app"]._on_signal()  # simule un SIGINT après le démarrage du cycle
        return await super().network_status()


@pytest.mark.asyncio
async def test_app_runs_one_cycle_then_shuts_down_cleanly(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    created: list[_ShutdownOnStatusClient] = []
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        client = _ShutdownOnStatusClient(app_holder)
        created.append(client)
        return client

    app = _make_app(tmp_path, matcher_config, factory=factory)
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert created and created[0].close_calls == 1  # client fermé APRÈS l'unwind
    assert created[0].connect_calls >= 1  # connecté au montage du pool (avant le coverage)
    assert (tmp_path / "catalog.db").exists()
    assert (tmp_path / "local.db").exists()


class _OrderRecordingClient(FakeMuleClient):
    """Enregistre l'ORDRE des appels (connect / network_status) pour prouver le bug d'ordre.

    Le bug : ``_aggregate_coverage`` relève le statut AVANT toute connexion → le 1er
    ``network_status`` frappe un client non connecté et lève. La correction connecte au
    montage du pool → ``connect`` PRÉCÈDE le 1er ``network_status`` sur chaque client. Un
    seul client du pool déclenche l'arrêt (drapeau PARTAGÉ) → le run est borné à un cycle
    sans double-signal (qui escaladerait en SystemExit)."""

    def __init__(
        self, app_holder: dict[str, CrawlerApp], events: list[str], fired: list[bool]
    ) -> None:
        super().__init__()
        self._app_holder = app_holder
        self._events = events
        self._fired = fired  # partagé par tout le pool : un seul arrêt

    async def connect(self) -> None:
        self._events.append("connect")
        await super().connect()

    async def network_status(self) -> NetworkStatus:
        self._events.append("status")
        if not self._fired:
            self._fired.append(True)
            self._app_holder["app"]._on_signal()
        return await super().network_status()


@pytest.mark.asyncio
async def test_pool_setup_connects_each_client_before_coverage(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # Le composition root CONNECTE chaque client au montage du pool, AVANT que
    # _aggregate_coverage ne relève le statut : sinon le 1er network_status frappe un client
    # non connecté et lève (bug d'ordre attrapé par l'e2e). On vérifie, sur CHAQUE client d'un
    # pool multi-instances, que le 1er événement observé est un connect (et non un status).
    created: list[_OrderRecordingClient] = []
    events: dict[str, list[str]] = {}
    fired: list[bool] = []  # partagé : un seul arrêt pour tout le pool
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _OrderRecordingClient:
        log: list[str] = []
        events[endpoint.name] = log
        client = _OrderRecordingClient(app_holder, log, fired)
        created.append(client)
        return client

    app = CrawlerApp(
        crawler_config=_crawler_config(),
        local_config=_local_config(tmp_path, count=2),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=factory,
    )
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert len(created) == 2
    for log in events.values():
        assert log[0] == "connect"  # connecté au montage AVANT tout relevé de statut
        assert "status" in log  # le coverage a bien relevé le statut ensuite


class _UnreachableAtStartupClient(_ShutdownOnStatusClient):
    """Client dont le 1er ``connect`` (au montage du pool) lève ``MuleUnreachableError``.

    Modélise un daemon down au démarrage : le composition root doit ATTRAPER, logger, et
    CONTINUER (un crawler multi-instances ne tombe pas parce qu'une instance est down ;
    le backoff du travailleur gouvernera les reconnexions). Déclenche aussi l'arrêt au 1er
    relevé de statut pour borner le run à un cycle."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__(app_holder, results=None)
        self._connect_seen = 0

    async def connect(self) -> None:
        self._connect_seen += 1
        self.connect_calls += 1
        if self._connect_seen == 1:
            raise MuleUnreachableError("daemon injoignable au démarrage")


@pytest.mark.asyncio
async def test_unreachable_client_at_startup_does_not_crash_the_run(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    # Un client injoignable au montage du pool (connect lève MuleUnreachableError) ne doit PAS
    # faire tomber run() : le composition root attrape, logge un warning NOMMANT l'instance, et
    # CONTINUE. La phase de cycle tourne quand même (network_status atteint → l'arrêt fire).
    created: list[_UnreachableAtStartupClient] = []
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _UnreachableAtStartupClient:
        client = _UnreachableAtStartupClient(app_holder)
        created.append(client)
        return client

    app = _make_app(tmp_path, matcher_config, factory=factory)
    app_holder["app"] = app
    with caplog.at_level(logging.WARNING, logger="emule_indexer.composition.app"):
        await asyncio.wait_for(app.run(), timeout=5.0)  # ne lève PAS (instance down tolérée)
    # Le warning de tolérance vient du COMPOSITION ROOT (pas du travailleur) et nomme
    # l'instance : c'est la branche `except MuleUnreachableError` du montage du pool.
    startup_warnings = [
        record
        for record in caplog.records
        if record.name == "emule_indexer.composition.app" and record.levelno == logging.WARNING
    ]
    assert startup_warnings, "le composition root doit logger la tolérance au démarrage"
    assert "amule-0" in startup_warnings[0].getMessage()  # le warning nomme l'instance down
    assert created and created[0].connect_calls >= 1  # connect tenté au montage (puis re-tenté)
    assert created[0]._fired  # network_status atteint → la phase de cycle a bien démarré


@pytest.mark.asyncio
async def test_node_id_override_is_used(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(app_holder, results=[(observation,)])

    app = _make_app(tmp_path, matcher_config, factory=factory, node_id="forced-node")
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    catalog = sqlite3.connect(tmp_path / "catalog.db")
    try:
        rows = catalog.execute("SELECT DISTINCT node_id FROM file_observations").fetchall()
    finally:
        catalog.close()
    assert rows == [("forced-node",)]


@pytest.mark.asyncio
async def test_second_signal_forces_exit(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient())
    app._on_signal()  # 1er signal : demande d'arrêt
    with pytest.raises(SystemExit):
        app._on_signal()  # 2e signal : escalade → SystemExit


class _ShutdownOnSleepClock(FakeClock):
    """Horloge qui déclenche l'arrêt sur le LONG sleep inter-cycle (≥ 100s), PAS sur les
    courtes pauses inter-mots-clés (1-2s) → le cycle se TERMINE, puis la boucle re-teste sa
    condition et SORT d'elle-même (sans annulation) au tour suivant."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__()
        self._app_holder = app_holder

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds)
        if seconds >= 100.0:  # le sommeil inter-cycle (cycle_interval − écoulé), pas une pause
            self._app_holder["app"]._shutdown.set()


@pytest.mark.asyncio
async def test_loop_exits_cleanly_when_shutdown_set_during_sleep(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # L'arrêt est posé pendant le sleep inter-cycle : la boucle re-teste sa condition et
    # SORT d'elle-même (sans annulation) → couvre la sortie normale du `while`.
    app_holder: dict[str, CrawlerApp] = {}
    clock = _ShutdownOnSleepClock(app_holder)
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient(), clock=clock)
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)


class _BlockingClient(FakeMuleClient):
    """Client dont ``fetch_results`` BLOQUE : la boucle reste en vol → l'annulation la frappe."""

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        await asyncio.Event().wait()  # ne se résout jamais : bloque jusqu'à annulation
        return ()


@pytest.mark.asyncio
async def test_signal_cancels_an_in_flight_cycle(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # Un travailleur est BLOQUÉ dans fetch_results ; un SIGINT externe annule le TaskGroup →
    # couvre le chemin d'annulation (unwind propre + ligne « Travailleurs arrêtés »).
    app = _make_app(tmp_path, matcher_config, factory=lambda e: _BlockingClient())
    run_task = asyncio.create_task(app.run())
    for _ in range(20):  # laisse le cycle démarrer et bloquer dans fetch_results
        await asyncio.sleep(0)
    app._on_signal()
    await asyncio.wait_for(run_task, timeout=5.0)


class _RealPacedClient(FakeMuleClient):
    """Client qui cadence chaque cycle par un PETIT sleep RÉEL (pas le FakeClock).

    Sert à prouver l'invariant temporel : ``network_status`` cède du temps RÉEL au lieu de
    busy-spinner, donc la boucle de cycles s'écoule à un rythme réel maîtrisé. Le run normal
    (sans signal) doit DÉPASSER ``shutdown_deadline_seconds`` de temps réel sans lever
    ``TimeoutError`` — la borne d'arrêt ne doit PAS armer tant que l'arrêt n'est pas demandé."""

    async def network_status(self) -> NetworkStatus:
        await asyncio.sleep(0.01)  # temps RÉEL : la boucle n'occupe pas l'event loop à 100 %
        return await super().network_status()


@pytest.mark.asyncio
async def test_normal_run_outlives_shutdown_deadline_without_a_signal(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # Régression (spec §6, DÉCISION 6) : la borne d'arrêt ne couvre QUE la phase d'arrêt. Un
    # run normal SANS signal doit tourner indéfiniment — donc survivre BIEN au-delà de
    # ``shutdown_deadline_seconds`` de temps RÉEL. Avant correctif, ``asyncio.timeout`` enrobait
    # tout ``_supervise`` (l'attente NON bornée du signal incluse) sur l'horloge RÉELLE → le run
    # levait ``TimeoutError`` ~deadline après le démarrage, sans aucun arrêt demandé. Ici la
    # deadline est minuscule (0.2 s) et on laisse passer 0.4 s de temps réel : le run doit
    # ENCORE tourner, sans avoir levé. Puis on demande l'arrêt → il se termine PROPREMENT.
    app = _make_app(
        tmp_path,
        matcher_config,
        factory=lambda e: _RealPacedClient(),
        shutdown_deadline=0.2,
    )
    run_task = asyncio.create_task(app.run())
    await asyncio.sleep(0.4)  # temps RÉEL > deadline : si la borne enrobait le run, il aurait levé
    assert not run_task.done(), "le run normal (sans signal) ne doit PAS se terminer ni lever"
    app._on_signal()  # arrêt demandé → la borne s'arme, l'arrêt propre est borné
    await asyncio.wait_for(run_task, timeout=5.0)  # se termine sans TimeoutError
    assert run_task.exception() is None


def test_default_client_factory_builds_an_amule_client() -> None:
    from emule_indexer.adapters.mule_ec.client import AmuleEcClient

    endpoint = AmuleEndpoint(name="amule-1", host="gluetun", port=4712, password="secret")
    assert isinstance(default_client_factory(endpoint), AmuleEcClient)


# Fermeture qui traîne BIEN AU-DELÀ de la borne armée (0.05 s), mais BIEN EN DEÇÀ du garde
# externe (5 s) : ainsi seule la borne INTERNE (armée par ``reschedule``) peut couper la
# fermeture. Si ``reschedule`` régressait, l'``aclose`` bloquerait ~1 s puis SORTIRAIT
# proprement (pas de TimeoutError) — le test échouerait alors fail-closed, au lieu de
# « passer » lentement via le garde externe.
_SLOW_CLOSE_SECONDS = 1.0


class _SlowCloseClient(_ShutdownOnStatusClient):
    """Client dont ``close`` traîne au-delà de la borne armée → la borne INTERNE le coupe."""

    async def close(self) -> None:
        await asyncio.sleep(_SLOW_CLOSE_SECONDS)  # > borne armée (0.05 s), < garde externe (5 s)


@pytest.mark.asyncio
async def test_shutdown_deadline_forces_exit(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    # Fermeture qui traîne + délai d'arrêt minuscule → la borne INTERNE (armée par
    # ``reschedule`` à l'arrêt) lève TimeoutError (spec §6 : l'app ne peut PAS paraître bloquée).
    # ROBUSTESSE : on mesure le temps réel écoulé et on exige que la levée vienne VITE — bien
    # sous le garde externe ET sous la durée du close lent — pour prouver que c'est la borne
    # ARMÉE (~0.05 s) qui a tiré, pas le ``wait_for`` externe (5 s) ni la fin du close (1 s).
    # Un régression de ``reschedule`` (borne jamais armée) ferait sortir l'``aclose`` proprement
    # après ~1 s SANS TimeoutError → ``pytest.raises`` échouerait (fail-closed).
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _SlowCloseClient:
        return _SlowCloseClient(app_holder)

    app = _make_app(tmp_path, matcher_config, factory=factory, shutdown_deadline=0.05)
    app_holder["app"] = app
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(app.run(), timeout=5.0)
    elapsed = loop.time() - started
    # < 0.5 s : largement sous le close lent (1 s) et le garde externe (5 s) → c'est bien la
    # borne armée (~0.05 s) qui a coupé la fermeture, pas un autre délai.
    assert elapsed < 0.5, f"la borne armée doit couper vite, écoulé={elapsed:.3f}s"


@pytest.mark.asyncio
async def test_observations_are_catalogued_during_the_cycle(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(app_holder, results=[(observation,)])

    app = _make_app(tmp_path, matcher_config, factory=factory)
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    catalog = sqlite3.connect(tmp_path / "catalog.db")
    try:
        count = catalog.execute("SELECT count(*) FROM match_decisions").fetchone()[0]
    finally:
        catalog.close()
    assert count == 1
