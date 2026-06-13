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
import sys
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack, suppress

from emule_indexer.adapters.config.crawler_config import CrawlerConfig
from emule_indexer.adapters.config.local_config import AmuleEndpoint, LocalConfig
from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog, open_local
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from emule_indexer.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from emule_indexer.application.run_search_cycle import run_search_cycle
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
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.mule_client import MuleClient, MuleUnreachableError
from emule_indexer.ports.scheduler_state_repository import SchedulerStateRepository

_logger = logging.getLogger("emule_indexer.composition.app")

# Type de la factory de client (injectable en test pour substituer un FakeMuleClient).
ClientFactory = Callable[[AmuleEndpoint], MuleClient]


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
    ) -> None:
        self._crawler_config = crawler_config
        self._local_config = local_config
        self._targets = tuple(targets)
        self._matcher_config = matcher_config
        self._clock = clock
        self._rng = rng
        self._signal = signal_hub
        self._client_factory = client_factory
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
            )
            cycle_index += 1
            elapsed = (self._clock.now() - started).total_seconds()
            remaining = max(0.0, self._crawler_config.cycle_interval_seconds - elapsed)
            await self._clock.sleep(remaining)

    async def _supervise(
        self,
        *,
        shutdown_timeout: asyncio.Timeout,
        workers: Sequence[SearchWorker],
        clients: Sequence[MuleClient],
        node_id: str,
        scheduler_state: SchedulerStateRepository,
        backoff: BackoffRegistry,
    ) -> None:
        """Lance la boucle, attend l'arrêt (NON borné), ARME la borne, annule et unwind.

        L'attente du signal d'arrêt est LIBRE (``shutdown_timeout`` entre ici DÉSARMÉ —
        échéance ``None`` — donc le crawler tourne tant qu'on ne l'arrête pas, sur un temps
        non borné). DÈS l'arrêt demandé, on ARME la borne (``reschedule`` à ``maintenant +
        shutdown_deadline_seconds``) AVANT d'annuler : ainsi l'unwind du ``TaskGroup`` (l'``await``
        du ``loop_task`` annulé à la sortie du ``async with``) PUIS la fermeture LIFO du stack
        (dans ``run``) sont tous deux bornés — l'app ne PEUT pas paraître bloquée à l'arrêt.
        L'annulation atterrit au prochain ``await`` réseau d'un travailleur (jamais en pleine
        écriture DB, repos sync, spec §6).
        VÉRIFICATION EMPIRIQUE : annuler UN enfant d'un ``TaskGroup`` (le groupe lui-même
        n'étant pas annulé) NE propage PAS de ``CancelledError`` au sortir du ``async with``
        — l'unwind est PROPRE. On affiche donc la progression APRÈS le bloc, sans ``except*``
        (qui serait du code mort). Une vraie exception d'un travailleur, elle, propagerait en
        ``ExceptionGroup`` — on ne la masque pas.
        """
        async with asyncio.TaskGroup() as group:
            loop_task = group.create_task(
                self._run_loop(
                    workers=workers,
                    clients=clients,
                    node_id=node_id,
                    scheduler_state=scheduler_state,
                    backoff=backoff,
                )
            )
            await self._shutdown.wait()  # NON borné (la borne est désarmée tant qu'on tourne)
            shutdown_timeout.reschedule(
                asyncio.get_running_loop().time() + self._crawler_config.shutdown_deadline_seconds
            )
            loop_task.cancel()
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
