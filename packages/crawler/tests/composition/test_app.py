import asyncio
import datetime
import logging
import sqlite3
from pathlib import Path

import pytest

from emule_indexer.adapters.config.crawler_config import (
    BackoffConfig,
    ConfigError,
    CrawlerConfig,
    DownloadConfig,
    VerifyConfig,
)
from emule_indexer.adapters.config.local_config import AmuleEndpoint, LocalConfig
from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.composition.app import CrawlerApp, default_client_factory
from emule_indexer.domain.matching.config import MatcherConfig
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.matching.validation import parse_matcher_config
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.content_verifier import VerificationResult
from emule_indexer.ports.mule_client import MuleUnreachableError, NetworkStatus
from emule_indexer.ports.mule_download_client import DownloadEntry
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


def test_default_download_client_factory_builds_an_amule_client() -> None:
    from emule_indexer.adapters.mule_ec.client import AmuleEcClient
    from emule_indexer.composition.app import default_download_client_factory

    endpoint = AmuleEndpoint(name="dl", host="gluetun", port=4799, password="secret")
    assert isinstance(default_download_client_factory(endpoint), AmuleEcClient)


def test_default_verifier_factory_builds_an_http_verifier() -> None:
    from emule_indexer.adapters.verifier_http import HttpContentVerifier
    from emule_indexer.composition.app import default_verifier_factory

    verifier = default_verifier_factory("http://verifier:8000")
    assert isinstance(verifier, HttpContentVerifier)


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


# ---------------------------------------------------------------------------
# Task 11 — mode full (verifier_url) : gate + câblage des 2 boucles
# ---------------------------------------------------------------------------


class FakeContentVerifier:
    """ContentVerifier de test : santé scriptable, verdict NO-OP."""

    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy
        self.closed = False

    async def verify(self, ed2k_hash: str, expected: object) -> VerificationResult:
        return VerificationResult(verdict="unverified", real_meta={}, checks=())

    async def health(self) -> bool:
        return self._healthy

    async def aclose(self) -> None:
        self.closed = True


class FakeDownloadClient(FakeMuleClient):
    """Client download de test : satisfait aussi add_link/download_queue (no-op).

    ``queue_calls`` compte les relevés de la file : preuve qu'un cycle de la boucle de download
    s'est BIEN exécuté (``download_queue`` est l'unique ``await`` réseau d'un cycle vide)."""

    def __init__(self) -> None:
        super().__init__()
        self.queue_calls = 0

    async def add_link(self, ed2k_link: str) -> None:
        return None

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        self.queue_calls += 1
        return ()


class _ShutdownOnQueueDownloadClient(FakeDownloadClient):
    """Client download qui déclenche l'arrêt au PREMIER ``download_queue`` (1 cycle puis stop).

    Borne le run de façon DÉTERMINISTE sur la boucle de DOWNLOAD elle-même : l'arrêt n'est posé
    QUE lorsque la boucle de download a exécuté un cycle (relevé de la file) → prouve que le
    corps de la boucle a tourné (pas seulement que la tâche a été créée), sans course de timing
    ni ``sleep`` réel. Le compteur ``queue_calls`` reste lisible après coup."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__()
        self._app_holder = app_holder

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        result = await super().download_queue()
        self._app_holder["app"]._on_signal()  # arrêt APRÈS le 1er cycle de download
        return result


class _UnreachableDownloadClient(FakeDownloadClient):
    """Client download dont ``connect`` lève ``MuleUnreachableError`` (daemon down au démarrage)."""

    async def connect(self) -> None:
        raise MuleUnreachableError("download daemon down")


def _full_local_config(tmp_path: Path, *, verifier_url: str | None) -> LocalConfig:
    base = _local_config(tmp_path)
    staging = tmp_path / "staging"
    quarantine = tmp_path / "quarantine"
    staging.mkdir(exist_ok=True)
    quarantine.mkdir(exist_ok=True)
    return LocalConfig(
        amules=base.amules,
        catalog_db_path=base.catalog_db_path,
        local_db_path=base.local_db_path,
        node_id=base.node_id,
        download_endpoint=AmuleEndpoint(name="dl", host="h", port=4799, password="p"),
        staging_dir=str(staging),
        quarantine_dir=str(quarantine),
        verifier_url=verifier_url,
    )


def _full_crawler_config() -> CrawlerConfig:
    base = _crawler_config()
    return CrawlerConfig(
        cycle_interval_seconds=base.cycle_interval_seconds,
        search_poll_budget_seconds=base.search_poll_budget_seconds,
        search_poll_interval_seconds=base.search_poll_interval_seconds,
        keyword_pause_min_seconds=base.keyword_pause_min_seconds,
        keyword_pause_max_seconds=base.keyword_pause_max_seconds,
        backoff=base.backoff,
        decision_poll_interval_seconds=base.decision_poll_interval_seconds,
        shutdown_deadline_seconds=base.shutdown_deadline_seconds,
        download=DownloadConfig(poll_interval_seconds=30.0, disk_cap_bytes=1_000_000_000),
        verify=VerifyConfig(poll_interval_seconds=10.0),
    )


@pytest.mark.asyncio
async def test_observer_mode_runs_without_download_or_verify_loops(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # verifier_url absent → observateur : démarre, tourne un cycle, s'arrête ; aucun verifier
    # construit, aucune boucle download/verif. (Comportement Plan C inchangé.)
    holder: dict[str, CrawlerApp] = {}
    verifier = FakeContentVerifier()

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_crawler_config(),
        local_config=_local_config(tmp_path),  # pas de verifier_url
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=factory,
        verifier_factory=lambda url: verifier,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert verifier.closed is False  # observateur : le verifier n'est jamais utilisé/fermé


@pytest.mark.asyncio
async def test_full_mode_health_ok_runs_both_loops(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # L'arrêt est piloté de façon DÉTERMINISTE par la boucle de DOWNLOAD elle-même (le client
    # download fire l'arrêt à son 1er relevé de file) → on prouve que le CORPS de la boucle
    # download a tourné (pas seulement que la tâche a été créée), sans course de timing. Le
    # corps de la boucle de VÉRIFICATION est couvert par ses tests unitaires (Task 9) ; ICI on
    # couvre le CÂBLAGE (sa tâche est créée dans le TaskGroup) + le health-check + le teardown.
    holder: dict[str, CrawlerApp] = {}
    verifier = FakeContentVerifier(healthy=True)
    download_client = _ShutdownOnQueueDownloadClient(holder)

    def search_factory(endpoint: AmuleEndpoint) -> FakeMuleClient:
        return FakeMuleClient()  # ne pilote PAS l'arrêt : c'est la boucle download qui l'arme

    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=_full_local_config(tmp_path, verifier_url="http://verifier:8000"),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=search_factory,
        download_client_factory=lambda endpoint: download_client,
        verifier_factory=lambda url: verifier,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    # full : le verifier a été health-checké et fermé proprement à l'arrêt.
    assert verifier.closed is True
    # la boucle de download a exécuté ≥ 1 cycle (corps tourné, pas juste la tâche créée).
    assert download_client.queue_calls >= 1


@pytest.mark.asyncio
async def test_full_mode_health_failure_is_fail_fast(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    verifier = FakeContentVerifier(healthy=False)  # health() → False → fail-fast

    def search_factory(endpoint: AmuleEndpoint) -> FakeMuleClient:
        return FakeMuleClient()

    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=_full_local_config(tmp_path, verifier_url="http://verifier:8000"),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=search_factory,
        download_client_factory=lambda endpoint: FakeDownloadClient(),
        verifier_factory=lambda url: verifier,
    )
    with pytest.raises(ConfigError, match="verifier"):
        await app.run()
    assert verifier.closed is True  # le client verifier est fermé même en fail-fast


@pytest.mark.asyncio
async def test_full_mode_missing_download_config_is_fail_fast(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # verifier_url présent MAIS l'ensemble download incomplet (download_endpoint absent) →
    # fail-fast au montage (handoff §3.5 : on ne télécharge jamais sans la config complète).
    local = _local_config(tmp_path)
    local = LocalConfig(
        amules=local.amules,
        catalog_db_path=local.catalog_db_path,
        local_db_path=local.local_db_path,
        node_id=local.node_id,
        verifier_url="http://verifier:8000",  # full déclenché, mais pas d'endpoint/dirs
    )
    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=local,
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=lambda endpoint: FakeMuleClient(),
        verifier_factory=lambda url: FakeContentVerifier(),
    )
    with pytest.raises(ConfigError, match="download"):
        await app.run()


@pytest.mark.asyncio
async def test_full_mode_missing_crawler_verify_and_download_sections_is_fail_fast(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # verifier_url présent MAIS crawler_config sans sections verify ET download →
    # fail-fast : couvre les branches verify is None et download is None de _require_full_config.
    local = _local_config(tmp_path)
    local = LocalConfig(
        amules=local.amules,
        catalog_db_path=local.catalog_db_path,
        local_db_path=local.local_db_path,
        node_id=local.node_id,
        verifier_url="http://verifier:8000",
    )
    crawler = _crawler_config()  # download=None, verify=None
    app = CrawlerApp(
        crawler_config=crawler,
        local_config=local,
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=lambda endpoint: FakeMuleClient(),
        verifier_factory=lambda url: FakeContentVerifier(),
    )
    with pytest.raises(ConfigError, match="verify"):
        await app.run()


@pytest.mark.asyncio
async def test_full_mode_tolerates_download_daemon_unreachable_at_startup(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # le daemon download injoignable au démarrage est TOLÉRÉ (handoff / DV8) : on n'échoue
    # PAS, les boucles sont quand même armées (le backoff de la boucle gouverne les retries).
    holder: dict[str, CrawlerApp] = {}
    verifier = FakeContentVerifier(healthy=True)

    def search_factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    def download_factory(endpoint: AmuleEndpoint) -> _UnreachableDownloadClient:
        return _UnreachableDownloadClient()

    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=_full_local_config(tmp_path, verifier_url="http://verifier:8000"),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=search_factory,
        download_client_factory=download_factory,
        verifier_factory=lambda url: verifier,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)  # ne lève pas : connect toléré
    assert verifier.closed is True  # full a démarré (boucles armées), verifier fermé à l'arrêt


class _BlockingPollClock(FakeClock):
    """Horloge dont les LONGS sleeps (≥ 5 s) BLOQUENT pour de bon (sur un Event jamais positionné).

    Modélise le sleep IN-CYCLE des boucles : ``download._sleep_or_nudge`` (poll 30 s) et le poll
    de la vérif (10 s) restent BLOQUÉS dans ``clock.sleep`` — donc ces boucles ne peuvent PAS
    re-tester ``self._shutdown`` d'elles-mêmes ; seule une ANNULATION explicite par ``_supervise``
    les en sort. Une BARRIÈRE : dès que les DEUX boucles (download 30 s + vérif 10 s) sont entrées
    dans un long sleep, on ARME ``self._shutdown`` — l'arrêt est donc demandé pendant qu'elles
    sont bloquées. Si ``_supervise`` ne les annulait PAS, le ``TaskGroup`` attendrait à jamais et
    le ``shutdown_deadline`` armé tirerait un ``TimeoutError`` (force-exit) : le test échouerait
    fail-closed. Les sleeps COURTS (pauses inter-mots-clés du search) rendent la main tout de suite
    (déterminisme, pas de temps réel). Le sleep inter-cycle du search (≥ 5 s) bloque aussi → il est
    sorti par l'annulation de ``loop_task`` (déjà en place)."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__()
        self._app_holder = app_holder
        self._blocked_long_polls: set[float] = set()
        self._never = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        if seconds < 5.0:
            await super().sleep(seconds)  # pause courte : rend la main (instantané)
            return
        # Long sleep (poll in-cycle d'une boucle, ou inter-cycle du search) : on note la cadence
        # et, dès que les DEUX polls des nouvelles boucles (30 s download + 10 s vérif) sont
        # bloqués, on demande l'arrêt PENDANT qu'elles dorment, puis on BLOQUE pour de bon.
        self._blocked_long_polls.add(seconds)
        if {10.0, 30.0} <= self._blocked_long_polls:
            self._app_holder["app"]._shutdown.set()
        await self._never.wait()  # ne se résout JAMAIS : sortie uniquement par annulation


@pytest.mark.asyncio
async def test_full_mode_shutdown_cancels_download_and_verify_loops_promptly(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # RÉGRESSION (revue holistique) : à l'arrêt, ``_supervise`` doit annuler EXPLICITEMENT les
    # boucles download/verify (tâches sœurs du search ``loop_task``). Sans cela, elles restent
    # bloquées dans leur sleep in-cycle (``_sleep_or_nudge`` ne surveille PAS ``self._shutdown``),
    # le ``TaskGroup`` attend leur poll (30 s/10 s), le ``shutdown_deadline`` tire AVANT un
    # ``TimeoutError`` et l'arrêt est FORCÉ — pas propre. Ici : un clock dont les longs sleeps
    # BLOQUENT, qui ARME l'arrêt une fois les deux boucles bloquées dans leur poll. Si l'annulation
    # est faite, ``run()`` RETOURNE promptement (sans atteindre le deadline) ; sinon il
    # ``TimeoutError``-erait (deadline) ou bloquerait jusqu'au garde externe → échec fail-closed.
    holder: dict[str, CrawlerApp] = {}
    verifier = FakeContentVerifier(healthy=True)
    clock = _BlockingPollClock(holder)

    # _full_crawler_config : download poll 30 s, verify poll 10 s, shutdown_deadline 30 s.
    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=_full_local_config(tmp_path, verifier_url="http://verifier:8000"),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=clock,
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=lambda endpoint: FakeMuleClient(),
        download_client_factory=lambda endpoint: FakeDownloadClient(),
        verifier_factory=lambda url: verifier,
    )
    holder["app"] = app
    # Le garde externe (3 s de temps RÉEL) est BIEN sous le shutdown_deadline (30 s) ET sous les
    # polls (10 s/30 s) : il ne peut tirer que si l'arrêt N'est PAS prompt. Avec l'annulation, le
    # run revient en quelques ticks d'event loop (aucun temps réel n'est consommé).
    await asyncio.wait_for(app.run(), timeout=3.0)
    assert verifier.closed is True  # arrêt propre : le teardown a bien fermé le verifier
