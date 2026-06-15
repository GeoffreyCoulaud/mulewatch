"""Un cycle de recherche : statut → coverage → keywords → fan-out → drain → avance (§4).

Couche APPLICATION. ``run_search_cycle`` exécute UN cycle (spec §4) :

  1. ``network_status`` de CHAQUE travailleur → ``effective_coverage`` agrégé (loggé).
  2. ``generate_keywords(targets)`` → larges + ciblés ; ``shuffle_for_cycle`` (seed =
     node_id + index de cycle).
  3. enfile un ``SearchTask`` (mot-clé × canal) dans une ``asyncio.Queue`` partagée.
  4. N travailleurs drainent en parallèle (un par instance) ; sentinelle par travailleur.
  5. queue drainée → ``write_cycle_state`` (index = N+1, last_full_cycle_at) ET
     ``save_channel_backoff`` (snapshot du registre PARTAGÉ) — AU MÊME MOMENT (spec §3/§7).

Le pool dégénère en boucle séquentielle à N=1 (spec §3). Les travailleurs partagent la
queue ; chacun lit jusqu'à la sentinelle ``None``. Le ``TaskGroup`` supervise : une
annulation (arrêt, spec §6) atterrit au prochain ``await`` réseau, jamais en pleine
écriture DB (repos sync). Le backoff (registre partagé, muté par les travailleurs pendant
le cycle) n'est PERSISTÉ qu'en FIN de cycle, exactement comme ``cycle_index`` : un kill au
milieu rejoue le cycle ET re-arme le backoff depuis l'état du cycle précédent (cohérent —
l'index n'avance pas non plus à mi-cycle, spec §7).
"""

import asyncio
import logging
from collections.abc import Sequence

from emule_indexer.application.edge_state import EdgeState
from emule_indexer.application.networks import ED2K, KAD
from emule_indexer.application.search_worker import BackoffRegistry, SearchTask, SearchWorker
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.observability.events import (
    AllInstancesBlind,
    ConnectedInstancesSampled,
    SearchCycleCompleted,
)
from emule_indexer.domain.search.coverage import Coverage, effective_coverage
from emule_indexer.domain.search.cycle import Rng, shuffle_for_cycle
from emule_indexer.domain.search.keywords import generate_keywords
from emule_indexer.ports.clock import Clock
from emule_indexer.ports.mule_client import (
    KadStatus,
    MuleClient,
    MuleUnreachableError,
    SearchChannel,
)
from emule_indexer.ports.scheduler_state_repository import SchedulerStateRepository
from emule_indexer.ports.telemetry import Telemetry

_logger = logging.getLogger("emule_indexer.application.run_search_cycle")

# Les deux canaux balayés à chaque cycle (spec MVP §6 : global serveurs + Kad).
_CHANNELS = (SearchChannel.GLOBAL, SearchChannel.KAD)


def _is_search_capable(*, ed2k_high: bool, kad_status: KadStatus) -> bool:
    """Une instance peut-elle faire ABOUTIR une recherche ? (HighID OU Kad CONNECTED).

    Traduction APPLICATION du ``NetworkStatus`` (port) en booléen pur, avant d'appeler le
    domaine ``effective_coverage`` (qui ne connaît pas ``NetworkStatus`` — règle de
    dépendance, le domaine n'importe jamais un port).
    """
    return ed2k_high or kad_status == KadStatus.CONNECTED


async def _aggregate_coverage(
    clients: Sequence[MuleClient], telemetry: Telemetry, edge: EdgeState
) -> None:
    """Relève le statut → gauges connected{network} + couverture agrégée (loggée, spec §7)."""
    capable: list[bool] = []
    ed2k_count = 0
    kad_count = 0
    for client in clients:
        # Une instance injoignable au relevé (flux EC mort / pas encore connectée) ne doit PAS
        # faire tomber tout le cycle : on la compte NON search-capable (l'agrégat reportera
        # alors BLIND/DEGRADED, le vrai état). On NE (re)connecte PAS ici — le travailleur
        # possède le cycle de connexion et son backoff anti-ban (re-connecter chaque cycle
        # martèlerait un daemon down et court-circuiterait ce backoff, spec §3/§7).
        try:
            status = await client.network_status()
        except MuleUnreachableError as error:
            _logger.warning("instance injoignable au relevé de statut (%s) — non capable", error)
            capable.append(False)
            continue
        if status.ed2k_high:
            ed2k_count += 1
        if status.kad_status == KadStatus.CONNECTED:
            kad_count += 1
        capable.append(_is_search_capable(ed2k_high=status.ed2k_high, kad_status=status.kad_status))
    await telemetry.emit(ConnectedInstancesSampled(network=ED2K, count=ed2k_count))
    await telemetry.emit(ConnectedInstancesSampled(network=KAD, count=kad_count))
    coverage = effective_coverage(capable)
    if coverage == Coverage.BLIND:
        _logger.warning("effective_coverage=%s (blind)", coverage)
        await telemetry.emit(AllInstancesBlind(first_occurrence=edge.enter("coverage_blind")))
    else:
        _logger.info("effective_coverage=%s (%d instance(s))", coverage, len(capable))
        edge.leave("coverage_blind")


async def _worker_loop(worker: SearchWorker, queue: "asyncio.Queue[SearchTask | None]") -> None:
    """Draine la queue jusqu'à la sentinelle ``None`` (un travailleur).

    PAUSE JITTERÉE inter-mots-clés (spec §5/§7) ENTRE deux items, JAMAIS après le dernier :
    si la file est déjà vidée après cet item, on saute la pause (inutile de dormir avant de
    sortir / d'attendre une sentinelle). La pause espace les recherches d'un même travailleur
    → ``amuled`` évite de se faire bannir d'un serveur eD2k.
    """
    while True:
        task = await queue.get()
        try:
            if task is None:
                return
            await worker.run_task(task)
            if not queue.empty():  # encore des items réels → on espace avant le suivant
                await worker.pause_between_items()
        finally:
            queue.task_done()


async def run_search_cycle(
    *,
    workers: Sequence[SearchWorker],
    clients: Sequence[MuleClient],
    targets: Sequence[TargetSegment],
    rng: Rng,
    node_id: str,
    cycle_index: int,
    scheduler_state: SchedulerStateRepository,
    backoff: BackoffRegistry,
    clock: Clock,
    telemetry: Telemetry,
    edge: EdgeState,
) -> None:
    """Exécute UN cycle complet (spec §4) ; persiste l'avance + le backoff EN FIN (spec §7)."""
    started = clock.now()
    await _aggregate_coverage(clients, telemetry, edge)
    keywords = generate_keywords(targets)
    texts = tuple(keyword.text for keyword in keywords)
    ordered = shuffle_for_cycle(texts, rng, node_id, cycle_index)
    queue: asyncio.Queue[SearchTask | None] = asyncio.Queue()
    for text in ordered:
        for channel in _CHANNELS:
            queue.put_nowait(SearchTask(keyword=text, channel=channel))
    _logger.info(
        "cycle %d : %d mot(s)-clé × %d canaux = %d tâche(s)",
        cycle_index,
        len(ordered),
        len(_CHANNELS),
        queue.qsize(),
    )
    async with asyncio.TaskGroup() as group:
        for worker in workers:
            group.create_task(_worker_loop(worker, queue))
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)
    # FIN de cycle : index ET backoff persistés ENSEMBLE (spec §3/§7).
    scheduler_state.write_cycle_state(cycle_index + 1, clock.now())
    scheduler_state.save_channel_backoff(backoff.snapshot())
    duration = (clock.now() - started).total_seconds()
    await telemetry.emit(SearchCycleCompleted(cycle_index=cycle_index, duration_seconds=duration))
    _logger.info("cycle %d terminé", cycle_index)
