"""Composition root: assembles the pool + UNIQUE repos + engine + loop (spec §4/§6).

COMPOSITION layer (the only one allowed to import adapters AND application). Builds:
- ONE ``SqliteCatalogRepository`` + ONE ``SqliteLocalStateRepository`` +
  ``SqliteSchedulerStateRepository`` (single writer, invariant §11), connections opened
  via ``open_catalog``/``open_local`` (migrations checked at startup, fail-fast §14).
- the ``MatchingEngine`` (once), the ``node_id`` (config override or the one from local.db),
- one ``MuleClient`` + ``SearchWorker`` per configured instance (pool, spec §3).

Loop (``_run_loop``): per cycle, ``run_search_cycle`` then sleep (cadence − elapsed).
OBSERVABLE & BOUNDED shutdown (spec §6): ``loop.add_signal_handler`` (NOT ``KeyboardInterrupt``,
which would preempt a sync function mid-write); 1st ^C → human line on stderr +
cancellation of the ``TaskGroup``; 2nd ^C → immediate ``SystemExit``; long-lived resources
are closed by the ``AsyncExitStack`` AFTER the full unwind of the ``TaskGroup`` (no worker
can write anymore), all within a bounded delay (``shutdown_deadline_seconds``).
"""

import asyncio
import logging
import signal
import sqlite3
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Protocol

import httpx
import uvicorn
from prometheus_client import CollectorRegistry, start_http_server
from starlette.applications import Starlette

import mulewatch.webui
from catalog_matching.config import MatcherConfig
from catalog_matching.engine import MatchingEngine
from catalog_matching.models import TargetSegment
from mulewatch.adapters.config.crawler_config import (
    AmuleEndpoint,
    ConfigError,
    CrawlerConfig,
    DownloadConfig,
)
from mulewatch.adapters.docker_restart_http import HttpMuleRestarter
from mulewatch.adapters.gluetun_port import GluetunPortReader
from mulewatch.adapters.mule_ec.client import AmuleEcClient
from mulewatch.adapters.observability.apprise_notifier import AppriseNotifier
from mulewatch.adapters.observability.dispatcher import ObservabilityDispatcher
from mulewatch.adapters.observability.prometheus_sink import PrometheusSink
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.adapters.persistence_sqlite.connection import open_catalog, open_local
from mulewatch.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from mulewatch.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from mulewatch.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from mulewatch.adapters.quarantine_fs import FilesystemQuarantine
from mulewatch.adapters.verifier_http import HttpContentVerifier
from mulewatch.application.edge_state import EdgeState
from mulewatch.application.port_sync_loop import PortSyncLoopDeps, port_sync_loop
from mulewatch.application.reevaluate_catalog import reevaluate_catalog
from mulewatch.application.run_backfill import run_backfill_if_policy_changed
from mulewatch.application.run_download_cycle import (
    DownloadLoopDeps,
    download_loop,
)
from mulewatch.application.run_search_cycle import run_search_cycle
from mulewatch.application.run_verification_cycle import VerifyLoopDeps, verification_loop
from mulewatch.application.search_worker import (
    BackoffRegistry,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from mulewatch.domain.observability.events import CrawlerStarted
from mulewatch.ports.clock import Clock, Rng
from mulewatch.ports.content_verifier import ContentVerifier
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.mule_client import MuleClient, MuleUnreachableError
from mulewatch.ports.mule_download_client import MuleDownloadClient
from mulewatch.ports.mule_restarter import MuleRestarter
from mulewatch.ports.port_forwarding import PortForwardingReader
from mulewatch.ports.scheduler_state_repository import SchedulerStateRepository
from mulewatch.ports.telemetry import Telemetry
from mulewatch.webui.composition.app import build_app as build_webui_app

_logger = logging.getLogger("mulewatch.composition.app")

# Type of the client factory (injectable in test to substitute a FakeMuleClient).
ClientFactory = Callable[[AmuleEndpoint], MuleClient]

# DOWNLOAD client factory: same endpoint type, but the client satisfies
# MuleDownloadClient (AmuleEcClient satisfies both Protocols structurally, DECISION D3).
DownloadClientFactory = Callable[[AmuleEndpoint], MuleDownloadClient]
# Verifier factory: takes the URL (verifier_url) + the read timeout (s) and returns a
# ContentVerifier.
VerifierFactory = Callable[[str, float], ContentVerifier]


def default_download_client_factory(endpoint: AmuleEndpoint) -> MuleDownloadClient:
    """An ``AmuleEcClient`` dedicated to download (distinct EC connection, DECISION D3)."""
    return AmuleEcClient(endpoint.host, endpoint.port, endpoint.password)


def default_verifier_factory(verifier_url: str, read_timeout_seconds: float) -> ContentVerifier:
    """An httpx ``HttpContentVerifier`` on the verifier's URL.

    ``read_timeout_seconds`` (config) must cover the worst-case analysis (clamav) — otherwise a
    healthy but slow file goes to dead-letter (concurrency-async#1). The ``connect`` stays short
    (10 s) to quickly detect a dead verifier without incurring the long read on establishment.
    """
    timeout = httpx.Timeout(read_timeout_seconds, connect=10.0)
    client = httpx.AsyncClient(base_url=verifier_url, timeout=timeout)
    return HttpContentVerifier(client)


# Port-sync factories (injectable in test — verifier_factory pattern). The 1st takes the URL of
# the gluetun control-server, the 2nd the docker-socket-proxy URL; each returns the real httpx
# adapter.
PortForwardingReaderFactory = Callable[[str], PortForwardingReader]
MuleRestarterFactory = Callable[[str], MuleRestarter]


def default_port_forwarding_reader_factory(gluetun_control_url: str) -> PortForwardingReader:
    """An httpx ``GluetunPortReader`` on the gluetun control-server URL (short timeout)."""
    client = httpx.AsyncClient(base_url=gluetun_control_url, timeout=httpx.Timeout(10.0))
    return GluetunPortReader(client)


def default_mule_restarter_factory(restarter_url: str) -> MuleRestarter:
    """An httpx ``HttpMuleRestarter`` on the docker-socket-proxy URL (short timeout)."""
    client = httpx.AsyncClient(base_url=restarter_url, timeout=httpx.Timeout(10.0))
    return HttpMuleRestarter(client)


MetricsServer = Callable[[int, CollectorRegistry], None]


def default_metrics_server(port: int, registry: CollectorRegistry) -> None:
    """Starts the /metrics HTTP server (daemon thread). Wrapper to fix the argument order."""
    start_http_server(port, registry=registry)  # pragma: no cover


class WebuiServer(Protocol):
    """The ``uvicorn.Server`` shape the crawler drives from the main thread: a ``serve()``
    coroutine (run on the webui thread's OWN loop) and a settable ``should_exit`` flag that
    the serve loop polls — set it True to ask for a graceful return (thread-safe by design)."""

    should_exit: bool

    async def serve(self) -> None: ...  # one line (branch-coverage gotcha, see CLAUDE.md)


# Injectable webui-server factory (fake in test): (built ASGI app, host, port) → uvicorn-shaped
# server. The default builds a real ``uvicorn.Server``; unit tests inject a fake (no real HTTP).
WebuiServerFactory = Callable[[Starlette, str, int], WebuiServer]

# Bound on the webui-thread join at shutdown. Set ``should_exit`` then join: uvicorn's serve
# loop polls ``should_exit`` (~0.1 s) and returns with no active connections, so the join
# normally returns well within the bound. The join runs via ``asyncio.to_thread`` (see
# ``_stop_webui``) so the crawler's armed shutdown ``asyncio.timeout`` can still cancel it.
_WEBUI_JOIN_TIMEOUT_SECONDS = 5.0


def default_webui_server_factory(app: Starlette, host: str, port: int) -> uvicorn.Server:
    """A real ``uvicorn.Server`` bound to ``host:port`` serving the webui ASGI ``app`` (own
    thread + loop). Constructing it opens no socket (that happens in ``serve()``)."""
    return uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))


def _human(message: str) -> None:
    """Human shutdown line on stderr (spec §6: observable progress, outside logging)."""
    print(message, file=sys.stderr, flush=True)


def _build_policy(config: CrawlerConfig) -> WorkerPolicy:
    """Unpacks the policy config into primitives for the application (dependency rule)."""
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
    """A real ``AmuleEcClient`` per instance (default factory, substituted in test)."""
    return AmuleEcClient(endpoint.host, endpoint.port, endpoint.password)


class CrawlerApp:
    """Assembles and runs the crawler (composition root, spec §4/§6)."""

    def __init__(
        self,
        *,
        crawler_config: CrawlerConfig,
        targets: Sequence[TargetSegment],
        matcher_config: MatcherConfig,
        clock: Clock,
        rng: Rng,
        signal_hub: DecisionSignal,
        policy_fingerprint: str,
        client_factory: ClientFactory = default_client_factory,
        download_client_factory: DownloadClientFactory = default_download_client_factory,
        verifier_factory: VerifierFactory = default_verifier_factory,
        port_forwarding_reader_factory: PortForwardingReaderFactory = (
            default_port_forwarding_reader_factory
        ),
        mule_restarter_factory: MuleRestarterFactory = default_mule_restarter_factory,
        metrics_server: MetricsServer = default_metrics_server,
        webui_server_factory: WebuiServerFactory = default_webui_server_factory,
    ) -> None:
        self._crawler_config = crawler_config
        self._targets = tuple(targets)
        self._matcher_config = matcher_config
        self._clock = clock
        self._rng = rng
        self._signal = signal_hub
        self._policy_fingerprint = policy_fingerprint
        self._client_factory = client_factory
        self._download_client_factory = download_client_factory
        self._verifier_factory = verifier_factory
        self._port_forwarding_reader_factory = port_forwarding_reader_factory
        self._mule_restarter_factory = mule_restarter_factory
        self._metrics_server = metrics_server
        self._webui_server_factory = webui_server_factory
        self._shutdown = asyncio.Event()
        self._signal_count = 0

    def _on_signal(self) -> None:
        """Loop handler (never preempts a sync function, spec §6)."""
        self._signal_count += 1
        if self._signal_count == 1:
            _human(
                "Shutdown requested — finishing in-flight searches, clean close… "
                "(Ctrl-C again to force)"
            )
            self._shutdown.set()
        else:
            _human("Forced shutdown.")
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
        """Cycle loop until the shutdown event (cancelled by the ``TaskGroup``)."""
        cycle_index = scheduler_state.read_cycle_index()
        while not self._shutdown.is_set():
            started = self._clock.now()
            await run_search_cycle(
                workers=workers,
                clients=clients,
                keywords=self._crawler_config.search_keywords,
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
        """Port-sync activates IFF the ``port_sync`` section is present (``enabled: true``).

        The unified parser guarantees the section's completeness when present (URLs +
        cadences) — no more "3 tied settings" rule at composition (deploy-simplification design).
        """
        return self._crawler_config.port_sync is not None

    async def _build_port_sync_loop(
        self,
        *,
        stack: AsyncExitStack,
        telemetry: Telemetry,
        edge: EdgeState,
    ) -> PortSyncLoopDeps:
        """Assemble the port-sync loop deps (design §9). Assumes the config is present.

        gluetun reader (factory) + restarter (factory), both ``aclose`` pushed onto the stack.
        DEDICATED port-sync EC connection (R6: no contention with download/search) to the amuled
        endpoint, connected TOLERATING ``MuleUnreachableError`` at boot (a down daemon does not kill
        the crawler; the loop's backoff governs). In prod, host = ``gluetun`` (compose) — it's
        the SAME endpoint as the other EC clients.
        """
        port_sync_config = self._crawler_config.port_sync
        assert port_sync_config is not None  # guaranteed by _port_sync_enabled (mypy: narrow)

        reader = self._port_forwarding_reader_factory(port_sync_config.gluetun_control_url)
        stack.push_async_callback(reader.aclose)  # type: ignore[attr-defined]
        restarter = self._mule_restarter_factory(port_sync_config.restarter_url)
        stack.push_async_callback(restarter.aclose)  # type: ignore[attr-defined]

        # DEDICATED port-sync EC connection: we target the 1st configured amuled endpoint (EC host
        # in prod = gluetun). Tolerates MuleUnreachableError at boot, like the download connection.
        endpoint = self._crawler_config.amules[0]
        ec_client = self._client_factory(endpoint)
        stack.push_async_callback(ec_client.close)
        try:
            await ec_client.connect()
        except MuleUnreachableError as error:
            _logger.warning(
                "port-sync daemon unreachable at startup (%s) — tolerated, retry by the loop",
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
        """Assemble the download + verification loop deps (full mode, spec §7).

        SHARED single repos (``catalog_repo``/``local_repo`` already built; a
        ``SqliteDownloadRepository`` on the SAME ``local_conn`` — single writer on the event
        loop, no race). A 2nd EC connection (``download_config.endpoint``) connected
        tolerating
        ``MuleUnreachableError`` (a down daemon at startup does not kill the crawler; the loop's
        backoff governs). ``staging_dir`` is the configured amuled Incoming; the NAME of the
        completed file now comes from the SHARED EC files (the real on-disk name reported
        by amuled — resolves DV10-Q2; the anti-traversal confinement lives in ``_safe_basename``).
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
                "download daemon unreachable at startup (%s) — tolerated, retry by the loop",
                error,
            )
        downloads_repo = SqliteDownloadRepository(local_conn)
        quarantine = FilesystemQuarantine(Path(quarantine_dir))
        # ``staging_dir`` = amuled's Incoming; the NAME of the completed file comes from the
        # SHARED EC files (DV10-Q2: ``_promote_completion`` builds ``staging_dir / <real name>``).
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
        """Launch the loops, wait for shutdown (UNBOUNDED), ARM the bound, cancel ALL and unwind.

        Waiting on the shutdown signal is FREE (``shutdown_timeout`` enters here DISARMED —
        deadline ``None`` — so the crawler runs until stopped, over an unbounded
        span). AS SOON AS shutdown is requested, we ARM the bound (``reschedule`` to ``now +
        shutdown_deadline_seconds``) BEFORE cancelling: thus the ``TaskGroup`` unwind (the ``await``
        of the cancelled tasks on exit of the ``async with``) THEN the LIFO stack close
        (in ``run``) are both bounded — the app CANNOT appear stuck at shutdown.
        Cancellation lands at the next network ``await`` (never mid DB write, sync repos,
        spec §6).
        PROMPT SHUTDOWN OF ALL LOOPS: each sibling task must be cancelled EXPLICITLY —
        cancelling ``loop_task`` (search) does NOT cancel the download/verify loops, which are its
        siblings in the ``TaskGroup``. Without this, shutdown would wait on each loop's in-cycle
        sleep (``_sleep_or_nudge`` of the download watches ONLY poll/nudge, not ``self._shutdown``
        ; the verify poll sleeps ``verify.poll_interval``), and the ``shutdown_deadline`` armed
        above would fire a ``TimeoutError`` FIRST — a routine Ctrl-C would then force the
        exit instead of a clean shutdown. So we cancel the ENTIRE set of created tasks.
        EMPIRICAL VERIFICATION: cancelling the children of a ``TaskGroup`` (the group itself
        not being cancelled) does NOT propagate a ``CancelledError`` on exit of the ``async with``
        — the unwind is CLEAN. So we print the progress AFTER the block, without ``except*``
        (which would be dead code). A real worker exception, however, would propagate as an
        ``ExceptionGroup`` — we don't mask it.
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
            await self._shutdown.wait()  # UNBOUNDED (the bound is disarmed while running)
            shutdown_timeout.reschedule(
                asyncio.get_running_loop().time() + self._crawler_config.shutdown_deadline_seconds
            )
            for task in tasks:
                task.cancel()
        _human("Workers stopped.")

    def _start_webui(self, stack: AsyncExitStack) -> None:
        """Start the read-only webui on its OWN thread + loop (spec §5), sharing only IMMUTABLE
        state with the crawler (the parsed matcher/targets, the DB paths). It reads through its
        OWN ``ReaderProvider`` (inside ``build_webui_app``); nothing here touches the crawler's
        event loop nor its writer connections. The thread is a daemon (never blocks process
        exit); its graceful stop is registered on ``stack`` so it runs during the normal LIFO
        unwind at shutdown, AFTER ``_supervise`` returns (see ``_stop_webui``)."""
        webui_config = self._crawler_config.webui
        webui_pkg_dir = Path(mulewatch.webui.__file__).parent
        app = build_webui_app(
            catalog_db=Path(self._crawler_config.catalog_db_path),
            local_db=Path(self._crawler_config.local_db_path),
            matcher_config=self._matcher_config,
            targets=self._targets,
            templates_dir=webui_pkg_dir / "adapters" / "templates",
            static_dir=webui_pkg_dir / "adapters" / "static",
        )
        server = self._webui_server_factory(app, webui_config.host, webui_config.port)
        thread = threading.Thread(
            target=self._serve_webui, args=(server,), name="webui", daemon=True
        )
        thread.start()
        stack.push_async_callback(self._stop_webui, server, thread)
        _logger.info("webui serving on %s:%d (own thread)", webui_config.host, webui_config.port)

    def _serve_webui(self, server: WebuiServer) -> None:
        """Webui thread body: run the server on a FRESH loop in THIS thread. A crash DEGRADES
        (spec §17.1): log loudly and return — a webui failure must NOT stop the crawler. We catch
        ``Exception`` only (a ``KeyboardInterrupt``/``SystemExit`` still propagates), never
        ``BaseException``."""
        try:
            asyncio.run(server.serve())
        except Exception:
            _logger.exception("webui thread crashed — crawler continues (degraded, no HTTP)")

    async def _stop_webui(self, server: WebuiServer, thread: threading.Thread) -> None:
        """Graceful webui stop (runs during the LIFO stack unwind at shutdown): ask the serve
        loop to exit (``should_exit`` is polled by ``serve()``), then join the thread OFF the
        event loop via ``asyncio.to_thread`` so the crawler's armed shutdown ``asyncio.timeout``
        stays able to cancel it (the thread is a daemon, so a truly-hung webui dies at exit)."""
        server.should_exit = True
        await asyncio.to_thread(thread.join, _WEBUI_JOIN_TIMEOUT_SECONDS)

    async def run(self) -> None:
        """Async entry point: opens the resources, installs the signals, loops (§6).

        Ownership (spec §6): the ``AsyncExitStack`` owns the long-lived resources (client pool +
        2 connections). The shutdown bound is an ``asyncio.timeout`` ENTERED DISARMED (deadline
        ``None``): the steady-state run (waiting on the signal, cycles) is UNBOUNDED — otherwise
        the crawler would die after ``shutdown_deadline_seconds`` of normal operation. ONLY the
        SHUTDOWN PHASE is bounded: ``_supervise`` ARMS the bound (``reschedule``) as soon as
        shutdown is requested, so the ``TaskGroup`` unwind THEN the LIFO stack close below fall
        under the deadline — the app CANNOT appear stuck at shutdown. An overrun raises
        ``TimeoutError`` (forced exit); the ``finally`` then attempts a best-effort close
        (suppress) so as not to re-block indefinitely. The bound NEVER arms without a requested
        shutdown → a ``TimeoutError`` can only hit a close that drags.
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
            # In-process webui (spec §5): own thread + loop, started EARLY (before the client
            # pool + startup backfill) so it is up promptly and stays isolated from the crawler's
            # synchronous work. Gated by ``webui.enabled``; a crash degrades (spec §17.1). Its
            # graceful stop is on ``stack`` → runs at the normal shutdown unwind (after DB conns
            # are pushed, so it stops the thread before those close during LIFO teardown).
            if self._crawler_config.webui.enabled:
                self._start_webui(stack)
            # SHARED backoff registry: built ONCE, RELOADED from scheduler_state
            # (backoff survives restart, spec §3/§7), injected into ALL workers
            # + passed to the cycle that persists it. Single writer on the event loop → no race.
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
                # CONNECT at pool assembly, BEFORE the 1st coverage readout (otherwise
                # _aggregate_coverage hits an unconnected client and raises). A daemon down at
                # startup must NOT bring down a multi-instance crawler: we TOLERATE the
                # MuleUnreachableError (warning naming the instance) and CONTINUE — the worker's
                # reconnection backoff will govern the retries. connect() is
                # idempotent → the worker's later _ensure_connected() stays a no-op.
                # We do NOT catch broader: EcAuthError (wrong password) is NOT a
                # MuleUnreachableError → it keeps propagating (fail-fast config, spec §14).
                try:
                    await client.connect()
                except MuleUnreachableError as error:
                    _logger.warning(
                        "instance %s unreachable at startup (%s) — tolerated, backoff at cycle",
                        endpoint.name,
                        error,
                    )
                clients.append(client)
                workers.append(SearchWorker(endpoint.name, client, deps))

            _logger.info("crawler started: %d instance(s), node_id=%s", len(clients), node_id)

            verifier: ContentVerifier | None = None
            download_deps: DownloadLoopDeps | None = None
            verify_deps: VerifyLoopDeps | None = None
            # FULL mode ⟺ the ``download`` section is present (``enabled: true``). The unified
            # parser then guarantees the wiring is complete (endpoint/dirs/verifier_url/verify) —
            # no more ``_require_full_config`` gate at composition.
            download_config = self._crawler_config.download
            if download_config is not None:
                verifier = self._verifier_factory(
                    download_config.verifier_url, download_config.verify.client_timeout_seconds
                )
                # Close the verifier client at teardown. The ``ContentVerifier`` port does NOT
                # declare ``aclose`` (http adapter detail) → documented ``# type: ignore``; every
                # impl passed to composition (HttpContentVerifier, test fake) exposes it
                # (DECISION DV16: no getattr/branch → no partial branch to cover).
                stack.push_async_callback(verifier.aclose)  # type: ignore[attr-defined]
                if not await verifier.health():
                    raise ConfigError(
                        "verifier unreachable at startup (health-check failed) — "
                        "refusing to start in full mode"
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
                _logger.info("full mode: download + verification loops armed")

            # Port-sync (High-ID): INDEPENDENT of observer/full mode (own trigger = ``port_sync``
            # section present with ``enabled: true``; completeness guaranteed by the parser).
            port_sync_deps: PortSyncLoopDeps | None = None
            if self._port_sync_enabled():
                port_sync_deps = await self._build_port_sync_loop(
                    stack=stack, telemetry=telemetry, edge=edge
                )
                _logger.info("port-sync (High-ID) armed")

            mode = "full" if download_config is not None else "observer"
            await telemetry.emit(CrawlerStarted(mode=mode))

            # Startup backfill (spec §7/§7.1): re-evaluate the WHOLE catalogue against the
            # current matcher, gated by a policy fingerprint stored in local.db (a
            # comment/whitespace-only edit to matcher.yml/targets.yml still triggers one
            # harmless extra pass, which then writes nothing before the marker updates).
            # Runs to completion BEFORE the loops so tier actions (download nudge, notify)
            # fire for the very first cycle, not a cycle later.
            summary = await run_backfill_if_policy_changed(
                fingerprint=self._policy_fingerprint,
                local_repo=local_repo,
                run_backfill=lambda: reevaluate_catalog(
                    catalog=catalog_repo, engine=engine, signal=self._signal, telemetry=telemetry
                ),
            )
            if summary is None:
                _logger.info("policy unchanged — catalogue re-evaluation skipped")
            else:
                _logger.info(
                    "catalogue re-evaluated: %d files, %d rows written",
                    summary.evaluated,
                    summary.written,
                )

            # Bound ENTERED DISARMED (None): the steady state is unbounded; ``_supervise`` arms it
            # (reschedule) as soon as shutdown is requested → only the shutdown phase + the aclose
            # below are bounded. (Verified empirically: timeout(None) does not fire; reschedule
            # from inside arms; a slow op after arming raises TimeoutError, a fast one does not.)
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
                _human(f"{len(clients)} EC connection(s) closing…")
                await stack.aclose()
                _human("Databases closed — exiting.")
        finally:
            # Best-effort if the bounded shutdown failed (TimeoutError) or if setup raised:
            # close what remains WITHOUT ever re-blocking (suppress any failure/cancellation).
            with suppress(BaseException):
                await stack.aclose()
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
