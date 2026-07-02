"""The verification loop: reclaim → claim → verify → record → complete (verify spec §6).

APPLICATION layer. CONSUMER of the ``verification_tasks`` queue (download is its
PRODUCER — the durable queue IS the coupling, DECISION DV5: no dedicated nudge, the poll is
the net). ``run_verification_cycle`` processes ONE task (or sleeps if the queue is empty);
``verification_loop`` repeats until a shutdown event — wired by ``CrawlerApp`` (Task 11).

Flow of one cycle (spec §6, DECISION DV13):
  1. ``reclaim_expired()`` (recovers expired leases along the way + at startup).
  2. ``claim_verification()`` → ``None`` (empty queue) → sleeps ``poll_interval`` and returns.
  3. Claimed task: ``get_target_id`` → MINIMAL ``expected`` (``{"target_id": …}`` or ``{}``
     if unknown — the NO-OP ignores it, D-analysis will enrich, DECISION DV11).
  4. ``verify`` → ``VerificationResult``; ``record_verification``; ``complete_verification``.

Errors (DECISION DV6, spec §8): ``VerifierUnavailableError`` (service unreachable) or
``RepositoryError`` (verdict write failed) → ``fail_verification`` (lease → retry;
after ``max_attempts`` → dead-letter, the repo handles it). We NEVER invent a verdict.
A malformed 200 response ALREADY arrives as ``VerificationResult(verdict="error")`` (defensive
parsing in the adapter) → recorded + ``complete`` (deterministic, no retry). Determinism:
``Clock``/``sleep`` injected. Single writer on the event loop → no lock.
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
    """Subset of ``LocalStateRepository`` consumed by the loop (local typing).

    The loop depends ONLY on reclaim/claim/complete/fail (no node_id/enqueue); the real
    ``SqliteLocalStateRepository`` satisfies it, the minimal fake too. Stubs on ONE line.
    """

    def reclaim_expired(self) -> int: ...

    def claim_verification(self) -> ClaimedTask | None: ...

    def complete_verification(self, task_id: int) -> None: ...

    def fail_verification(self, task_id: int) -> None: ...

    def count_pending_verifications(self) -> int: ...


class TargetIdLookup(Protocol):
    """Subset of ``SqliteDownloadRepository``: the hash→target lookup (DECISION DV11)."""

    def get_target_id(self, ed2k_hash: str) -> str | None: ...


class VerificationWriter(Protocol):
    """Subset of ``CatalogRepository``: the verdict write (spec §5)."""

    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: tuple[object, ...],
    ) -> None: ...


@dataclass
class VerifyDeps:
    """Dependencies of the verification loop (composition assembles them once).

    ``targets`` is the downloads repo (hash→target lookup for ``expected``); ``writer`` the
    catalog (``record_verification``); ``queue`` the local queue (consumed). All typed to the
    NARROW Protocols above → the minimal test fakes AND the real repos satisfy them.
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
    """MINIMAL ``expected`` in NO-OP (DECISION DV11): ``{"target_id": …}`` or ``{}`` if unknown.

    The NO-OP verifier ignores it; D-analysis will enrich it (expected size/duration/codec). A
    missing ``target_id`` (task for a hash whose download row was promoted/purged) → ``{}``.

    A ``RepositoryError`` propagated from here (``targets`` read failed) bubbles up to the
    top-level net → the task STAYS claimed, released by ``reclaim_expired`` after the lease
    (15 min). A DELIBERATE choice, documented (logic-download#3 in the 2026-06-23 audit): no
    immediate fail-fast → a transient failure (SQLITE_BUSY) on the same `local_conn` as the queue
    would not trigger ``fail_verification`` either (same failure points), and the lease
    semantics are designed to replay cleanly. The 15-min latency is the lease's VALUE; shorten
    it if judged too painful, don't work around it here.
    """
    target_id = deps.targets.get_target_id(ed2k_hash)
    if target_id is None:
        return {}
    return {"target_id": target_id}


async def run_verification_cycle(deps: VerifyDeps) -> None:
    """ONE cycle (spec §6). Reclaim → claim → (empty: sleep) → verify → record → complete.

    NEVER RAISES (like ``run_download_cycle``): any repo failure (``RepositoryError`` from
    reclaim/claim/record/complete/fail) is absorbed by the top-level net (log + sleep + skip the
    iteration — the claimed task comes back via the lease → ``reclaim_expired``). An unreachable
    verifier (``VerifierUnavailableError``) or a failed verdict write
    (``RepositoryError`` at record/complete) → ``fail_verification`` (retry via lease; after
    ``max_attempts`` → dead-letter, the repo handles it). We NEVER invent a verdict.

    Assumed AT-LEAST-ONCE semantics: ``record_verification`` (catalog.db) and
    ``complete_verification`` (local.db) CANNOT be atomic (two SQLite files). If
    ``complete`` fails AFTER a successful ``record``, the lease expires → ``reclaim`` re-verifies →
    a DUPLICATE row is possible in ``file_verifications`` (append-only table). This is an
    at-least-once artifact: D-analysis will dedup (last verdict per hash). We NEVER crash and we
    never lose a task. Determinism: ``Clock``/``sleep`` injected.
    """
    try:
        deps.queue.reclaim_expired()
        await deps.telemetry.emit(
            VerificationQueueDepthSampled(count=deps.queue.count_pending_verifications())
        )
        task = deps.queue.claim_verification()
        if task is None:
            # empty queue → backoff (no busy-spin)
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
                    target_id=str(expected.get("target_id", "unknown")), verdict=result.verdict
                )
            )
        except VerifierUnavailableError as error:
            _logger.warning(
                "verifier unreachable for task=%d hash=%s (%s) — fail + backoff (retry)",
                task.task_id,
                task.ed2k_hash,
                error,
            )
            deps.queue.fail_verification(task.task_id)
            await deps.telemetry.emit(
                VerifierUnavailable(first_occurrence=deps.edge.enter("verifier_unavailable"))
            )
            # backoff: no spin on failure (``fail`` puts back ``pending`` immediately and
            # ``attempts`` is counted at claim → without this sleep, a transient verifier failure
            # would dead-letter the tasks in a burst instead of one attempt per ``poll_interval``).
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        except RepositoryError as error:
            _logger.error(
                "verdict write failed for task=%d hash=%s (%s) — fail + backoff (retry, "
                "duplicate possible on reclaim: at-least-once)",
                task.task_id,
                task.ed2k_hash,
                error,
            )
            deps.queue.fail_verification(task.task_id)
            # backoff: no spin (``fail`` puts back ``pending`` immediately) — if ``complete``
            # (local.db) fails durably while verify/record succeed, without this sleep
            # each cycle would re-emit a verify RPC + a duplicate ``file_verifications`` row
            # in a burst. With the sleep: at most one attempt per ``poll_interval``.
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        _logger.info(
            "task=%d hash=%s verified (verdict=%s)", task.task_id, task.ed2k_hash, result.verdict
        )
    except RepositoryError as error:
        # LAST-RESORT net: reclaim/claim/_build_expected OR ``fail_verification`` itself raised →
        # we absorb it to NEVER crash the loop. The task (if claimed) comes back via the lease →
        # ``reclaim``. We sleep to avoid a tight spin if the DB is durably erroring.
        _logger.error(
            "verification persistence failed (%s) — iteration skipped, retry via lease", error
        )
        await deps.clock.sleep(deps.poll_interval_seconds)


@dataclass
class VerifyLoopDeps(VerifyDeps):
    """``VerifyDeps`` + shutdown (DECISION DV13). The queue is the coupling → no dedicated nudge."""

    shutdown: asyncio.Event


async def verification_loop(deps: VerifyLoopDeps) -> None:
    """Repeats ``run_verification_cycle`` until shutdown (spec §6/§7).

    Wired by ``CrawlerApp`` (Task 11) into the ``TaskGroup``; cancellation (shutdown) lands at
    the next ``await`` (the ``verify`` RPC or a sleep). ``run_verification_cycle`` NEVER RAISES
    (every ``RepositoryError`` is absorbed + sleep), so this loop cannot crash the
    ``TaskGroup`` on a DB failure. The post-cycle ``if deps.shutdown.is_set(): break`` avoids one
    extra cycle when shutdown is requested DURING the cycle.
    """
    while not deps.shutdown.is_set():
        await run_verification_cycle(deps)
        if deps.shutdown.is_set():
            break
