"""La boucle de download : monitor → complétions → nouveaux candidats → sleep/nudge (§5).

Couche APPLICATION. Une SEULE tâche, série, sur l'unique connexion EC download (spec §3/§5) :
aucun entrelacement de trames. ``run_download_cycle`` exécute UNE itération (testable sans
event d'arrêt) ; ``download_loop`` la répète puis attend ``poll_interval`` OU le nudge
(``DecisionSignal``), jusqu'à un événement d'arrêt — câblé par ``CrawlerApp`` en D-verify.

Flux d'une itération (spec §5, DÉCISION D8) :
  1. MONITOR : ``download_queue()`` → pour chaque entrée CONNUE de ``downloads``, réconcilie
     (``downloading`` si en cours, ``completed`` si complète) ; une entrée inconnue (download
     hors crawler) est ignorée.
  2. COMPLÉTIONS : chaque hash ``completed`` (pas ``quarantined``) → ``quarantine.promote`` →
     ``enqueue_verification`` → ``set_state(quarantined)``. Idempotent : ``promote`` échoue →
     reste ``completed``, n'enfile PAS, retry au tour suivant ; déjà ``quarantined`` → sauté.
  3. CANDIDATS : ``catalog.download_decisions()`` (latest=download) ∖ ``downloads`` → pour
     chacun, ``download_policy`` (statut de la cible, dédup, plafond) → si ``download`` :
     ``build_ed2k_link`` (depuis ``last_observation``) → ``add_link`` → ``record_queued``.
     Le plafond est recalculé EN MÉMOIRE au fil du cycle (``committed += size``).

Erreurs (contrats Plan C, spec §9) : ``MuleUnreachableError`` (flux EC mort) → tolère, skip
l'itération (le client se reconnecte au tour suivant ; amuled persiste les downloads).
``RepositoryError`` → absorbée (log + continue). ``promote`` échoue → reste ``completed``.
JAMAIS d'abandon d'un download stallé. Déterminisme : ``Clock``/``sleep`` injectés.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from emule_indexer.domain.download.ed2k_link import build_ed2k_link
from emule_indexer.domain.download.policy import DownloadVerdict, download_policy
from emule_indexer.domain.download.states import DownloadState
from emule_indexer.domain.matching.engine import DownloadCandidate
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.observability.events import (
    DownloadCompleted,
    DownloadQueued,
    PromotionFailed,
)
from emule_indexer.ports.catalog_repository import ObservedFile
from emule_indexer.ports.clock import Clock
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.mule_client import MuleSearchFailedError, MuleUnreachableError
from emule_indexer.ports.mule_download_client import DownloadEntry, MuleDownloadClient
from emule_indexer.ports.quarantine import Quarantine
from emule_indexer.ports.repository_errors import RepositoryError
from emule_indexer.ports.telemetry import Telemetry

_logger = logging.getLogger("emule_indexer.application.run_download_cycle")

# Sujet conventionnel du nudge de download (DÉCISION D13). D-download s'abonne à CE sujet ;
# le câblage du signal("download") côté producteur (pipeline) atterrit en D-verify.
DOWNLOAD_NUDGE_SUBJECT = "download"

StagingResolver = Callable[[DownloadEntry], Path]


class DownloadRepository(Protocol):
    """Protocol STRUCTUREL du repo downloads (typage local ; l'adapter le satisfait).

    Protocol minimal pour que l'application ne dépende QUE de ce dont elle a besoin
    (record_queued/set_state/is_downloaded/committed_bytes/active_states), sans importer
    l'adapter. Le vrai ``SqliteDownloadRepository`` (et le fake de test) le satisfait
    structurellement. Stubs sur UNE ligne (le ``def`` est couvert à la création de la classe).
    """

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool: ...

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None: ...

    def is_downloaded(self, ed2k_hash: str) -> bool: ...

    def committed_bytes(self) -> int: ...

    def active_states(self) -> dict[str, DownloadState]: ...

    def get_target_id(self, ed2k_hash: str) -> str | None: ...


class CatalogReader(Protocol):
    """Protocol STRUCTUREL des LECTURES catalogue dont la boucle a besoin (DÉCISION D9).

    Sous-ensemble de ``CatalogRepository`` (download_decisions + last_observation) : la boucle
    ne dépend QUE de ce qu'elle lit, donc le fake minimal de test la satisfait sans implémenter
    record_observation/record_decision/last_decision. Le vrai ``SqliteCatalogRepository`` le
    satisfait aussi (il a ces deux méthodes). Stubs sur UNE ligne.
    """

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None: ...


class VerificationQueue(Protocol):
    """Protocol STRUCTUREL de l'enfilement de vérification (sous-ensemble de LocalStateRepository).

    La boucle ne dépend que d'``enqueue_verification`` ; le fake minimal de test n'a pas à
    implémenter claim/complete/fail/reclaim. Le vrai ``SqliteLocalStateRepository`` le satisfait.
    """

    def enqueue_verification(self, ed2k_hash: str) -> bool: ...


@dataclass
class DownloadDeps:
    """Dépendances de la boucle de download (la composition les assemble une fois).

    ``staging_path_for`` mappe une entrée de file vers le chemin du fichier complété en
    staging (DÉCISION D2 : EC n'expose pas ce chemin ; la composition de D-verify le branche
    sur le layout amuled). ``targets`` sert au lookup ``target_id → status`` (politique pure).
    ``catalog``/``local`` sont typés aux Protocols NARROW ci-dessus (``CatalogReader``/
    ``VerificationQueue``) — la boucle ne dépend que du sous-ensemble lu/écrit (cohérent avec
    le Protocol local ``DownloadRepository``), donc les fakes minimaux de test sont acceptés.
    """

    client: MuleDownloadClient
    quarantine: Quarantine
    downloads: DownloadRepository
    catalog: CatalogReader
    local: VerificationQueue
    targets: Sequence[TargetSegment]
    disk_cap_bytes: int
    staging_path_for: StagingResolver
    clock: Clock
    telemetry: Telemetry


@dataclass
class DownloadLoopDeps(DownloadDeps):
    """``DownloadDeps`` + ce qu'il faut pour RÉPÉTER (nudge, cadence, arrêt) — DÉCISION D12."""

    signal: DecisionSignal
    poll_interval_seconds: float
    shutdown: asyncio.Event


def _target_status(targets: Sequence[TargetSegment], target_id: str) -> str:
    """Statut de la cible (lookup ``target_id → status``) ; ``complete`` par défaut si la cible
    a disparu de la config (conservateur : ne pas télécharger pour une cible inconnue)."""
    for target in targets:
        if target.target_id == target_id:
            return target.status
    return "complete"


async def _monitor(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Réconcilie ``downloads`` avec la vraie file amuled (étape 1, spec §5)."""
    queue = await deps.client.download_queue()
    for entry in queue:
        current = states.get(entry.ed2k_hash)
        if current is None:
            continue  # download hors crawler : ignoré
        if current in {DownloadState.QUARANTINED, DownloadState.FAILED}:
            continue  # terminal côté crawler : ne pas régresser
        target = DownloadState.COMPLETED if entry.is_complete else DownloadState.DOWNLOADING
        if target != current:
            deps.downloads.set_state(entry.ed2k_hash, target)
            states[entry.ed2k_hash] = target


async def _promote_completion(deps: DownloadDeps, ed2k_hash: str) -> None:
    """Promeut un hash ``completed`` → quarantaine + enqueue + ``quarantined`` (étape 2, §5)."""
    entry = DownloadEntry(ed2k_hash=ed2k_hash, size_done=0, size_full=0)
    staging_path = deps.staging_path_for(entry)
    try:
        deps.quarantine.promote(staging_path, ed2k_hash)
    except Exception as error:  # noqa: BLE001 — toute panne FS laisse completed (retry idempotent)
        _logger.warning(
            "quarantaine échouée pour hash=%s (%s) — reste completed, retry", ed2k_hash, error
        )
        await deps.telemetry.emit(PromotionFailed(ed2k_hash=ed2k_hash))
        return
    deps.local.enqueue_verification(ed2k_hash)
    deps.downloads.set_state(ed2k_hash, DownloadState.QUARANTINED)
    target_id = deps.downloads.get_target_id(ed2k_hash) or "inconnu"
    await deps.telemetry.emit(DownloadCompleted(target_id=target_id, ed2k_hash=ed2k_hash))
    _logger.info("hash=%s mis en quarantaine + vérification enfilée", ed2k_hash)


async def _handle_completions(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Promeut chaque hash ``completed`` pas encore ``quarantined`` (étape 2, spec §5)."""
    for ed2k_hash, state in list(states.items()):
        if state is DownloadState.COMPLETED:
            await _promote_completion(deps, ed2k_hash)


async def _queue_new_candidates(deps: DownloadDeps) -> None:
    """Rejoue les décisions tier=download absentes de ``downloads`` (étape 3, spec §5)."""
    committed = deps.downloads.committed_bytes()
    for candidate in deps.catalog.download_decisions():
        if deps.downloads.is_downloaded(candidate.ed2k_hash):
            continue
        observation = deps.catalog.last_observation(candidate.ed2k_hash)
        if observation is None:
            _logger.warning(
                "candidat hash=%s sans observation — lien impossible, sauté", candidate.ed2k_hash
            )
            continue
        verdict = download_policy(
            tier="download",
            target_status=_target_status(deps.targets, candidate.target_id),
            already_downloaded=False,
            committed_bytes=committed,
            file_size=observation.size_bytes,
            disk_cap=deps.disk_cap_bytes,
        )
        if verdict is not DownloadVerdict.DOWNLOAD:
            _logger.info(
                "candidat hash=%s → %s (sauté/différé)", candidate.ed2k_hash, verdict.value
            )
            continue
        # record_queued SEUL ici (écriture DB sync) ; le lien ed2k est bâti et émis par
        # _add_links (I/O réseau) pour tout 'queued' — l'écriture précède le réseau, et un
        # add_link qui lève laisse le download 'queued' en base (rattrapé au tour suivant).
        deps.downloads.record_queued(
            candidate.ed2k_hash, candidate.target_id, observation.size_bytes
        )
        committed += observation.size_bytes  # plafond recalculé en mémoire au fil du cycle
        _logger.info("candidat hash=%s mis en file de download", candidate.ed2k_hash)
        await deps.telemetry.emit(DownloadQueued(target_id=candidate.target_id))


async def _add_links(deps: DownloadDeps) -> None:
    """Émet les ``add_link`` EC pour les downloads ``queued`` sans lien encore envoyé.

    Séparé de ``_queue_new_candidates`` pour que l'écriture DB (sync) précède l'I/O réseau
    (async) : un ``MuleUnreachableError`` à ``add_link`` laisse le download ``queued`` en base
    (le monitor du tour suivant rattrape). On ré-émet le lien pour tout ``queued`` connu.

    Deux échecs d'``add_link`` à distinguer (spec §9) :
      - ``MuleSearchFailedError`` (le daemon a répondu ``EC_OP_FAILED`` — lien explicitement
        REJETÉ) : on marque CE hash ``failed`` (log + ``set_state``) et on ``continue`` au
        suivant. Réessayer ne ferait que ré-émettre le même lien rejeté en boucle.
      - ``MuleUnreachableError`` (flux EC mort) : on laisse PROPAGER — la capture de tête de
        ``run_download_cycle`` saute toute l'itération (un daemon mort fait tout échouer).
    """
    # Re-lecture FRAÎCHE de active_states : _queue_new_candidates a écrit de nouvelles lignes
    # QUEUED ce cycle, absentes du dict passé à _monitor/_handle_completions (figé en début).
    states = deps.downloads.active_states()
    for ed2k_hash, state in states.items():
        if state is not DownloadState.QUEUED:
            continue
        observation = deps.catalog.last_observation(ed2k_hash)
        if observation is None:
            continue
        link = build_ed2k_link(observation.filename, observation.size_bytes, ed2k_hash)
        try:
            await deps.client.add_link(link)
        except MuleSearchFailedError as error:
            deps.downloads.set_state(ed2k_hash, DownloadState.FAILED)
            _logger.warning(
                "add_link rejeté par amuled pour hash=%s (%s) — marqué failed", ed2k_hash, error
            )


async def run_download_cycle(deps: DownloadDeps) -> None:
    """UNE itération de la boucle de download (spec §5). Ne lève jamais : tolère/absorbe.

    Tout flux EC mort (``MuleUnreachableError``) ou échec de repo (``RepositoryError``) est
    toléré (log + skip de l'itération) — la prochaine itération réessaie (amuled persiste les
    downloads). Les repos sont sync → l'annulation (arrêt) atterrit aux ``await`` réseau.
    """
    try:
        states = deps.downloads.active_states()
        await _monitor(deps, states)
        await _handle_completions(deps, states)
        await _queue_new_candidates(deps)
        await _add_links(deps)
    except MuleUnreachableError as error:
        _logger.warning("daemon download injoignable (%s) — itération sautée, retry", error)
    except RepositoryError as error:
        _logger.error("persistance download en échec (%s) — itération sautée, retry", error)


async def _sleep_or_nudge(deps: DownloadLoopDeps) -> None:
    """Attend ``poll_interval`` OU le nudge ``download``, au PREMIER des deux (spec §5).

    ``asyncio.wait(FIRST_COMPLETED)`` puis annulation du perdant : un changement de décision
    (nudge) réveille la boucle tout de suite ; sinon le poll de repli la réveille à la cadence.
    L'annulation d'arrêt atterrit ICI (un ``await``), jamais en pleine écriture DB (sync).
    """
    sleep_task = asyncio.ensure_future(deps.clock.sleep(deps.poll_interval_seconds))
    nudge_task = asyncio.ensure_future(deps.signal.wait(DOWNLOAD_NUDGE_SUBJECT))
    try:
        await asyncio.wait({sleep_task, nudge_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (sleep_task, nudge_task):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def download_loop(deps: DownloadLoopDeps) -> None:
    """Répète ``run_download_cycle`` puis attend (poll/nudge) jusqu'à l'arrêt (DÉCISION D12).

    Câblée par ``CrawlerApp`` (D-verify) dans le ``TaskGroup`` ; l'annulation (arrêt) atterrit
    au prochain ``await`` (poll EC ou attente sleep/nudge), jamais en pleine écriture DB.
    """
    while not deps.shutdown.is_set():
        await run_download_cycle(deps)
        if deps.shutdown.is_set():
            break
        await _sleep_or_nudge(deps)
