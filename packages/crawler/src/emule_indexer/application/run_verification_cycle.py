"""La boucle de vérification : reclaim → claim → verify → record → complete (spec verify §6).

Couche APPLICATION. CONSOMMATEUR de la file ``verification_tasks`` (le download en est le
PRODUCTEUR — la file durable EST le couplage, DÉCISION DV5 : pas de nudge dédié, le poll est
le filet). ``run_verification_cycle`` traite UNE tâche (ou dort si la file est vide) ;
``verification_loop`` répète jusqu'à un événement d'arrêt — câblé par ``CrawlerApp`` (Task 11).

Flux d'un cycle (spec §6, DÉCISION DV13) :
  1. ``reclaim_expired()`` (récupère les leases expirés au fil de l'eau + au démarrage).
  2. ``claim_verification()`` → ``None`` (file vide) → dort ``poll_interval`` et rend.
  3. Tâche claimée : ``get_target_id`` → ``expected`` MINIMAL (``{"target_id": …}`` ou ``{}``
     si inconnu — le NO-OP l'ignore, D-analysis enrichira, DÉCISION DV11).
  4. ``verify`` → ``VerificationResult`` ; ``record_verification`` ; ``complete_verification``.

Erreurs (DÉCISION DV6, spec §8) : ``VerifierUnavailableError`` (service injoignable) ou
``RepositoryError`` (écriture du verdict échouée) → ``fail_verification`` (lease → retry ;
après ``max_attempts`` → dead-letter, le repo s'en charge). On n'invente JAMAIS de verdict.
Une réponse 200 malformée arrive DÉJÀ en ``VerificationResult(verdict="error")`` (parsing
défensif de l'adapter) → enregistrée + ``complete`` (déterministe, pas de retry). Déterminisme
: ``Clock``/``sleep`` injectés. Writer unique sur l'event loop → aucun verrou.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from emule_indexer.application.edge_state import EdgeState
from emule_indexer.domain.observability.events import (
    VerificationCompleted,
    VerificationQueueDepthSampled,
    VerifierUnavailable,
)
from emule_indexer.ports.clock import Clock
from emule_indexer.ports.content_verifier import ContentVerifier
from emule_indexer.ports.local_state_repository import ClaimedTask
from emule_indexer.ports.repository_errors import RepositoryError
from emule_indexer.ports.telemetry import Telemetry
from emule_indexer.ports.verifier_errors import VerifierUnavailableError

_logger = logging.getLogger("emule_indexer.application.run_verification_cycle")


class VerificationTaskQueue(Protocol):
    """Sous-ensemble de ``LocalStateRepository`` consommé par la boucle (typage local).

    La boucle ne dépend QUE de reclaim/claim/complete/fail (pas de node_id/enqueue) ; le vrai
    ``SqliteLocalStateRepository`` le satisfait, le fake minimal aussi. Stubs sur UNE ligne.
    """

    def reclaim_expired(self) -> int: ...

    def claim_verification(self) -> ClaimedTask | None: ...

    def complete_verification(self, task_id: int) -> None: ...

    def fail_verification(self, task_id: int) -> None: ...

    def count_pending_verifications(self) -> int: ...


class TargetIdLookup(Protocol):
    """Sous-ensemble de ``SqliteDownloadRepository`` : le lookup hash→target (DÉCISION DV11)."""

    def get_target_id(self, ed2k_hash: str) -> str | None: ...


class VerificationWriter(Protocol):
    """Sous-ensemble de ``CatalogRepository`` : l'écriture du verdict (spec §5)."""

    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: tuple[object, ...],
    ) -> None: ...


@dataclass
class VerifyDeps:
    """Dépendances de la boucle de vérification (la composition les assemble une fois).

    ``targets`` est le repo downloads (lookup hash→target pour ``expected``) ; ``writer`` le
    catalogue (``record_verification``) ; ``queue`` la file locale (consommée). Tous typés aux
    Protocols NARROW ci-dessus → les fakes minimaux de test ET les vrais repos les satisfont.
    """

    queue: VerificationTaskQueue
    verifier: ContentVerifier
    writer: VerificationWriter
    targets: TargetIdLookup
    poll_interval_seconds: float
    clock: Clock
    telemetry: Telemetry
    edge: EdgeState


def _build_expected(deps: VerifyDeps, ed2k_hash: str) -> dict[str, object]:
    """``expected`` MINIMAL en NO-OP (DÉCISION DV11) : ``{"target_id": …}`` ou ``{}`` si inconnu.

    Le verifier NO-OP l'ignore ; D-analysis l'enrichira (taille/durée/codec attendus). Un
    ``target_id`` absent (tâche pour un hash dont la ligne download a été promue/purgée) → ``{}``.

    Une ``RepositoryError`` propagée d'ici (lecture ``targets`` en échec) remonte au filet
    top-level → la task RESTE claimée, libérée par ``reclaim_expired`` après le lease (15 min).
    Choix DÉLIBÉRÉ documenté (logic-download#3 dans l'audit 2026-06-23) : pas de fail-fast
    immédiat → un échec transitoire (SQLITE_BUSY) sur la même `local_conn` que la queue ne
    déclencherait pas non plus le ``fail_verification`` (mêmes points de défaillance), et la
    sémantique du lease est conçue pour rejouer proprement. La latence 15 min est la VALEUR du
    lease ; à raccourcir si jugée trop pénible, pas à contourner ici.
    """
    target_id = deps.targets.get_target_id(ed2k_hash)
    if target_id is None:
        return {}
    return {"target_id": target_id}


async def run_verification_cycle(deps: VerifyDeps) -> None:
    """UN cycle (spec §6). Reclaim → claim → (vide : sleep) → verify → record → complete.

    NE LÈVE JAMAIS (comme ``run_download_cycle``) : tout échec de repo (``RepositoryError`` depuis
    reclaim/claim/record/complete/fail) est absorbé par le filet top-level (log + sleep + skip de
    l'itération — la task claimée repart par le lease → ``reclaim_expired``). Un verifier
    injoignable (``VerifierUnavailableError``) ou une écriture du verdict en échec
    (``RepositoryError`` à record/complete) → ``fail_verification`` (retry via lease ; après
    ``max_attempts`` → dead-letter, le repo s'en charge). On n'invente JAMAIS de verdict.

    Sémantique AT-LEAST-ONCE assumée : ``record_verification`` (catalog.db) et
    ``complete_verification`` (local.db) ne peuvent PAS être atomiques (deux fichiers SQLite). Si
    ``complete`` échoue APRÈS un ``record`` réussi, le lease expire → ``reclaim`` re-vérifie → une
    ligne DUPLIQUÉE est possible dans ``file_verifications`` (table append-only). C'est un artefact
    at-least-once : D-analysis dédupliquera (dernier verdict par hash). On NE crash JAMAIS et on ne
    perd jamais une task. Déterminisme : ``Clock``/``sleep`` injectés.
    """
    try:
        deps.queue.reclaim_expired()
        await deps.telemetry.emit(
            VerificationQueueDepthSampled(count=deps.queue.count_pending_verifications())
        )
        task = deps.queue.claim_verification()
        if task is None:
            # file vide → backoff (pas de busy-spin)
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        expected = _build_expected(deps, task.ed2k_hash)
        try:
            result = await deps.verifier.verify(task.ed2k_hash, expected)
            deps.writer.record_verification(
                task.ed2k_hash, result.verdict, result.real_meta, result.checks
            )
            deps.queue.complete_verification(task.task_id)
            deps.edge.leave("verifier_unavailable")
            await deps.telemetry.emit(
                VerificationCompleted(
                    target_id=str(expected.get("target_id", "inconnu")), verdict=result.verdict
                )
            )
        except VerifierUnavailableError as error:
            _logger.warning(
                "verifier injoignable pour task=%d hash=%s (%s) — fail + backoff (retry)",
                task.task_id,
                task.ed2k_hash,
                error,
            )
            deps.queue.fail_verification(task.task_id)
            await deps.telemetry.emit(
                VerifierUnavailable(first_occurrence=deps.edge.enter("verifier_unavailable"))
            )
            # backoff : pas de spin sur panne (``fail`` remet ``pending`` immédiatement et
            # ``attempts`` est compté au claim → sans ce sleep, une panne transitoire du verifier
            # dead-letterait les tasks en rafale au lieu d'une tentative par ``poll_interval``).
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        except RepositoryError as error:
            _logger.error(
                "écriture du verdict échouée pour task=%d hash=%s (%s) — fail + backoff (retry, "
                "duplicate possible au reclaim : at-least-once)",
                task.task_id,
                task.ed2k_hash,
                error,
            )
            deps.queue.fail_verification(task.task_id)
            # backoff : pas de spin (``fail`` remet ``pending`` immédiatement) — si ``complete``
            # (local.db) échoue durablement alors que verify/record réussissent, sans ce sleep
            # chaque cycle ré-émettrait un RPC verify + une ligne ``file_verifications`` dupliquée
            # en rafale. Avec le sleep : au plus une tentative par ``poll_interval``.
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        _logger.info(
            "task=%d hash=%s vérifiée (verdict=%s)", task.task_id, task.ed2k_hash, result.verdict
        )
    except RepositoryError as error:
        # Filet ULTIME : reclaim/claim/_build_expected OU ``fail_verification`` lui-même a levé →
        # on absorbe pour ne JAMAIS crasher la boucle. La task (si claimée) repart par le lease →
        # ``reclaim``. On dort pour éviter un spin serré si la DB est durablement en erreur.
        _logger.error("persistance vérif en échec (%s) — itération sautée, retry via lease", error)
        await deps.clock.sleep(deps.poll_interval_seconds)


@dataclass
class VerifyLoopDeps(VerifyDeps):
    """``VerifyDeps`` + l'arrêt (DÉCISION DV13). La file est le couplage → pas de nudge dédié."""

    shutdown: asyncio.Event


async def verification_loop(deps: VerifyLoopDeps) -> None:
    """Répète ``run_verification_cycle`` jusqu'à l'arrêt (spec §6/§7).

    Câblée par ``CrawlerApp`` (Task 11) dans le ``TaskGroup`` ; l'annulation (arrêt) atterrit au
    prochain ``await`` (le RPC ``verify`` ou un sleep). ``run_verification_cycle`` NE LÈVE JAMAIS
    (tout ``RepositoryError`` est absorbé + sleep), donc cette boucle ne peut pas crasher le
    ``TaskGroup`` sur une panne DB. Le ``if deps.shutdown.is_set(): break`` post-cycle évite un
    cycle de plus quand l'arrêt est demandé PENDANT le cycle.
    """
    while not deps.shutdown.is_set():
        await run_verification_cycle(deps)
        if deps.shutdown.is_set():
            break
