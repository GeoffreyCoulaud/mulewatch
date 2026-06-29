"""Composition root : assemble le pool + repos UNIQUES + moteur + boucle (spec §4/§6).

Couche COMPOSITION (la seule autorisée à importer adapters ET application). Construit :
- UNE ``SqliteCatalogRepository`` + UNE ``SqliteLocalStateRepository`` +
  ``SqliteSchedulerStateRepository`` (writer unique, invariant §11), connexions ouvertes
  via ``open_catalog``/``open_local`` (migrations vérifiées au démarrage, fail-fast §14).
- le ``MatchingEngine`` (une fois), le ``node_id`` (override config ou celui de local.db),
- un ``MuleClient`` + ``SearchWorker`` par instance configurée (pool, spec §3).

Boucle (``_run_loop``) : par cycle, ``run_search_cycle`` puis sommeil (cadence − écoulé).
Arrêt OBSERVABLE & BORNÉ (spec §6) : ``loop.add_signal_handler`` (PAS ``KeyboardInterrupt``,
qui préempterait une fonction sync en pleine écriture) ; 1er ^C → ligne humaine stderr +
annulation du ``TaskGroup`` ; 2e ^C → ``SystemExit`` immédiat ; les ressources longue durée
sont fermées par l'``AsyncExitStack`` APRÈS l'unwind complet du ``TaskGroup`` (plus aucun
travailleur ne peut écrire), le tout sous un délai borné (``shutdown_deadline_seconds``).
"""

import asyncio
import logging
import signal
import sqlite3
import sys
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack, suppress
from pathlib import Path

import httpx
from prometheus_client import CollectorRegistry, start_http_server

from catalog_matching.config import MatcherConfig
from catalog_matching.engine import MatchingEngine
from catalog_matching.models import TargetSegment
from emule_indexer.adapters.config.crawler_config import (
    AmuleEndpoint,
    ConfigError,
    CrawlerConfig,
    DownloadConfig,
)
from emule_indexer.adapters.docker_restart_http import HttpMuleRestarter
from emule_indexer.adapters.gluetun_port import GluetunPortReader
from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.observability.apprise_notifier import AppriseNotifier
from emule_indexer.adapters.observability.dispatcher import ObservabilityDispatcher
from emule_indexer.adapters.observability.prometheus_sink import PrometheusSink
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog, open_local
from emule_indexer.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from emule_indexer.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from emule_indexer.adapters.quarantine_fs import FilesystemQuarantine
from emule_indexer.adapters.verifier_http import HttpContentVerifier
from emule_indexer.application.edge_state import EdgeState
from emule_indexer.application.port_sync_loop import PortSyncLoopDeps, port_sync_loop
from emule_indexer.application.run_download_cycle import (
    DownloadLoopDeps,
    download_loop,
)
from emule_indexer.application.run_search_cycle import run_search_cycle
from emule_indexer.application.run_verification_cycle import VerifyLoopDeps, verification_loop
from emule_indexer.application.search_worker import (
    BackoffRegistry,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from emule_indexer.domain.observability.events import CrawlerStarted
from emule_indexer.ports.clock import Clock, Rng
from emule_indexer.ports.content_verifier import ContentVerifier
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.mule_client import MuleClient, MuleUnreachableError
from emule_indexer.ports.mule_download_client import MuleDownloadClient
from emule_indexer.ports.mule_restarter import MuleRestarter
from emule_indexer.ports.port_forwarding import PortForwardingReader
from emule_indexer.ports.scheduler_state_repository import SchedulerStateRepository
from emule_indexer.ports.telemetry import Telemetry

_logger = logging.getLogger("emule_indexer.composition.app")

# Type de la factory de client (injectable en test pour substituer un FakeMuleClient).
ClientFactory = Callable[[AmuleEndpoint], MuleClient]

# Factory du client de DOWNLOAD : même type d'endpoint, mais le client satisfait
# MuleDownloadClient (AmuleEcClient satisfait les deux Protocols structurellement, DÉCISION D3).
DownloadClientFactory = Callable[[AmuleEndpoint], MuleDownloadClient]
# Factory du verifier : prend l'URL (verifier_url) + le timeout de lecture (s) et rend un
# ContentVerifier.
VerifierFactory = Callable[[str, float], ContentVerifier]


def default_download_client_factory(endpoint: AmuleEndpoint) -> MuleDownloadClient:
    """Un ``AmuleEcClient`` dédié au download (connexion EC distincte, DÉCISION D3)."""
    return AmuleEcClient(endpoint.host, endpoint.port, endpoint.password)


def default_verifier_factory(verifier_url: str, read_timeout_seconds: float) -> ContentVerifier:
    """Un ``HttpContentVerifier`` httpx sur l'URL du verifier.

    ``read_timeout_seconds`` (config) doit couvrir le pire cas d'analyse (clamav) — sinon un
    fichier sain mais lent part en dead-letter (concurrency-async#1). Le ``connect`` reste court
    (10 s) pour détecter vite un verifier mort sans subir le long read sur l'établissement.
    """
    timeout = httpx.Timeout(read_timeout_seconds, connect=10.0)
    client = httpx.AsyncClient(base_url=verifier_url, timeout=timeout)
    return HttpContentVerifier(client)


# Factories du port-sync (injectables en test — pattern verifier_factory). La 1re prend l'URL du
# control-server gluetun, la 2e l'URL du docker-socket-proxy ; chacune rend l'adapter httpx réel.
PortForwardingReaderFactory = Callable[[str], PortForwardingReader]
MuleRestarterFactory = Callable[[str], MuleRestarter]


def default_port_forwarding_reader_factory(gluetun_control_url: str) -> PortForwardingReader:
    """Un ``GluetunPortReader`` httpx sur l'URL du control-server gluetun (timeout court)."""
    client = httpx.AsyncClient(base_url=gluetun_control_url, timeout=httpx.Timeout(10.0))
    return GluetunPortReader(client)


def default_mule_restarter_factory(restarter_url: str) -> MuleRestarter:
    """Un ``HttpMuleRestarter`` httpx sur l'URL du docker-socket-proxy (timeout court)."""
    client = httpx.AsyncClient(base_url=restarter_url, timeout=httpx.Timeout(10.0))
    return HttpMuleRestarter(client)


MetricsServer = Callable[[int, CollectorRegistry], None]


def default_metrics_server(port: int, registry: CollectorRegistry) -> None:
    """Démarre le serveur HTTP /metrics (thread daemon). Wrapper pour fixer l'ordre des args."""
    start_http_server(port, registry=registry)  # pragma: no cover


def _human(message: str) -> None:
    """Ligne humaine d'arrêt sur stderr (spec §6 : progression observable, hors logging)."""
    print(message, file=sys.stderr, flush=True)


def _build_policy(config: CrawlerConfig) -> WorkerPolicy:
    """Déballe la config de politique en primitifs pour l'application (règle de dépendance)."""
    return WorkerPolicy(
        backoff_base_seconds=config.backoff.base_seconds,
        backoff_cap_seconds=config.backoff.cap_seconds,
        backoff_factor=config.backoff.factor,
        backoff_jitter_ratio=config.backoff.jitter_ratio,
        poll_budget_seconds=config.search_poll_budget_seconds,
        poll_interval_seconds=config.search_poll_interval_seconds,
        keyword_pause_min_seconds=config.keyword_pause_min_seconds,
        keyword_pause_max_seconds=config.keyword_pause_max_seconds,
    )


def default_client_factory(endpoint: AmuleEndpoint) -> MuleClient:
    """Un ``AmuleEcClient`` réel par instance (factory par défaut, substituée en test)."""
    return AmuleEcClient(endpoint.host, endpoint.port, endpoint.password)


class CrawlerApp:
    """Assemble et fait tourner le crawler (composition root, spec §4/§6)."""

    def __init__(
        self,
        *,
        crawler_config: CrawlerConfig,
        targets: Sequence[TargetSegment],
        matcher_config: MatcherConfig,
        clock: Clock,
        rng: Rng,
        signal_hub: DecisionSignal,
        client_factory: ClientFactory = default_client_factory,
        download_client_factory: DownloadClientFactory = default_download_client_factory,
        verifier_factory: VerifierFactory = default_verifier_factory,
        port_forwarding_reader_factory: PortForwardingReaderFactory = (
            default_port_forwarding_reader_factory
        ),
        mule_restarter_factory: MuleRestarterFactory = default_mule_restarter_factory,
        metrics_server: MetricsServer = default_metrics_server,
    ) -> None:
        self._crawler_config = crawler_config
        self._targets = tuple(targets)
        self._matcher_config = matcher_config
        self._clock = clock
        self._rng = rng
        self._signal = signal_hub
        self._client_factory = client_factory
        self._download_client_factory = download_client_factory
        self._verifier_factory = verifier_factory
        self._port_forwarding_reader_factory = port_forwarding_reader_factory
        self._mule_restarter_factory = mule_restarter_factory
        self._metrics_server = metrics_server
        self._shutdown = asyncio.Event()
        self._signal_count = 0

    def _on_signal(self) -> None:
        """Handler de boucle (ne préempte jamais une fonction sync, spec §6)."""
        self._signal_count += 1
        if self._signal_count == 1:
            _human(
                "Arrêt demandé — fin des recherches en vol, fermeture propre… "
                "(Ctrl-C à nouveau pour forcer)"
            )
            self._shutdown.set()
        else:
            _human("Arrêt forcé.")
            raise SystemExit(1)

    async def _run_loop(
        self,
        *,
        workers: Sequence[SearchWorker],
        clients: Sequence[MuleClient],
        node_id: str,
        scheduler_state: SchedulerStateRepository,
        backoff: BackoffRegistry,
        telemetry: Telemetry,
        edge: EdgeState,
    ) -> None:
        """Boucle de cycles jusqu'à l'événement d'arrêt (annulée par le ``TaskGroup``)."""
        cycle_index = scheduler_state.read_cycle_index()
        while not self._shutdown.is_set():
            started = self._clock.now()
            await run_search_cycle(
                workers=workers,
                clients=clients,
                targets=self._targets,
                rng=self._rng,
                node_id=node_id,
                cycle_index=cycle_index,
                scheduler_state=scheduler_state,
                backoff=backoff,
                clock=self._clock,
                telemetry=telemetry,
                edge=edge,
            )
            cycle_index += 1
            elapsed = (self._clock.now() - started).total_seconds()
            remaining = max(0.0, self._crawler_config.cycle_interval_seconds - elapsed)
            await self._clock.sleep(remaining)

    def _port_sync_enabled(self) -> bool:
        """Le port-sync s'active SSI la section ``port_sync`` est présente (``enabled: true``).

        Le parseur unifié garantit la complétude de la section quand elle est présente (URLs +
        cadences) — plus de règle « 3 réglages solidaires » à la composition (design simpl.-dépl.).
        """
        return self._crawler_config.port_sync is not None

    async def _build_port_sync_loop(
        self,
        *,
        stack: AsyncExitStack,
        telemetry: Telemetry,
        edge: EdgeState,
    ) -> PortSyncLoopDeps:
        """Assemble les deps de la boucle port-sync (design §9). Suppose la config présente.

        Lecteur gluetun (factory) + restarter (factory), tous deux ``aclose`` poussés sur le stack.
        Connexion EC port-sync DÉDIÉE (R6 : pas de contention avec download/search) vers l'endpoint
        amuled, connectée en TOLÉRANT ``MuleUnreachableError`` au boot (un daemon down ne tue pas le
        crawler ; le backoff de la boucle gouverne). En prod, host = ``gluetun`` (compose) — c'est
        le MÊME endpoint que les autres clients EC.
        """
        port_sync_config = self._crawler_config.port_sync
        assert port_sync_config is not None  # garanti par _port_sync_enabled (mypy : narrow)

        reader = self._port_forwarding_reader_factory(port_sync_config.gluetun_control_url)
        stack.push_async_callback(reader.aclose)  # type: ignore[attr-defined]
        restarter = self._mule_restarter_factory(port_sync_config.restarter_url)
        stack.push_async_callback(restarter.aclose)  # type: ignore[attr-defined]

        # Connexion EC DÉDIÉE au port-sync : on vise le 1er endpoint amuled configuré (host EC en
        # prod = gluetun). Tolère MuleUnreachableError au boot, comme la connexion download.
        endpoint = self._crawler_config.amules[0]
        ec_client = self._client_factory(endpoint)
        stack.push_async_callback(ec_client.close)
        try:
            await ec_client.connect()
        except MuleUnreachableError as error:
            _logger.warning(
                "daemon port-sync injoignable au démarrage (%s) — toléré, retry par la boucle",
                error,
            )
        return PortSyncLoopDeps(
            reader=reader,
            ports=ec_client,  # type: ignore[arg-type]  # AmuleEcClient satisfait PortPreferences
            restarter=restarter,
            clock=self._clock,
            telemetry=telemetry,
            edge=edge,
            poll_interval_seconds=port_sync_config.poll_interval_seconds,
            restart_min_interval_seconds=port_sync_config.restart_min_interval_seconds,
            shutdown=self._shutdown,
        )

    async def _build_full_loops(
        self,
        *,
        download_config: DownloadConfig,
        stack: AsyncExitStack,
        catalog_repo: SqliteCatalogRepository,
        local_repo: SqliteLocalStateRepository,
        local_conn: sqlite3.Connection,
        verifier: ContentVerifier,
        telemetry: Telemetry,
        edge: EdgeState,
    ) -> tuple[DownloadLoopDeps, VerifyLoopDeps]:
        """Assemble les deps des boucles download + vérification (mode full, spec §7).

        Repos UNIQUES partagés (``catalog_repo``/``local_repo`` déjà construits ; un
        ``SqliteDownloadRepository`` sur la MÊME ``local_conn`` — writer unique sur l'event
        loop, aucune course). Une 2e connexion EC (``download_config.endpoint``) connectée en
        tolérant
        ``MuleUnreachableError`` (un daemon down au démarrage ne tue pas le crawler ; le backoff
        de la boucle gouverne). ``staging_dir`` est l'Incoming d'amuled configuré ; le NOM du
        fichier complété vient désormais des fichiers PARTAGÉS EC (le vrai nom on-disk rapporté
        par amuled — résout DV10-Q2 ; la confinement anti-traversal vit dans ``_safe_basename``).
        """
        endpoint = download_config.endpoint
        staging_dir = download_config.staging_dir
        quarantine_dir = download_config.quarantine_dir
        verify_config = download_config.verify

        download_client = self._download_client_factory(endpoint)
        stack.push_async_callback(download_client.close)
        try:
            await download_client.connect()
        except MuleUnreachableError as error:
            _logger.warning(
                "daemon download injoignable au démarrage (%s) — toléré, retry par la boucle",
                error,
            )
        downloads_repo = SqliteDownloadRepository(local_conn)
        quarantine = FilesystemQuarantine(Path(quarantine_dir))
        # ``staging_dir`` = l'Incoming d'amuled ; le NOM du fichier complété vient des fichiers
        # PARTAGÉS EC (DV10-Q2 : ``_promote_completion`` bâtit ``staging_dir / <vrai nom>``).
        download_deps = DownloadLoopDeps(
            client=download_client,
            quarantine=quarantine,
            downloads=downloads_repo,
            catalog=catalog_repo,
            local=local_repo,
            targets=self._targets,
            disk_cap_bytes=download_config.disk_cap_bytes,
            staging_dir=Path(staging_dir),
            clock=self._clock,
            telemetry=telemetry,
            signal=self._signal,
            poll_interval_seconds=download_config.poll_interval_seconds,
            shutdown=self._shutdown,
        )
        verify_deps = VerifyLoopDeps(
            queue=local_repo,
            verifier=verifier,
            writer=catalog_repo,
            targets=downloads_repo,
            poll_interval_seconds=verify_config.poll_interval_seconds,
            clock=self._clock,
            telemetry=telemetry,
            edge=edge,
            shutdown=self._shutdown,
        )
        return download_deps, verify_deps

    async def _supervise(
        self,
        *,
        shutdown_timeout: asyncio.Timeout,
        workers: Sequence[SearchWorker],
        clients: Sequence[MuleClient],
        node_id: str,
        scheduler_state: SchedulerStateRepository,
        backoff: BackoffRegistry,
        download_deps: DownloadLoopDeps | None,
        verify_deps: VerifyLoopDeps | None,
        port_sync_deps: PortSyncLoopDeps | None,
        telemetry: Telemetry,
        edge: EdgeState,
    ) -> None:
        """Lance les boucles, attend l'arrêt (NON borné), ARME la borne, annule TOUT et unwind.

        L'attente du signal d'arrêt est LIBRE (``shutdown_timeout`` entre ici DÉSARMÉ —
        échéance ``None`` — donc le crawler tourne tant qu'on ne l'arrête pas, sur un temps
        non borné). DÈS l'arrêt demandé, on ARME la borne (``reschedule`` à ``maintenant +
        shutdown_deadline_seconds``) AVANT d'annuler : ainsi l'unwind du ``TaskGroup`` (l'``await``
        des tâches annulées à la sortie du ``async with``) PUIS la fermeture LIFO du stack
        (dans ``run``) sont tous deux bornés — l'app ne PEUT pas paraître bloquée à l'arrêt.
        L'annulation atterrit au prochain ``await`` réseau (jamais en pleine écriture DB, repos
        sync, spec §6).
        ARRÊT PROMPT DE TOUTES LES BOUCLES : il faut annuler EXPLICITEMENT chaque tâche sœur —
        annuler ``loop_task`` (search) N'annule PAS les boucles download/verify, qui sont ses
        sœurs dans le ``TaskGroup``. Sans cela, l'arrêt attendrait le sleep in-cycle de chaque
        boucle (``_sleep_or_nudge`` du download ne surveille QUE poll/nudge, pas ``self._shutdown``
        ; le poll de la vérif dort ``verify.poll_interval``), et le ``shutdown_deadline`` armé
        au-dessus tirerait un ``TimeoutError`` AVANT — un Ctrl-C de routine forcerait alors la
        sortie au lieu d'un arrêt propre. On annule donc l'ENSEMBLE des tâches créées.
        VÉRIFICATION EMPIRIQUE : annuler les enfants d'un ``TaskGroup`` (le groupe lui-même
        n'étant pas annulé) NE propage PAS de ``CancelledError`` au sortir du ``async with``
        — l'unwind est PROPRE. On affiche donc la progression APRÈS le bloc, sans ``except*``
        (qui serait du code mort). Une vraie exception d'un travailleur, elle, propagerait en
        ``ExceptionGroup`` — on ne la masque pas.
        """
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(
                    self._run_loop(
                        workers=workers,
                        clients=clients,
                        node_id=node_id,
                        scheduler_state=scheduler_state,
                        backoff=backoff,
                        telemetry=telemetry,
                        edge=edge,
                    )
                )
            ]
            if download_deps is not None:
                tasks.append(group.create_task(download_loop(download_deps)))
            if verify_deps is not None:
                tasks.append(group.create_task(verification_loop(verify_deps)))
            if port_sync_deps is not None:
                tasks.append(group.create_task(port_sync_loop(port_sync_deps)))
            await self._shutdown.wait()  # NON borné (la borne est désarmée tant qu'on tourne)
            shutdown_timeout.reschedule(
                asyncio.get_running_loop().time() + self._crawler_config.shutdown_deadline_seconds
            )
            for task in tasks:
                task.cancel()
        _human("Travailleurs arrêtés.")

    async def run(self) -> None:
        """Point d'entrée async : ouvre les ressources, installe les signaux, boucle (§6).

        Ownership (spec §6) : l'``AsyncExitStack`` possède les ressources longue durée (pool
        de clients + 2 connexions). La borne d'arrêt est un ``asyncio.timeout`` ENTRÉ DÉSARMÉ
        (échéance ``None``) : le run en régime permanent (attente du signal, cycles) est NON
        borné — sinon le crawler mourrait après ``shutdown_deadline_seconds`` de marche normale.
        SEULE la PHASE D'ARRÊT est bornée : ``_supervise`` ARME la borne (``reschedule``) dès
        l'arrêt demandé, donc l'unwind du ``TaskGroup`` PUIS la fermeture LIFO du stack ci-dessous
        tombent sous l'échéance — l'app ne PEUT pas paraître bloquée à l'arrêt. Un dépassement
        lève ``TimeoutError`` (sortie forcée) ; le ``finally`` tente alors une fermeture
        best-effort (suppress) pour ne pas re-bloquer indéfiniment. La borne ne s'arme JAMAIS
        sans arrêt demandé → un ``TimeoutError`` ne peut frapper qu'une fermeture qui traîne.
        """
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self._on_signal)
        loop.add_signal_handler(signal.SIGTERM, self._on_signal)
        stack = AsyncExitStack()
        try:
            catalog_conn = open_catalog(self._crawler_config.catalog_db_path)
            stack.callback(catalog_conn.close)
            local_conn = open_local(self._crawler_config.local_db_path)
            stack.callback(local_conn.close)

            local_repo = SqliteLocalStateRepository(local_conn)
            node_id = self._crawler_config.node_id or local_repo.node_id()
            obs = self._crawler_config.observability
            notifications = obs.notifications if obs is not None else ()
            registry = CollectorRegistry()
            notifier = AppriseNotifier(
                tuple((target.url, target.tag) for target in notifications),
                node_id=node_id,
            )
            telemetry = ObservabilityDispatcher(
                metrics=PrometheusSink(registry),
                notifier=notifier,
                notify_timeout_seconds=(
                    obs.notification_timeout_seconds if obs is not None else 5.0
                ),
            )
            edge = EdgeState()
            if obs is not None and obs.metrics is not None and obs.metrics.enabled:
                self._metrics_server(obs.metrics.port, registry)
            catalog_repo = SqliteCatalogRepository(catalog_conn, node_id)
            scheduler_state = SqliteSchedulerStateRepository(local_conn)
            engine = MatchingEngine(self._matcher_config, self._targets)
            # Registre de backoff PARTAGÉ : construit UNE fois, RECHARGÉ depuis scheduler_state
            # (le backoff survit au redémarrage, spec §3/§7), injecté dans TOUS les travailleurs
            # + passé au cycle qui le persiste. Writer unique sur l'event loop → aucune course.
            policy = _build_policy(self._crawler_config)
            backoff = BackoffRegistry(policy, self._clock, self._rng)
            backoff.load_from(scheduler_state.load_channel_backoff())
            deps = WorkerDeps(
                catalog=catalog_repo,
                engine=engine,
                signal=self._signal,
                clock=self._clock,
                rng=self._rng,
                policy=policy,
                backoff=backoff,
                telemetry=telemetry,
            )

            clients: list[MuleClient] = []
            workers: list[SearchWorker] = []
            for endpoint in self._crawler_config.amules:
                client = self._client_factory(endpoint)
                stack.push_async_callback(client.close)
                # CONNECTE au montage du pool, AVANT le 1er relevé de coverage (sinon
                # _aggregate_coverage frappe un client non connecté et lève). Un daemon down au
                # démarrage ne doit PAS faire tomber un crawler multi-instances : on TOLÈRE le
                # MuleUnreachableError (warning nommant l'instance) et on CONTINUE — le backoff
                # de reconnexion du travailleur gouvernera les retentatives. connect() est
                # idempotent → le _ensure_connected() ultérieur du travailleur reste un no-op.
                # On NE catch PAS plus large : EcAuthError (mot de passe faux) n'est PAS un
                # MuleUnreachableError → il continue de propager (fail-fast config, spec §14).
                try:
                    await client.connect()
                except MuleUnreachableError as error:
                    _logger.warning(
                        "instance %s injoignable au démarrage (%s) — tolérée, backoff au cycle",
                        endpoint.name,
                        error,
                    )
                clients.append(client)
                workers.append(SearchWorker(endpoint.name, client, deps))

            _logger.info("crawler démarré : %d instance(s), node_id=%s", len(clients), node_id)

            verifier: ContentVerifier | None = None
            download_deps: DownloadLoopDeps | None = None
            verify_deps: VerifyLoopDeps | None = None
            # Mode FULL ⟺ la section ``download`` est présente (``enabled: true``). Le parseur
            # unifié garantit alors la complétude du câblage (endpoint/dirs/verifier_url/verify) —
            # plus de gate ``_require_full_config`` à la composition.
            download_config = self._crawler_config.download
            if download_config is not None:
                verifier = self._verifier_factory(
                    download_config.verifier_url, download_config.verify.client_timeout_seconds
                )
                # Ferme le client verifier au teardown. Le port ``ContentVerifier`` ne déclare
                # PAS ``aclose`` (détail d'adapter http) → ``# type: ignore`` documenté ; toute
                # impl passée à la composition (HttpContentVerifier, faux de test) l'expose
                # (DÉCISION DV16 : pas de getattr/branche → pas de branche partielle à couvrir).
                stack.push_async_callback(verifier.aclose)  # type: ignore[attr-defined]
                if not await verifier.health():
                    raise ConfigError(
                        "verifier injoignable au démarrage (health-check KO) — "
                        "refus de démarrer en mode full"
                    )
                download_deps, verify_deps = await self._build_full_loops(
                    download_config=download_config,
                    stack=stack,
                    catalog_repo=catalog_repo,
                    local_repo=local_repo,
                    local_conn=local_conn,
                    verifier=verifier,
                    telemetry=telemetry,
                    edge=edge,
                )
                _logger.info("mode full : boucles download + vérification armées")

            # Port-sync (High-ID) : INDÉPENDANT du mode observer/full (déclencheur propre = section
            # ``port_sync`` présente avec ``enabled: true`` ; complétude garantie par le parseur).
            port_sync_deps: PortSyncLoopDeps | None = None
            if self._port_sync_enabled():
                port_sync_deps = await self._build_port_sync_loop(
                    stack=stack, telemetry=telemetry, edge=edge
                )
                _logger.info("port-sync (High-ID) armé")

            mode = "full" if download_config is not None else "observer"
            await telemetry.emit(CrawlerStarted(mode=mode))

            # Borne ENTRÉE DÉSARMÉE (None) : le régime permanent est non borné ; ``_supervise``
            # l'arme (reschedule) dès l'arrêt demandé → seule la phase d'arrêt + l'aclose ci-dessous
            # sont bornés. (Vérifié empiriquement : timeout(None) ne tire pas ; reschedule de
            # l'intérieur arme ; une op lente après arme lève TimeoutError, une rapide non.)
            async with asyncio.timeout(None) as shutdown_timeout:
                await self._supervise(
                    shutdown_timeout=shutdown_timeout,
                    workers=workers,
                    clients=clients,
                    node_id=node_id,
                    scheduler_state=scheduler_state,
                    backoff=backoff,
                    download_deps=download_deps,
                    verify_deps=verify_deps,
                    port_sync_deps=port_sync_deps,
                    telemetry=telemetry,
                    edge=edge,
                )
                _human(f"{len(clients)} connexion(s) EC en fermeture…")
                await stack.aclose()
                _human("Bases fermées — sortie.")
        finally:
            # Best-effort si l'arrêt borné a échoué (TimeoutError) ou si le setup a levé :
            # ferme ce qui reste SANS jamais re-bloquer (suppress de toute panne/annulation).
            with suppress(BaseException):
                await stack.aclose()
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
