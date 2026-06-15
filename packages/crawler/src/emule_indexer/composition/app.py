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
from prometheus_client import CollectorRegistry

from emule_indexer.adapters.config.crawler_config import ConfigError, CrawlerConfig
from emule_indexer.adapters.config.local_config import AmuleEndpoint, LocalConfig
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
from emule_indexer.application.run_download_cycle import (
    CatalogReader,
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
from emule_indexer.domain.matching.config import MatcherConfig
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.ports.clock import Clock, Rng
from emule_indexer.ports.content_verifier import ContentVerifier
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.mule_client import MuleClient, MuleUnreachableError
from emule_indexer.ports.mule_download_client import DownloadEntry, MuleDownloadClient
from emule_indexer.ports.scheduler_state_repository import SchedulerStateRepository
from emule_indexer.ports.telemetry import Telemetry

_logger = logging.getLogger("emule_indexer.composition.app")

# Type de la factory de client (injectable en test pour substituer un FakeMuleClient).
ClientFactory = Callable[[AmuleEndpoint], MuleClient]

# Factory du client de DOWNLOAD : même type d'endpoint, mais le client satisfait
# MuleDownloadClient (AmuleEcClient satisfait les deux Protocols structurellement, DÉCISION D3).
DownloadClientFactory = Callable[[AmuleEndpoint], MuleDownloadClient]
# Factory du verifier : prend l'URL (verifier_url) et rend un ContentVerifier.
VerifierFactory = Callable[[str], ContentVerifier]


def default_download_client_factory(endpoint: AmuleEndpoint) -> MuleDownloadClient:
    """Un ``AmuleEcClient`` dédié au download (connexion EC distincte, DÉCISION D3)."""
    return AmuleEcClient(endpoint.host, endpoint.port, endpoint.password)


def default_verifier_factory(verifier_url: str) -> ContentVerifier:
    """Un ``HttpContentVerifier`` httpx sur l'URL du verifier (timeout dev raisonnable)."""
    client = httpx.AsyncClient(base_url=verifier_url, timeout=httpx.Timeout(10.0))
    return HttpContentVerifier(client)


def resolve_staging_path(staging_base: Path, catalog: CatalogReader, entry: DownloadEntry) -> Path:
    """Chemin du fichier complété en staging pour une entrée de file (DÉCISION DV10).

    Dérive le nom du fichier de la DERNIÈRE observation du hash (le vrai layout amuled est
    PENDING-homelab) ; si aucune observation n'a survécu, retombe sur ``staging_base/<hash>``
    (best-effort : ce chemin échouera simplement à ``os.replace`` → ``_promote_completion``
    laisse ``completed`` et retente, JAMAIS de crash). ``catalog`` est typé au Protocol narrow
    ``CatalogReader`` (``application.run_download_cycle``) → ``SqliteCatalogRepository`` ET le
    faux de test le satisfont.

    Ce chemin est la SOURCE d'un ``os.replace`` dans ``quarantine.promote`` : il DOIT rester
    confiné à ``staging_base`` (la destination, par hash, l'est déjà côté ``quarantine_fs``).
    """
    observation = catalog.last_observation(entry.ed2k_hash)
    if observation is None:
        return staging_base / entry.ed2k_hash
    # filename = input HOSTILE (CLAUDE.md : « filenames are hostile input ») : confiner au
    # basename pour que la SOURCE de ``os.replace`` ne puisse JAMAIS sortir de ``staging_base``
    # (anti-traversal — ``staging_base / "/etc/passwd"`` rendrait ``/etc/passwd``, et
    # ``staging_base / "../../etc/passwd"`` échapperait). ATTENTION : ``Path.name`` ne suffit
    # PAS seul — ``Path("..").name == ".."`` (pas ``""`` !), donc ``staging_base / ".."``
    # remonterait d'un cran. On rejette donc EXPLICITEMENT les noms dégénérés ``""``/``.``/``..``
    # → fallback sur le hash (confiné, échouera proprement à ``os.replace``, retry idempotent).
    safe_name = Path(observation.filename).name
    if safe_name in {"", ".", ".."}:
        return staging_base / entry.ed2k_hash
    return staging_base / safe_name


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
        local_config: LocalConfig,
        targets: Sequence[TargetSegment],
        matcher_config: MatcherConfig,
        clock: Clock,
        rng: Rng,
        signal_hub: DecisionSignal,
        client_factory: ClientFactory = default_client_factory,
        download_client_factory: DownloadClientFactory = default_download_client_factory,
        verifier_factory: VerifierFactory = default_verifier_factory,
    ) -> None:
        self._crawler_config = crawler_config
        self._local_config = local_config
        self._targets = tuple(targets)
        self._matcher_config = matcher_config
        self._clock = clock
        self._rng = rng
        self._signal = signal_hub
        self._client_factory = client_factory
        self._download_client_factory = download_client_factory
        self._verifier_factory = verifier_factory
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

    def _require_full_config(self) -> None:
        """Fail-fast au montage si le mode full est déclenché sans config complète (DV15).

        ``verifier_url`` est le DÉCLENCHEUR du mode full ; l'ensemble download DOIT suivre
        (handoff §3.5 — la lacune unidirectionnelle du parser : des dirs sans endpoint sont
        ignorés, mais un crawler full SANS endpoint/dirs/section download/verify ne doit PAS
        démarrer : on ne télécharge jamais sans pouvoir vérifier ni sans staging/quarantaine).
        """
        missing: list[str] = []
        if self._crawler_config.verify is None:
            missing.append("crawler.verify")
        if self._crawler_config.download is None:
            missing.append("crawler.download")
        if self._local_config.download_endpoint is None:
            missing.append("local.download_endpoint")
        if self._local_config.staging_dir is None:
            missing.append("local.staging_dir")
        if self._local_config.quarantine_dir is None:
            missing.append("local.quarantine_dir")
        if missing:
            raise ConfigError(
                "mode full (verifier_url défini) exige aussi : "
                + ", ".join(missing)
                + " (refus de télécharger sans config complète)"
            )

    async def _build_full_loops(
        self,
        *,
        stack: AsyncExitStack,
        catalog_repo: SqliteCatalogRepository,
        local_repo: SqliteLocalStateRepository,
        local_conn: sqlite3.Connection,
        verifier: ContentVerifier,
    ) -> tuple[DownloadLoopDeps, VerifyLoopDeps]:
        """Assemble les deps des boucles download + vérification (mode full, spec §7).

        Repos UNIQUES partagés (``catalog_repo``/``local_repo`` déjà construits ; un
        ``SqliteDownloadRepository`` sur la MÊME ``local_conn`` — writer unique sur l'event
        loop, aucune course). Une 2e connexion EC (``download_endpoint``) connectée en tolérant
        ``MuleUnreachableError`` (un daemon down au démarrage ne tue pas le crawler ; le backoff
        de la boucle gouverne). ``staging_path_for`` dérive le chemin du fichier complété du
        ``staging_dir`` configuré + le filename de la dernière observation (DÉCISION DV10 ;
        ``None`` → chemin best-effort qui échouera à ``os.replace``, laissant ``completed``).
        """
        endpoint = self._local_config.download_endpoint
        assert endpoint is not None  # garanti par _require_full_config (mypy : narrow)
        staging_dir = self._local_config.staging_dir
        quarantine_dir = self._local_config.quarantine_dir
        assert staging_dir is not None and quarantine_dir is not None
        download_config = self._crawler_config.download
        verify_config = self._crawler_config.verify
        assert download_config is not None and verify_config is not None

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
        staging_base = Path(staging_dir)
        # ``resolve_staging_path`` est une fonction MODULE-LEVEL (unit-testée à 100 % branch,
        # test_staging_resolver.py) — le lambda ne fait que la lier au staging + catalogue
        # (DÉCISION DV10 ; observation None → chemin sous staging par hash, best-effort).
        download_deps = DownloadLoopDeps(
            client=download_client,
            quarantine=quarantine,
            downloads=downloads_repo,
            catalog=catalog_repo,
            local=local_repo,
            targets=self._targets,
            disk_cap_bytes=download_config.disk_cap_bytes,
            staging_path_for=lambda entry: resolve_staging_path(staging_base, catalog_repo, entry),
            clock=self._clock,
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
            catalog_conn = open_catalog(self._local_config.catalog_db_path)
            stack.callback(catalog_conn.close)
            local_conn = open_local(self._local_config.local_db_path)
            stack.callback(local_conn.close)

            local_repo = SqliteLocalStateRepository(local_conn)
            node_id = self._local_config.node_id or local_repo.node_id()
            obs = self._crawler_config.observability
            registry = CollectorRegistry()
            notifier = AppriseNotifier(
                tuple((target.url, target.tag) for target in self._local_config.notifications),
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
            for endpoint in self._local_config.amules:
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
            if self._local_config.verifier_url is not None:
                self._require_full_config()
                verifier = self._verifier_factory(self._local_config.verifier_url)
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
                    stack=stack,
                    catalog_repo=catalog_repo,
                    local_repo=local_repo,
                    local_conn=local_conn,
                    verifier=verifier,
                )
                _logger.info("mode full : boucles download + vérification armées")

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
