"""The download loop: monitor → completions → new candidates → sleep/nudge (§5).

APPLICATION layer. A SINGLE task, serial, on the sole download EC connection (spec §3/§5):
no frame interleaving. ``run_download_cycle`` runs ONE iteration (testable without a
shutdown event); ``download_loop`` repeats it then waits ``poll_interval`` OR the nudge
(``DecisionSignal``), until a shutdown event - wired by ``CrawlerApp``.

Flow of one iteration (spec §5, DECISION D8):
  0. CONNECT + QUEUE SNAPSHOT: ``connect()`` (idempotent, and the ONLY thing that re-arms a
     transport the adapter threw away after a dead EC stream) then ONE ``download_queue()`` read,
     shared by steps 1 and 2.
  1. MONITOR: for each queue entry KNOWN to ``downloads``, reconciles ``downloading``
     (QUEUED→DOWNLOADING); an unknown entry (download outside the crawler) is ignored.
     Completion is NO LONGER inferred from bytes (see ``_monitor``).
  2. COMPLETIONS: ``shared_files()`` → each tracked hash present in amuled's SHARED files AND
     absent from the queue (amuled shares PARTIAL downloads too, so the queue is what separates a
     completion from a partial, see ``_handle_completions``) → ``set_state(completed)``. The file
     stays where amuled put it: nothing moves it, nothing reads it. Already ``completed`` →
     skipped, so the completion notification fires once.
  2b. PRESENCE + TTL: every hash amuled still knows (queue OR shared files) gets its
     ``last_seen_at`` stamped, THEN a ``queued``/``downloading`` row unseen for
     ``lost_after_seconds`` becomes ``failed`` (see ``_expire_lost``). Stamping first is what
     keeps a row present right now from being condemned.
  3. CANDIDATES: ``catalog.download_decisions()`` (latest=download) ∖ ``downloads`` → for
     each, ``download_policy`` (target status, dedup, free space) → if ``download``:
     ``build_ed2k_link`` (from ``last_observation``) → ``add_link`` → ``record_queued``.
     ``outstanding`` is carried IN MEMORY as the cycle proceeds (a candidate admitted now is
     not in amuled's queue yet).

Errors (Plan C contracts, spec §9): ``MuleUnreachableError`` (EC stream dead) → tolerate, skip
the iteration. ``OSError`` on the disk measurement (output mount gone) → tolerate, admit
nothing. Step 0 re-arms the connection on EVERY iteration (``connect()`` is idempotent):
that is what makes "the client reconnects next round" true. Without it the loop stayed wedged on
"EC client not connected (call connect() first)" forever after any amuled restart (field,
2026-09-04 to 09-11: 7 days of a dead download loop, one warning per 30 s).
``RepositoryError`` → absorbed (log + continue).
NEVER abandon a stalled download. Determinism: ``Clock``/``sleep`` injected.
"""

import asyncio
import logging
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from catalog_matching.ed2k_link import build_ed2k_link
from catalog_matching.engine import DownloadCandidate
from catalog_matching.models import TargetSegment
from mulewatch.domain.download.policy import DownloadVerdict, download_policy
from mulewatch.domain.download.states import DownloadState
from mulewatch.domain.observability.events import DownloadCompleted, DownloadQueued
from mulewatch.ports.catalog_repository import ObservedFile
from mulewatch.ports.clock import Clock
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.disk_space import DiskSpace
from mulewatch.ports.mule_client import MuleSearchFailedError, MuleUnreachableError
from mulewatch.ports.mule_download_client import (
    DownloadEntry,
    MuleDownloadClient,
    SharedFileEntry,
)
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.telemetry import Telemetry

_logger = logging.getLogger("mulewatch.application.run_download_cycle")

# Conventional subject of the download nudge (DECISION D13). D-download subscribes to THIS
# subject; the producer side (the pipeline) calls signal("download").
DOWNLOAD_NUDGE_SUBJECT = "download"


class DownloadRepository(Protocol):
    """STRUCTURAL Protocol of the downloads repo (local typing; the adapter satisfies it).

    Minimal Protocol so the application depends ONLY on what it needs
    (record_queued/set_state/is_downloaded/mark_seen/expire_lost/active_states), without
    importing the adapter. The real ``SqliteDownloadRepository`` (and the test fake) satisfies it
    structurally. Stubs on ONE line (the ``def`` is covered when the class is created).
    """

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool: ...

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None: ...

    def is_downloaded(self, ed2k_hash: str) -> bool: ...

    def mark_seen(self, ed2k_hashes: Iterable[str]) -> None: ...

    def expire_lost(self, max_age_seconds: float) -> tuple[str, ...]: ...

    def active_states(self) -> dict[str, DownloadState]: ...

    def get_target_id(self, ed2k_hash: str) -> str | None: ...


class CatalogReader(Protocol):
    """STRUCTURAL Protocol of the catalog READS the loop needs (DECISION D9).

    Subset of ``CatalogRepository`` (download_decisions + last_observation): the loop
    depends ONLY on what it reads, so the minimal test fake satisfies it without implementing
    record_observation/record_decision. The real ``SqliteCatalogRepository``
    satisfies it too (it has these two methods). Stubs on ONE line.
    """

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None: ...


@dataclass
class DownloadDeps:
    """Dependencies of the download loop (composition assembles them once).

    ``targets`` serves the ``target_id → status`` lookup (pure policy). ``disk`` measures the
    free space of the filesystem amuled writes to (metadata only, no file is opened).
    ``catalog`` is typed to
    the NARROW ``CatalogReader`` Protocol above: the loop depends only on the subset it reads
    (consistent with the local ``DownloadRepository`` Protocol), so the minimal test fakes are
    accepted.
    """

    client: MuleDownloadClient
    downloads: DownloadRepository
    catalog: CatalogReader
    targets: Sequence[TargetSegment]
    disk: DiskSpace
    min_free_bytes: int
    lost_after_seconds: float
    clock: Clock
    telemetry: Telemetry


@dataclass
class DownloadLoopDeps(DownloadDeps):
    """``DownloadDeps`` + what it takes to REPEAT (nudge, cadence, shutdown) - DECISION D12."""

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


async def _monitor(
    deps: DownloadDeps, states: dict[str, DownloadState], queue: tuple[DownloadEntry, ...]
) -> None:
    """Reconciles ``downloads`` with the amuled queue: QUEUED→DOWNLOADING (step 1, spec §5).

    ``queue`` is the cycle's ONE snapshot (step 0), shared with ``_handle_completions``: the
    completion rule needs it, and a second read could contradict the first.

    Completion is NO LONGER inferred from bytes (PS_COMPLETE is unobservable via the queue - cf.
    docs/reference/2026-06-17-amuled-completion-behavior.md): it comes from the shared files
    (_handle_completions). Here we only record that amuled is pulling a queued download.

    ``FAILED`` is NOT a wall here: amuled is the authority on what it holds, so a row the TTL
    condemned (or one whose add_link amuled rejected) that reappears in the queue resumes.
    ``COMPLETED`` stays a wall, so its notification never fires twice.
    """
    for entry in queue:
        current = states.get(entry.ed2k_hash)
        if current is None:
            continue  # download outside the crawler: ignored
        if current is DownloadState.COMPLETED:
            continue  # already notified: don't regress and don't re-fire
        if current is not DownloadState.DOWNLOADING:
            deps.downloads.set_state(entry.ed2k_hash, DownloadState.DOWNLOADING)
            states[entry.ed2k_hash] = DownloadState.DOWNLOADING


async def _record_completion(
    deps: DownloadDeps, ed2k_hash: str, states: dict[str, DownloadState]
) -> None:
    """Marks ``completed`` (stamps completed_at) and notifies (step 2, §5).

    ``completed`` is terminal: the file stays in amuled's IncomingDir, nothing moves it and
    nothing opens it. The caller skips hashes already ``completed``, so the notification fires
    exactly once per download.
    """
    deps.downloads.set_state(ed2k_hash, DownloadState.COMPLETED)
    states[ed2k_hash] = DownloadState.COMPLETED
    target_id = deps.downloads.get_target_id(ed2k_hash) or "unknown"
    await deps.telemetry.emit(DownloadCompleted(target_id=target_id, ed2k_hash=ed2k_hash))
    _logger.info("hash=%s completed", ed2k_hash)


async def _handle_completions(
    deps: DownloadDeps,
    states: dict[str, DownloadState],
    queued: frozenset[str],
    shared: tuple[SharedFileEntry, ...],
) -> None:
    """Completes each tracked hash that is SHARED **and** gone from the download queue (step 2, §5).

    Presence in the shared files ALONE is not a completion: amuled shares PARTIAL downloads too
    (standard eMule: you upload what you have downloaded). The discriminator is the queue, which
    a finished file leaves (its entry goes away when it reaches ``PS_COMPLETE``), while a running
    one stays in it. Field 2026-09-02: without the queue check the crawler stamped 065B
    ``completed`` at 20.1 %. ``completed`` hashes are ignored; a ``failed`` one is NOT, so a
    download the TTL condemned that turns out to be shared completes and notifies.

    The queue was read in step 0, BEFORE this shared snapshot: a file completing in between is
    therefore seen as "still queued" and completed on the NEXT cycle (30 s later). The shared
    signal persists, so the delay is harmless, and the inverse order would be the unsafe one.

    PER-HASH isolation (error-boundary#2): a ``RepositoryError`` on one hash is logged and
    CONTINUES with the next ones. Without this net, a repo failure on hash N would abandon
    N+1, N+2 of the same cycle (the completion signal is re-evaluated the next cycle; no
    permanent loss, but intra-cycle starvation is undesirable).
    """
    for entry in shared:
        current = states.get(entry.ed2k_hash)
        if current is None:
            continue  # shared file outside the crawler: ignored
        if current is DownloadState.COMPLETED:
            continue  # already completed: the notification fired once
        if entry.ed2k_hash in queued:
            continue  # still downloading (partial): NOT a completion
        try:
            await _record_completion(deps, entry.ed2k_hash, states)
        except RepositoryError as error:
            _logger.error(
                "completion hash=%s repo failure (%s): hash skipped, continues",
                entry.ed2k_hash,
                error,
            )


def _expire_lost(deps: DownloadDeps) -> None:
    """Fails the downloads amuled has not shown for ``lost_after_seconds`` (spec §2).

    An entry stays in amuled's queue even with zero sources, so absence is a rare and strong
    signal: the entry was removed, or the file completed and was moved out of IncomingDir
    before the next poll. ``is_downloaded`` stays state-blind, so a ``failed`` row still blocks
    automatic re-queuing; the manual retry is deleting the row.
    """
    for ed2k_hash in deps.downloads.expire_lost(deps.lost_after_seconds):
        _logger.warning(
            "hash=%s unseen by amuled for %ss: marked failed",
            ed2k_hash,
            deps.lost_after_seconds,
        )


async def _queue_new_candidates(deps: DownloadDeps, outstanding: int) -> None:
    """Replays tier=download decisions missing from ``downloads`` (step 3, spec §5).

    ``outstanding`` is what amuled's queue still has to transfer, measured from the cycle's
    snapshot; ``free`` is read once here. Both are MEASURED, never declared.
    """
    free = deps.disk.free_bytes()
    for candidate in deps.catalog.download_decisions():
        if deps.downloads.is_downloaded(candidate.ed2k_hash):
            continue
        observation = deps.catalog.last_observation(candidate.ed2k_hash)
        if observation is None:
            _logger.warning(
                "candidate hash=%s without observation: link impossible, skipped",
                candidate.ed2k_hash,
            )
            continue
        verdict = download_policy(
            tier="download",
            target_status=_target_status(deps.targets, candidate.target_id),
            already_downloaded=False,
            free_bytes=free,
            outstanding_bytes=outstanding,
            file_size=observation.size_bytes,
            min_free_bytes=deps.min_free_bytes,
        )
        if verdict is not DownloadVerdict.DOWNLOAD:
            _logger.info(
                "candidate hash=%s → %s (skipped/deferred)", candidate.ed2k_hash, verdict.value
            )
            continue
        # record_queued ONLY here (sync DB write); the ed2k link is built and emitted by
        # _add_links (network I/O) for every 'queued' - the write precedes the network, and an
        # add_link that raises leaves the download 'queued' in the DB (caught up next round).
        deps.downloads.record_queued(
            candidate.ed2k_hash, candidate.target_id, observation.size_bytes
        )
        outstanding += observation.size_bytes  # not in amuled's queue yet: carried in memory
        _logger.info("candidate hash=%s queued for download", candidate.ed2k_hash)
        await deps.telemetry.emit(DownloadQueued(target_id=candidate.target_id))


async def _add_links(deps: DownloadDeps) -> None:
    """Emits the EC ``add_link`` calls for ``queued`` downloads with no link sent yet.

    Split from ``_queue_new_candidates`` so the (sync) DB write precedes the (async) network
    I/O: a ``MuleUnreachableError`` at ``add_link`` leaves the download ``queued`` in the DB
    (the next round's monitor catches up). We re-emit the link for every known ``queued``.

    Two ``add_link`` failures to distinguish (spec §9):
      - ``MuleSearchFailedError`` (the daemon answered ``EC_OP_FAILED`` - link explicitly
        REJECTED): we mark THIS hash ``failed`` (log + ``set_state``) and ``continue`` to the
        next. Retrying would only re-emit the same rejected link in a loop.
      - ``MuleUnreachableError`` (EC stream dead): we let it PROPAGATE - the top capture of
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
                "add_link rejected by amuled for hash=%s (%s): marked failed", ed2k_hash, error
            )


async def run_download_cycle(deps: DownloadDeps) -> None:
    """ONE iteration of the download loop (spec §5). Never raises: tolerates/absorbs.

    Two distinct error DOCTRINES (item I2 - anti-starvation):

    - ``MuleUnreachableError`` (EC stream dead, from step 0, ``_handle_completions`` or
      ``_add_links``) = dead daemon → ABORT the iteration ("a dead daemon makes everything
      fail", cf. ``_add_links``). We skip the rest; the next iteration retries (amuled persists
      the downloads).
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
    # Step 0 - CONNECT + QUEUE SNAPSHOT: client I/O → MuleUnreachableError = dead daemon = ABORT.
    # ``connect()`` is IDEMPOTENT (the adapter no-ops when its transport is live) and is the ONLY
    # thing that re-arms a stream the adapter discarded after a failed read. SKIPPING IT WEDGES
    # THE LOOP: amuled is restarted by the port-sync on every VPN renegotiation, and nothing else
    # in this loop ever reconnects (field, 2026-09-04: 7 days of "EC client not connected").
    # Same guard as ``SearchWorker._ensure_connected`` and ``run_port_sync_cycle``.
    try:
        await deps.client.connect()
        queue = await deps.client.download_queue()
    except MuleUnreachableError as error:
        _logger.warning("download daemon unreachable (%s): iteration skipped, retry", error)
        return
    queued = frozenset(entry.ed2k_hash for entry in queue)
    outstanding = sum(entry.remaining_bytes for entry in queue)
    # Step 1 - MONITOR: NO client I/O left (the queue came from step 0) → only RepositoryError.
    # Steps 1 and 2 share that ONE snapshot: a second read could only contradict the first.
    try:
        states = deps.downloads.active_states()
        await _monitor(deps, states, queue)
    except RepositoryError as error:
        _logger.error("download monitor repo failure (%s): step skipped, continues", error)
    # Step 2 - COMPLETIONS: we RE-READ ``active_states`` FRESHLY (logic-download#2). Without it,
    # a failure in step 1 left ``states`` frozen/empty → every shared hash → ``states.get is
    # None`` → ignored → NO completion recorded the entire cycle. The re-read is also better
    # aligned than ``states={}`` with the nominal case (fresh states), at the cost of one extra
    # repo call (idempotent). A failure of the re-read itself is caught downstream.
    # Steps 2 & 3 - NO client I/O → only RepositoryError possible, ISOLATED per step (I2):
    # a repo failure in one must NOT prevent the other from running.
    try:
        shared = await deps.client.shared_files()
    except MuleUnreachableError as error:
        _logger.warning("download daemon unreachable (%s): iteration skipped, retry", error)
        return
    try:
        fresh_states = deps.downloads.active_states()
        await _handle_completions(deps, fresh_states, queued, shared)
    except RepositoryError as error:
        _logger.error("download completions repo failure (%s): step skipped, continues", error)
    # Step 2b - PRESENCE + TTL: stamp FIRST, condemn after. The reverse order would fail a row
    # amuled is showing us right now.
    try:
        deps.downloads.mark_seen(queued | {entry.ed2k_hash for entry in shared})
        _expire_lost(deps)
    except RepositoryError as error:
        _logger.error("download presence repo failure (%s): step skipped, continues", error)
    try:
        await _queue_new_candidates(deps, outstanding)
    except RepositoryError as error:
        _logger.error("download candidates repo failure (%s): step skipped, continues", error)
    except OSError as error:
        # A missing or unreadable output mount: refuse to admit anything rather than take down
        # the crawler, which also catalogues. Conservative, loud, and retried next cycle.
        _logger.error("output directory unmeasurable (%s): no candidate admitted, retry", error)
    # Step 4 - ADD_LINKS: client I/O → MuleUnreachableError = dead daemon = ABORT. Re-reads
    # ``active_states`` FRESHLY, so it runs even if step 3 partially failed.
    try:
        await _add_links(deps)
    except MuleUnreachableError as error:
        _logger.warning("download daemon unreachable (%s): iteration skipped, retry", error)
    except RepositoryError as error:
        _logger.error("add_link download repo failure (%s): step skipped, retry", error)


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

    Wired by ``CrawlerApp`` into the ``TaskGroup``; cancellation (shutdown) lands
    at the next ``await`` (EC poll or sleep/nudge wait), never mid DB write.
    """
    while not deps.shutdown.is_set():
        await run_download_cycle(deps)
        if deps.shutdown.is_set():
            break
        await _sleep_or_nudge(deps)
