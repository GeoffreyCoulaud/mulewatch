"""The download loop: monitor → completions → new candidates → sleep/nudge (§5).

APPLICATION layer. A SINGLE task, serial, on the sole download EC connection (spec §3/§5):
no frame interleaving. ``run_download_cycle`` runs ONE iteration (testable without a
shutdown event); ``download_loop`` repeats it then waits ``poll_interval`` OR the nudge
(``DecisionSignal``), until a shutdown event — wired by ``CrawlerApp`` in D-verify.

Flow of one iteration (spec §5, DECISION D8):
  1. MONITOR: ``download_queue()`` → for each entry KNOWN to ``downloads``, reconciles
     ``downloading`` (QUEUED→DOWNLOADING); an unknown entry (download outside the crawler) is
     ignored. Completion is NO LONGER inferred from bytes (see ``_monitor``).
  2. COMPLETIONS: ``shared_files()`` → each tracked hash present in amuled's SHARED files
     (POSITIVE completion signal, with the real on-disk name) → ``set_state(completed)`` →
     ``quarantine.promote(staging_dir / name)`` → ``enqueue_verification`` → ``quarantined``.
     Idempotent: ``promote`` fails → stays ``completed``, does NOT enqueue, retry next round
     (the hash stays in the shared files); already ``quarantined``/``failed`` → skipped.
  3. CANDIDATES: ``catalog.download_decisions()`` (latest=download) ∖ ``downloads`` → for
     each, ``download_policy`` (target status, dedup, cap) → if ``download``:
     ``build_ed2k_link`` (from ``last_observation``) → ``add_link`` → ``record_queued``.
     The cap is recomputed IN MEMORY as the cycle proceeds (``committed += size``).

Errors (Plan C contracts, spec §9): ``MuleUnreachableError`` (EC stream dead) → tolerate, skip
the iteration (the client reconnects next round; amuled persists the downloads).
``RepositoryError`` → absorbed (log + continue). ``promote`` fails → stays ``completed``.
NEVER abandon a stalled download. Determinism: ``Clock``/``sleep`` injected.
"""

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from catalog_matching.ed2k_link import build_ed2k_link
from catalog_matching.engine import DownloadCandidate
from catalog_matching.models import TargetSegment
from mulewatch.domain.download.policy import DownloadVerdict, download_policy
from mulewatch.domain.download.states import DownloadState
from mulewatch.domain.observability.events import (
    DownloadCompleted,
    DownloadQueued,
    PromotionFailed,
)
from mulewatch.ports.catalog_repository import ObservedFile
from mulewatch.ports.clock import Clock
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.mule_client import MuleSearchFailedError, MuleUnreachableError
from mulewatch.ports.mule_download_client import MuleDownloadClient
from mulewatch.ports.quarantine import Quarantine
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.telemetry import Telemetry

_logger = logging.getLogger("mulewatch.application.run_download_cycle")

# Conventional subject of the download nudge (DECISION D13). D-download subscribes to THIS
# subject; the signal("download") wiring on the producer side (pipeline) lands in D-verify.
DOWNLOAD_NUDGE_SUBJECT = "download"


def _safe_basename(name: str) -> str | None:
    """Traversal-safe confined basename; ``None`` if degenerate (``""``/``.``/``..``).

    The name comes from amuled (external input — defense in depth, cf. CLAUDE.md "filenames
    are hostile input"): we confine the SOURCE of ``os.replace`` to ``staging_dir``.
    """
    base = Path(name).name
    if base in {"", ".", ".."}:
        return None
    return base


class DownloadRepository(Protocol):
    """STRUCTURAL Protocol of the downloads repo (local typing; the adapter satisfies it).

    Minimal Protocol so the application depends ONLY on what it needs
    (record_queued/set_state/is_downloaded/committed_bytes/active_states), without importing
    the adapter. The real ``SqliteDownloadRepository`` (and the test fake) satisfies it
    structurally. Stubs on ONE line (the ``def`` is covered when the class is created).
    """

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool: ...

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None: ...

    def is_downloaded(self, ed2k_hash: str) -> bool: ...

    def committed_bytes(self) -> int: ...

    def active_states(self) -> dict[str, DownloadState]: ...

    def get_target_id(self, ed2k_hash: str) -> str | None: ...


class CatalogReader(Protocol):
    """STRUCTURAL Protocol of the catalog READS the loop needs (DECISION D9).

    Subset of ``CatalogRepository`` (download_decisions + last_observation): the loop
    depends ONLY on what it reads, so the minimal test fake satisfies it without implementing
    record_observation/record_decision/last_decision. The real ``SqliteCatalogRepository``
    satisfies it too (it has these two methods). Stubs on ONE line.
    """

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None: ...


class VerificationQueue(Protocol):
    """STRUCTURAL Protocol of verification enqueuing (subset of LocalStateRepository).

    The loop depends only on ``enqueue_verification``; the minimal test fake need not
    implement claim/complete/fail/reclaim. The real ``SqliteLocalStateRepository`` satisfies it.
    """

    def enqueue_verification(self, ed2k_hash: str) -> bool: ...


@dataclass
class DownloadDeps:
    """Dependencies of the download loop (composition assembles them once).

    ``staging_dir`` is amuled's Incoming (DECISION D2: EC does not expose the staging path;
    D-verify composition wires it from the amuled layout). The NAME of the completed file no
    longer comes from the download queue: it comes from the SHARED EC files (the real on-disk
    name reported by amuled), so ``staging_path = staging_dir / <real name>``. ``targets`` serves
    the ``target_id → status`` lookup (pure policy). ``catalog``/``local`` are typed to the NARROW
    Protocols above (``CatalogReader``/``VerificationQueue``) — the loop depends only on the
    subset read/written (consistent with the local ``DownloadRepository`` Protocol), so the
    minimal test fakes are accepted.
    """

    client: MuleDownloadClient
    quarantine: Quarantine
    downloads: DownloadRepository
    catalog: CatalogReader
    local: VerificationQueue
    targets: Sequence[TargetSegment]
    disk_cap_bytes: int
    staging_dir: Path
    clock: Clock
    telemetry: Telemetry


@dataclass
class DownloadLoopDeps(DownloadDeps):
    """``DownloadDeps`` + what it takes to REPEAT (nudge, cadence, shutdown) — DECISION D12."""

    signal: DecisionSignal
    poll_interval_seconds: float
    shutdown: asyncio.Event


def _target_status(targets: Sequence[TargetSegment], target_id: str) -> str:
    """Target status (lookup ``target_id → status``); ``complete`` by default if the target
    has vanished from the config (conservative: do not download for an unknown target)."""
    for target in targets:
        if target.target_id == target_id:
            return target.status
    return "complete"


async def _monitor(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Reconciles ``downloads`` with the amuled queue: QUEUED→DOWNLOADING (step 1, spec §5).

    Completion is NO LONGER inferred from bytes (PS_COMPLETE is unobservable via the queue — cf.
    docs/reference/2026-06-17-amuled-completion-behavior.md): it comes from the shared files
    (_handle_completions). Here we only record that amuled is pulling a queued download.
    """
    queue = await deps.client.download_queue()
    for entry in queue:
        current = states.get(entry.ed2k_hash)
        if current is None:
            continue  # download outside the crawler: ignored
        if current in {
            DownloadState.QUARANTINED,
            DownloadState.FAILED,
            DownloadState.COMPLETED,
        }:
            continue  # terminal / already completed: don't regress
        if current is not DownloadState.DOWNLOADING:
            deps.downloads.set_state(entry.ed2k_hash, DownloadState.DOWNLOADING)
            states[entry.ed2k_hash] = DownloadState.DOWNLOADING


async def _promote_completion(
    deps: DownloadDeps,
    ed2k_hash: str,
    name: str,
    current: DownloadState,
    states: dict[str, DownloadState],
) -> None:
    """Marks ``completed`` (stamps completed_at) then promotes → quarantine (step 2, §5).

    The ``staging_path`` is ``staging_dir / <real amuled name>`` (resolves DV10-Q2: the
    ``name(0)`` dedup is handled since the name comes from amuled). ``promote`` fails → stays
    ``completed``, retry next round (the hash is still in the shared files — persistent signal).
    """
    safe = _safe_basename(name)
    if safe is None:
        _logger.warning(
            "degenerate shared name for hash=%s (%r) — promotion skipped", ed2k_hash, name
        )
        return
    if current is not DownloadState.COMPLETED:
        deps.downloads.set_state(ed2k_hash, DownloadState.COMPLETED)
        states[ed2k_hash] = DownloadState.COMPLETED
    try:
        deps.quarantine.promote(deps.staging_dir / safe, ed2k_hash)
    except Exception as error:  # noqa: BLE001 — any FS failure leaves completed (idempotent retry)
        _logger.warning(
            "quarantine failed for hash=%s (%s) — stays completed, retry", ed2k_hash, error
        )
        await deps.telemetry.emit(PromotionFailed(ed2k_hash=ed2k_hash))
        return
    deps.local.enqueue_verification(ed2k_hash)
    deps.downloads.set_state(ed2k_hash, DownloadState.QUARANTINED)
    states[ed2k_hash] = DownloadState.QUARANTINED
    target_id = deps.downloads.get_target_id(ed2k_hash) or "unknown"
    await deps.telemetry.emit(DownloadCompleted(target_id=target_id, ed2k_hash=ed2k_hash))
    _logger.info("hash=%s quarantined + verification enqueued", ed2k_hash)


async def _handle_completions(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Promotes each tracked hash that appears in amuled's SHARED files (step 2, §5).

    Presence in the shared files = POSITIVE completion (file already moved/in place, auto-shared
    by amuled). We promote with the real name. Terminal hashes (quarantined/failed) are ignored.

    PER-HASH isolation (error-boundary#2): a ``RepositoryError`` in the promotion of one hash
    is logged and CONTINUES with the next ones. Without this net, a repo failure on hash N
    would abandon N+1, N+2 of the same cycle (the completion signal is re-evaluated the next
    cycle; no permanent loss, but intra-cycle starvation is undesirable).
    """
    shared = await deps.client.shared_files()
    for entry in shared:
        current = states.get(entry.ed2k_hash)
        if current is None:
            continue  # shared file outside the crawler: ignored
        if current in {DownloadState.QUARANTINED, DownloadState.FAILED}:
            continue  # already promoted / failed
        try:
            await _promote_completion(deps, entry.ed2k_hash, entry.name, current, states)
        except RepositoryError as error:
            _logger.error(
                "completion hash=%s repo failure (%s) — hash skipped, continues",
                entry.ed2k_hash,
                error,
            )


async def _queue_new_candidates(deps: DownloadDeps) -> None:
    """Replays tier=download decisions missing from ``downloads`` (step 3, spec §5)."""
    committed = deps.downloads.committed_bytes()
    for candidate in deps.catalog.download_decisions():
        if deps.downloads.is_downloaded(candidate.ed2k_hash):
            continue
        observation = deps.catalog.last_observation(candidate.ed2k_hash)
        if observation is None:
            _logger.warning(
                "candidate hash=%s without observation — link impossible, skipped",
                candidate.ed2k_hash,
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
                "candidate hash=%s → %s (skipped/deferred)", candidate.ed2k_hash, verdict.value
            )
            continue
        # record_queued ONLY here (sync DB write); the ed2k link is built and emitted by
        # _add_links (network I/O) for every 'queued' — the write precedes the network, and an
        # add_link that raises leaves the download 'queued' in the DB (caught up next round).
        deps.downloads.record_queued(
            candidate.ed2k_hash, candidate.target_id, observation.size_bytes
        )
        committed += observation.size_bytes  # cap recomputed in memory as the cycle proceeds
        _logger.info("candidate hash=%s queued for download", candidate.ed2k_hash)
        await deps.telemetry.emit(DownloadQueued(target_id=candidate.target_id))


async def _add_links(deps: DownloadDeps) -> None:
    """Emits the EC ``add_link`` calls for ``queued`` downloads with no link sent yet.

    Split from ``_queue_new_candidates`` so the (sync) DB write precedes the (async) network
    I/O: a ``MuleUnreachableError`` at ``add_link`` leaves the download ``queued`` in the DB
    (the next round's monitor catches up). We re-emit the link for every known ``queued``.

    Two ``add_link`` failures to distinguish (spec §9):
      - ``MuleSearchFailedError`` (the daemon answered ``EC_OP_FAILED`` — link explicitly
        REJECTED): we mark THIS hash ``failed`` (log + ``set_state``) and ``continue`` to the
        next. Retrying would only re-emit the same rejected link in a loop.
      - ``MuleUnreachableError`` (EC stream dead): we let it PROPAGATE — the top capture of
        ``run_download_cycle`` skips the whole iteration (a dead daemon makes everything fail).
    """
    # FRESH re-read of active_states: _queue_new_candidates wrote new QUEUED rows this cycle,
    # absent from the dict passed to _monitor/_handle_completions (frozen at the start).
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
                "add_link rejected by amuled for hash=%s (%s) — marked failed", ed2k_hash, error
            )


async def run_download_cycle(deps: DownloadDeps) -> None:
    """ONE iteration of the download loop (spec §5). Never raises: tolerates/absorbs.

    Two distinct error DOCTRINES (item I2 — anti-starvation):

    - ``MuleUnreachableError`` (EC stream dead, from ``_monitor`` or ``_add_links``) = dead
      daemon → ABORT the iteration ("a dead daemon makes everything fail", cf. ``_add_links``).
      We skip the rest; the next iteration retries (amuled persists the downloads).
    - ``RepositoryError`` (persistence failure, NO client I/O) → ISOLATED PER STEP: a repo
      failure in one step must NOT starve the others. Each step that can raise
      ``RepositoryError`` (monitor/completions/candidates) is wrapped separately (log +
      ``continue`` to the next step). ``_add_links`` re-reads ``active_states`` FRESHLY, so it
      runs even if an upstream step partially failed. "NEVER abandon a stalled download": the
      1→2→3→4 order is preserved, each step is best-effort.

    The repos are sync → cancellation (shutdown) lands at the network ``await``, never mid-write.

    DECISION (audit 2026-06-23 / observability#5): a ``MuleUnreachableError`` here does NOT
    emit ``InstanceUnreachable`` (unlike ``run_search_cycle``). The E-D5 taxonomy files this
    event under SEARCH only; the download loop is single-instance and the label
    ``instance=...`` would be meaningless (counter shared with the search workers). The
    unavailability is handled by the next cycle's retry + the warning log. Intentional
    asymmetry.
    """
    # Step 1 — MONITOR: client I/O → MuleUnreachableError = dead daemon = ABORT the iteration.
    # (A RepositoryError from ``set_state`` is isolated HERE too: it doesn't starve steps 2-4.)
    try:
        states = deps.downloads.active_states()
        await _monitor(deps, states)
    except MuleUnreachableError as error:
        _logger.warning("download daemon unreachable (%s) — iteration skipped, retry", error)
        return
    except RepositoryError as error:
        _logger.error("download monitor repo failure (%s) — step skipped, continues", error)
    # Step 2 — COMPLETIONS: we RE-READ ``active_states`` FRESHLY (logic-download#2). Without it,
    # a failure in step 1 left ``states`` frozen/empty → every shared hash → ``states.get is
    # None`` → ignored → NO completion promoted the entire cycle. The re-read is also better
    # aligned than ``states={}`` with the nominal case (fresh states), at the cost of one extra
    # repo call (idempotent). A failure of the re-read itself is caught downstream.
    # Steps 2 & 3 — NO client I/O → only RepositoryError possible, ISOLATED per step (I2):
    # a repo failure in one must NOT prevent the other from running.
    try:
        fresh_states = deps.downloads.active_states()
        await _handle_completions(deps, fresh_states)
    except MuleUnreachableError as error:
        _logger.warning("download daemon unreachable (%s) — iteration skipped, retry", error)
        return
    except RepositoryError as error:
        _logger.error("download completions repo failure (%s) — step skipped, continues", error)
    try:
        await _queue_new_candidates(deps)
    except RepositoryError as error:
        _logger.error("download candidates repo failure (%s) — step skipped, continues", error)
    # Step 4 — ADD_LINKS: client I/O → MuleUnreachableError = dead daemon = ABORT. Re-reads
    # ``active_states`` FRESHLY, so it runs even if step 3 partially failed.
    try:
        await _add_links(deps)
    except MuleUnreachableError as error:
        _logger.warning("download daemon unreachable (%s) — iteration skipped, retry", error)
    except RepositoryError as error:
        _logger.error("add_link download repo failure (%s) — step skipped, retry", error)


async def _sleep_or_nudge(deps: DownloadLoopDeps) -> None:
    """Waits ``poll_interval`` OR the ``download`` nudge, whichever comes FIRST (spec §5).

    ``asyncio.wait(FIRST_COMPLETED)`` then cancel the loser: a decision change (nudge) wakes the
    loop immediately; otherwise the fallback poll wakes it at the cadence.
    Shutdown cancellation lands HERE (an ``await``), never mid DB write (sync).
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
    """Repeats ``run_download_cycle`` then waits (poll/nudge) until shutdown (DECISION D12).

    Wired by ``CrawlerApp`` (D-verify) into the ``TaskGroup``; cancellation (shutdown) lands
    at the next ``await`` (EC poll or sleep/nudge wait), never mid DB write.
    """
    while not deps.shutdown.is_set():
        await run_download_cycle(deps)
        if deps.shutdown.is_set():
            break
        await _sleep_or_nudge(deps)
