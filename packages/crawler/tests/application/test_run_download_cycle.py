import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from catalog_matching.engine import DownloadCandidate
from catalog_matching.models import TargetSegment
from emule_indexer.application.run_download_cycle import DownloadDeps, run_download_cycle
from emule_indexer.domain.download.states import DownloadState
from emule_indexer.domain.observability.events import DownloadCompleted, PromotionFailed
from emule_indexer.ports.catalog_repository import ObservedFile
from emule_indexer.ports.mule_client import (
    KadStatus,
    MuleSearchFailedError,
    MuleUnreachableError,
    NetworkStatus,
)
from emule_indexer.ports.mule_download_client import DownloadEntry, SharedFileEntry
from emule_indexer.ports.repository_errors import RepositoryError
from tests.application.fakes import RecordingTelemetry

_A = "a" * 32
_B = "b" * 32

_TARGETS = (
    TargetSegment(
        season=2, seasonal_number=11, absolute_number=62, segment="A", title="t", status="lost"
    ),
    TargetSegment(
        season=2,
        seasonal_number=63,
        absolute_number=63,
        segment="A",
        title="t2",
        status="complete",
    ),
)


class FakeDownloadClient:
    """Scripted MuleDownloadClient: SCRIPTED download queue, captures added links."""

    def __init__(
        self,
        *,
        queue: list[tuple[DownloadEntry, ...]] | None = None,
        shared: list[tuple[SharedFileEntry, ...]] | None = None,
        connect_failures: list[Exception] | None = None,
        queue_failures: list[Exception] | None = None,
        add_failures: list[Exception] | None = None,
        shared_failures: list[Exception] | None = None,
    ) -> None:
        self._queue = list(queue or [()])
        self._shared = list(shared or [()])
        self._connect_failures = list(connect_failures or [])
        self._queue_failures = list(queue_failures or [])
        self._add_failures = list(add_failures or [])
        self._shared_failures = list(shared_failures or [])
        self.added_links: list[str] = []
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_failures:
            raise self._connect_failures.pop(0)

    async def close(self) -> None:
        return None

    async def add_link(self, ed2k_link: str) -> None:
        if self._add_failures:
            raise self._add_failures.pop(0)
        self.added_links.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        if self._queue_failures:
            raise self._queue_failures.pop(0)
        return self._queue.pop(0) if self._queue else ()

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        if self._shared_failures:
            raise self._shared_failures.pop(0)
        return self._shared.pop(0) if self._shared else ()

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


class FakeQuarantine:
    """Fake Quarantine FAITHFUL to the contract: ``promote`` does an ``os.replace`` that CONSUMES
    the source. A re-promote of the same hash (source already consumed, target already in place)
    reproduces the behavior of the real ``FilesystemQuarantine.promote`` — see that branch.
    ``fail_for`` simulates an FS failure (``OSError``) on the first promote."""

    def __init__(self, *, fail_for: set[str] | None = None) -> None:
        self.promoted: list[tuple[Path, str]] = []
        self._fail_for = fail_for or set()
        self._consumed: set[str] = set()

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        if ed2k_hash in self._fail_for:
            raise OSError("rename impossible")
        if ed2k_hash in self._consumed:
            # source already consumed by an earlier promotion (target quarantine/<hash> in
            # place): the real FilesystemQuarantine.promote is idempotent → no-op success.
            return
        self._consumed.add(ed2k_hash)
        self.promoted.append((staging_path, ed2k_hash))


class FakeDownloadRepo:
    """In-memory downloads repo (the contract of SqliteDownloadRepository, without SQL).

    ``fail_set_state_for``: hashes for which ``set_state`` raises ``RepositoryError`` —
    lets us simulate a mid-cycle repo failure (cf. logic-download#2/error-boundary#2).
    ``fail_active_states``: ``active_states()`` raises — lets us simulate persistence being down
    also at re-read time (every step of the cycle absorbs)."""

    def __init__(
        self,
        *,
        fail_record: bool = False,
        fail_set_state_for: set[str] | None = None,
        fail_active_states: bool = False,
    ) -> None:
        self.states: dict[str, DownloadState] = {}
        self.sizes: dict[str, int] = {}
        self._fail_record = fail_record
        self._fail_set_state_for = fail_set_state_for or set()
        self._fail_active_states = fail_active_states
        self._target_ids: dict[str, str] = {}

    def get_target_id(self, ed2k_hash: str) -> str | None:
        return self._target_ids.get(ed2k_hash)

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool:
        if self._fail_record:
            raise RepositoryError("downloads write failed")
        if ed2k_hash in self.states:
            return False
        self.states[ed2k_hash] = DownloadState.QUEUED
        self.sizes[ed2k_hash] = size_bytes
        return True

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
        if ed2k_hash in self._fail_set_state_for:
            raise RepositoryError(f"set_state({ed2k_hash}) failed")
        self.states[ed2k_hash] = state

    def is_downloaded(self, ed2k_hash: str) -> bool:
        return ed2k_hash in self.states

    def committed_bytes(self) -> int:
        return sum(
            self.sizes.get(h, 0)
            for h, s in self.states.items()
            if s in {DownloadState.QUEUED, DownloadState.DOWNLOADING}
        )

    def active_states(self) -> dict[str, DownloadState]:
        if self._fail_active_states:
            raise RepositoryError("active_states failed")
        return dict(self.states)


class FakeCatalogReads:
    """Catalog read side: download_decisions + last_observation scripted."""

    def __init__(
        self,
        *,
        candidates: tuple[DownloadCandidate, ...] = (),
        observations: dict[str, ObservedFile] | None = None,
    ) -> None:
        self._candidates = candidates
        self._observations = observations or {}

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return self._candidates

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return self._observations.get(ed2k_hash)


class FakeLocalRepo:
    """enqueue_verification (idempotent) captured; ``fail_enqueue`` raises ``RepositoryError``."""

    def __init__(self, *, fail_enqueue: bool = False) -> None:
        self.enqueued: list[str] = []
        self._fail_enqueue = fail_enqueue

    def enqueue_verification(self, ed2k_hash: str) -> bool:
        if self._fail_enqueue:
            raise RepositoryError("enqueue_verification failed")
        first = ed2k_hash not in self.enqueued
        self.enqueued.append(ed2k_hash)
        return first


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


def _candidate(hash_hex: str, target_id: str) -> DownloadCandidate:
    return DownloadCandidate(ed2k_hash=hash_hex, target_id=target_id)


def _deps(
    *,
    client: FakeDownloadClient,
    quarantine: FakeQuarantine,
    downloads: FakeDownloadRepo,
    catalog: FakeCatalogReads,
    local: FakeLocalRepo,
    disk_cap: int = 1_000_000,
    telemetry: RecordingTelemetry | None = None,
) -> DownloadDeps:
    return DownloadDeps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
        targets=_TARGETS,
        disk_cap_bytes=disk_cap,
        staging_dir=Path("/staging"),
        clock=FakeClock(),
        telemetry=telemetry or RecordingTelemetry(),
    )


@pytest.mark.asyncio
async def test_new_candidate_is_queued_and_link_added() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="Keroro.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.QUEUED
    assert len(client.added_links) == 1
    assert _A in client.added_links[0]


@pytest.mark.asyncio
async def test_already_downloaded_candidate_is_deduped() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING  # already known
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []  # dedup: no new link


@pytest.mark.asyncio
async def test_complete_target_candidate_is_skipped() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E063A"),),  # S2E063A status=complete
        observations={_B: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _B not in downloads.states


@pytest.mark.asyncio
async def test_disk_cap_defers_candidate() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=500)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
        disk_cap=100,  # 500 > 100 → defers
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_candidate_without_observation_is_skipped() -> None:
    # a candidate for which no observation survived (edge case) cannot build a link:
    # we skip it (log), never a crash.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(candidates=(_candidate(_A, "S2E062A"),), observations={})
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_marks_in_progress_when_not_complete() -> None:
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_monitor_does_not_regress_terminal_or_completed_queue_entry() -> None:
    # _monitor: a tracked hash that is TERMINAL/already completed (quarantined/failed/completed)
    # present in the amuled queue MUST NOT regress to DOWNLOADING (monitor's skip branch).
    class _NoSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise AssertionError("set_state must not be called (terminal/completed state)")

    for terminal in (
        DownloadState.QUARANTINED,
        DownloadState.FAILED,
        DownloadState.COMPLETED,
    ):
        client = FakeDownloadClient(
            queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)]
        )
        repo = _NoSetStateRepo()
        repo.states[_A] = terminal
        deps = _deps(
            client=client,
            quarantine=FakeQuarantine(),
            downloads=repo,
            catalog=FakeCatalogReads(),
            local=FakeLocalRepo(),
        )
        await run_download_cycle(deps)
        assert repo.states[_A] is terminal  # unchanged


@pytest.mark.asyncio
async def test_monitor_ignores_unknown_queue_entries() -> None:
    # an entry in the amuled queue but unknown to downloads (started outside crawler) is ignored.
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_B, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert _B not in downloads.states


@pytest.mark.asyncio
async def test_promote_failure_keeps_completed_and_does_not_enqueue() -> None:
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine(fail_for={_A})
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED  # stays completed (retry)
    assert local.enqueued == []  # does NOT enqueue


@pytest.mark.asyncio
async def test_unreachable_client_is_tolerated_and_iteration_skipped() -> None:
    client = FakeDownloadClient(queue_failures=[MuleUnreachableError("daemon down")])
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(candidates=(_candidate(_A, "S2E062A"),)),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # does not raise
    assert client.added_links == []  # iteration skipped (no candidates processed)


@pytest.mark.asyncio
async def test_repository_error_is_absorbed() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(fail_record=True)  # record_queued raises RepositoryError
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # does not raise (RepositoryError absorbed)


@pytest.mark.asyncio
async def test_monitor_repo_error_still_promotes_completions_in_same_cycle() -> None:
    # Regression logic-download#2: if ``_monitor`` raises ``RepositoryError`` (set_state down on
    # another hash), the old code set ``states={}`` then called ``_handle_completions``
    # → each shared hash → ``states.get(...) is None`` → ignored → NO completion promoted
    # in the whole cycle (latency +1 cycle although we already have the signal). The fix re-reads
    # ``active_states()`` BEFORE ``_handle_completions`` so the completions are seen.
    client = FakeDownloadClient(
        queue=[
            (
                DownloadEntry(ed2k_hash=_A, size_done=0, size_full=0),
                DownloadEntry(ed2k_hash=_B, size_done=0, size_full=0),
            )
        ],
        shared=[(SharedFileEntry(ed2k_hash=_A, name="keroro_062a.avi"),)],
    )
    downloads = FakeDownloadRepo(fail_set_state_for={_B})
    # _A already DOWNLOADING (no transition by _monitor); _B QUEUED → _monitor will try
    # set_state(_B, DOWNLOADING) which raises → step 1 crashes, but _A is complete in shared.
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.states[_B] = DownloadState.QUEUED
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert local.enqueued == [_A]  # _A's completion is promoted despite the failure of _monitor
    assert downloads.states[_A] is DownloadState.QUARANTINED


@pytest.mark.asyncio
async def test_active_states_repo_failure_is_absorbed_at_step_2() -> None:
    # ``active_states`` that raises IN A LOOP (persistent repo failure): step 1 absorbs, then the
    # re-read of step 2 (logic-download#2) absorbs in turn → the cycle ends without
    # crashing the loop (the next cycle will replay). We exercise HERE the branch
    # ``except RepositoryError`` of step 2 separately from that of step 1.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(fail_active_states=True)
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # does not raise (both RepositoryError are absorbed)


@pytest.mark.asyncio
async def test_one_hash_repo_failure_does_not_starve_other_completions() -> None:
    # Regression error-boundary#2: a ``RepositoryError`` in ``_promote_completion`` of hash N
    # used to bubble up to the cycle handler, abandoning N+1, N+2 of the same shared_files. The fix
    # isolates PER HASH (try/except around _promote_completion), honoring the "isolated per
    # step" intent of the comment (I2).
    client = FakeDownloadClient(
        shared=[
            (
                SharedFileEntry(ed2k_hash=_A, name="a.avi"),
                SharedFileEntry(ed2k_hash=_B, name="b.avi"),
            )
        ],
    )
    # _A and _B in DOWNLOADING; set_state(_A, ...) crashes → _promote_completion of _A raises;
    # _B must nonetheless be promoted (intra-cycle continuity).
    downloads = FakeDownloadRepo(fail_set_state_for={_A})
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.states[_B] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert _B in local.enqueued  # _B is promoted despite the failure on _A
    assert downloads.states[_B] is DownloadState.QUARANTINED


@pytest.mark.asyncio
async def test_intra_cycle_disk_cap_accounts_for_links_added_this_cycle() -> None:
    # two candidates of 600 bytes, cap 1000: the 1st passes (600 ≤ 1000), the 2nd defers
    # (600 + 600 > 1000) — the committed is recalculated IN MEMORY over the course of the cycle.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"), _candidate(_B, "S2E062A")),
        observations={
            _A: ObservedFile(filename="a", size_bytes=600),
            _B: ObservedFile(filename="b", size_bytes=600),
        },
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
        disk_cap=1000,
    )
    await run_download_cycle(deps)
    assert len(client.added_links) == 1  # only one fit within the cap


@pytest.mark.asyncio
async def test_candidate_for_unknown_target_is_treated_as_complete() -> None:
    # _target_status: a candidate whose target_id is ABSENT from _TARGETS → "complete"
    # (conservative) → SKIP_COMPLETE policy → no link, hash not enqueued.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S9E999Z"),),  # ghost target, absent from _TARGETS
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_monitor_no_op_when_state_already_matches() -> None:
    # _monitor: in-progress entry (done=3/full=10) and repo already DOWNLOADING → target == current
    # → NO set_state (FALSE branch of `if target != current`).
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING

    class _NoSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise AssertionError("set_state must not be called (state already up to date)")

    repo = _NoSetStateRepo()
    repo.states[_A] = DownloadState.DOWNLOADING
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=repo,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert repo.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_queued_download_without_observation_emits_no_link() -> None:
    # _add_links: a QUEUED download in the DB but without observation in the catalog → no link
    # (branch `if observation is None: continue`).
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    downloads.sizes[_A] = 100
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(observations={}),  # no observation
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_unreachable_keeps_queued_and_is_tolerated() -> None:
    # add_link raises MuleUnreachableError → tolerated at cycle level; the download stays QUEUED
    # (record_queued already happened) → caught up next round. write-before-network invariant.
    client = FakeDownloadClient(add_failures=[MuleUnreachableError("down")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # does not raise
    assert downloads.states[_A] is DownloadState.QUEUED  # stays queued → caught up
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_rejected_marks_failed_and_does_not_crash() -> None:
    # add_link raises MuleSearchFailedError (the daemon replied EC_OP_FAILED — link rejected):
    # THIS hash is marked FAILED (spec §9 "failed + log"), the loop continues, does not raise.
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # does not raise (application failure tolerated per hash)
    assert downloads.states[_A] is DownloadState.FAILED  # link rejected → marked failed
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_rejected_for_one_hash_does_not_block_the_next() -> None:
    # add_link rejected (EC_OP_FAILED) for _A, accepted for _B: _A → FAILED, _B → link emitted and
    # stays QUEUED. The break does not abort the loop (continues to the next hash).
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"), _candidate(_B, "S2E062A")),
        observations={
            _A: ObservedFile(filename="a", size_bytes=1),
            _B: ObservedFile(filename="b", size_bytes=1),
        },
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.FAILED  # rejected
    assert downloads.states[_B] is DownloadState.QUEUED  # accepted (link emitted)
    assert any(_B in link for link in client.added_links)
    assert all(_A not in link for link in client.added_links)


@pytest.mark.asyncio
async def test_completion_and_new_candidate_in_the_same_cycle() -> None:
    # _A completed via the SHARED files (promoted this cycle); _B is a new candidate
    # (enqueued + link).
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="a.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.sizes[_A] = 10
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.QUARANTINED  # completed → promoted + enqueued
    assert local.enqueued == [_A]
    assert downloads.states[_B] is DownloadState.QUEUED  # new → enqueued
    assert any(_B in link for link in client.added_links)  # + link emitted


@pytest.mark.asyncio
async def test_emits_download_queued() -> None:
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="Keroro.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert any(type(e).__name__ == "DownloadQueued" for e in telemetry.events)


@pytest.mark.asyncio
async def test_emits_download_completed_on_promotion() -> None:
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads._target_ids[_A] = "S2E062A"
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert any(
        isinstance(e, DownloadCompleted) and e.target_id == "S2E062A" for e in telemetry.events
    )


@pytest.mark.asyncio
async def test_emits_promotion_failed() -> None:
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine(fail_for={_A})  # promote raises → PromotionFailed
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert any(type(e).__name__ == "PromotionFailed" for e in telemetry.events)


# ---------------------------------------------------------------------------
# I2 — per-STEP error granularity (anti-starvation): a RepositoryError in one
# step (completions / new candidates) must NOT prevent the OTHER from running.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_repo_failure_does_not_starve_new_candidates() -> None:
    # _handle_completions raises RepositoryError (enqueue_verification fails on the shared hash
    # _A) → _queue_new_candidates AND _add_links run ANYWAY for _B:
    # a step-2 repo failure does not starve step 3 (anti-starvation, I2).
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="a.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING  # shared → promoted step 2 (enqueue raises)
    downloads.sizes[_A] = 10
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),  # new candidate step 3
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    local = FakeLocalRepo(fail_enqueue=True)  # step 2 raises RepositoryError
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=local,
    )
    await run_download_cycle(deps)  # does not raise
    # Observable effect of step 3: _B enqueued AND its link emitted despite the step-2 failure.
    assert downloads.states[_B] is DownloadState.QUEUED
    assert any(_B in link for link in client.added_links)
    # _A stays COMPLETED (the enqueue failure left step 2 incomplete → retry next round).
    assert downloads.states[_A] is DownloadState.COMPLETED


@pytest.mark.asyncio
async def test_candidate_repo_failure_does_not_starve_completions() -> None:
    # Symmetric: _queue_new_candidates raises RepositoryError (record_queued fails) → the
    # step-2 completions were promoted ANYWAY (observable effect). The failure of
    # step 3 does not starve step 2.
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="a.avi"),)])
    downloads = FakeDownloadRepo(fail_record=True)  # step 3 raises RepositoryError
    downloads.states[_A] = DownloadState.DOWNLOADING  # _A shared → promoted step 2
    downloads.sizes[_A] = 10
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),  # new candidate → record_queued will raise
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
    )
    await run_download_cycle(deps)  # does not raise
    # Observable effect of step 2: _A promoted + enqueued despite the step-3 failure.
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert local.enqueued == [_A]
    # _B was NOT enqueued (record_queued raised) → no link emitted for it.
    assert _B not in downloads.states
    assert client.added_links == []


@pytest.mark.asyncio
async def test_completion_recovers_after_transient_enqueue_failure() -> None:
    # Regression logic-download#0: a TRANSIENT enqueue_verification failure AFTER a successful
    # promote (source already consumed by os.replace) must NOT block the file forever.
    # Next cycle, enqueue restored + idempotent promote → the file ends up QUARANTINED +
    # enqueued, instead of looping forever on PromotionFailed (consumed source not found).
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()  # SHARED across cycles: models source consumption
    shared = (SharedFileEntry(ed2k_hash=_A, name="a.avi"),)

    # Cycle 1: promote succeeds (source consumed) then enqueue raises RepositoryError.
    await run_download_cycle(
        _deps(
            client=FakeDownloadClient(shared=[shared]),
            quarantine=quarantine,
            downloads=downloads,
            catalog=FakeCatalogReads(),
            local=FakeLocalRepo(fail_enqueue=True),
        )
    )
    assert downloads.states[_A] is DownloadState.COMPLETED  # stuck at completed this round
    assert (Path("/staging") / "a.avi", _A) in quarantine.promoted  # source ALREADY consumed

    # Cycle 2: enqueue restored. The hash is still shared, state COMPLETED → re-promotion.
    local_ok = FakeLocalRepo()
    await run_download_cycle(
        _deps(
            client=FakeDownloadClient(shared=[shared]),
            quarantine=quarantine,
            downloads=downloads,
            catalog=FakeCatalogReads(),
            local=local_ok,
        )
    )
    assert downloads.states[_A] is DownloadState.QUARANTINED  # recovered, no more infinite loop
    assert local_ok.enqueued == [_A]


@pytest.mark.asyncio
async def test_monitor_unreachable_aborts_subsequent_steps() -> None:
    # MuleUnreachableError in _monitor (download_queue) = dead daemon → ABORT of the iteration:
    # neither the completions (step 2) nor the new candidates (step 3) must run.
    # (Doctrine "a dead daemon fails everything" — distinct from RepositoryError isolation.)
    client = FakeDownloadClient(queue_failures=[MuleUnreachableError("daemon down")])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.COMPLETED  # a pending completion (step 2)
    downloads.sizes[_A] = 10
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),  # a candidate (step 3)
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
    )
    await run_download_cycle(deps)  # does not raise (tolerated) but EVERYTHING is skipped
    assert quarantine.promoted == []  # step 2 NOT executed (abort before)
    assert local.enqueued == []
    assert downloads.states[_A] is DownloadState.COMPLETED  # unchanged
    assert _B not in downloads.states  # step 3 NOT executed
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_repo_failure_is_isolated_and_does_not_starve_candidates() -> None:
    # _monitor raises RepositoryError (set_state fails during reconciliation) → step 1 is
    # ISOLATED (log + continue), it does NOT starve step 3: _B is enqueued anyway.
    class _MonitorFailRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise RepositoryError("set_state monitor failed")

    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = _MonitorFailRepo()
    downloads.states[_A] = DownloadState.QUEUED  # reconciled → set_state(COMPLETED) will raise
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # does not raise
    # Step 3 ran despite the step-1 failure: _B enqueued + link emitted.
    assert downloads.states[_B] is DownloadState.QUEUED
    assert any(_B in link for link in client.added_links)


@pytest.mark.asyncio
async def test_add_links_repo_failure_is_tolerated_and_does_not_raise() -> None:
    # _add_links raises RepositoryError (set_state fails while marking a rejected link FAILED) →
    # tolerated (log), run_download_cycle does not raise. Contract "never raises".
    class _AddLinkSetStateFailRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            if state is DownloadState.FAILED:
                raise RepositoryError("set_state(FAILED) failed")
            super().set_state(ed2k_hash, state)

    # add_link rejected (EC_OP_FAILED) → _add_links tries set_state(FAILED), which raises
    # RepositoryError.
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = _AddLinkSetStateFailRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # does NOT raise (step-4 RepositoryError tolerated)


# ---------------------------------------------------------------------------
# Completion via the EC SHARED files (positive signal) + promotion to the REAL NAME (DV10-Q2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_file_for_tracked_hash_is_promoted_with_real_name() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="Keroro 62a.avi"),)])
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert (Path("/staging") / "Keroro 62a.avi", _A) in quarantine.promoted
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert local.enqueued == [_A]


@pytest.mark.asyncio
async def test_shared_name_with_traversal_is_confined_to_basename() -> None:
    # shared name = HOSTILE input (CLAUDE.md "filenames are hostile input"): a name with
    # traversal MUST NOT escape staging_dir — the os.replace SOURCE stays confined to the
    # basename (_safe_basename, non-None branch).
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="../../etc/passwd"),)])
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    (path, _) = quarantine.promoted[0]
    assert path == Path("/staging") / "passwd"  # confined to the basename
    assert ".." not in path.parts
    assert path.parent == Path("/staging")


@pytest.mark.asyncio
async def test_already_completed_shared_hash_is_promoted_without_restamping() -> None:
    # _A already COMPLETED (a previous round stamped it but promote had failed) reappears in
    # the shared files → promotion succeeds this time WITHOUT re-stamping COMPLETED (branch
    # `current is COMPLETED` of _promote_completion: skip the set_state, promote directly).
    class _NoCompletedSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            if state is DownloadState.COMPLETED:
                raise AssertionError("must not re-stamp COMPLETED (already completed)")
            super().set_state(ed2k_hash, state)

    downloads = _NoCompletedSetStateRepo()
    downloads.states[_A] = DownloadState.COMPLETED
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert (Path("/staging") / "x.avi", _A) in quarantine.promoted
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert local.enqueued == [_A]


@pytest.mark.asyncio
async def test_shared_file_for_untracked_hash_is_ignored() -> None:
    downloads = FakeDownloadRepo()  # _A non suivi
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []


@pytest.mark.asyncio
async def test_already_quarantined_shared_hash_is_not_repromoted() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUARANTINED
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []


@pytest.mark.asyncio
async def test_degenerate_shared_name_is_skipped() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name=".."),)])
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []
    # degenerate name → guard BEFORE the completed stamp: the state stays unchanged (DOWNLOADING),
    # re-judged next round if amuled finally reports a usable name. (Concern raised: the
    # test spec said COMPLETED, but the spec's prod code returns BEFORE stamping.)
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_promotion_failure_leaves_completed_for_retry() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine(fail_for={_A})
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    local = FakeLocalRepo()
    telemetry = RecordingTelemetry()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED
    assert local.enqueued == []
    assert any(isinstance(e, PromotionFailed) for e in telemetry.events)


@pytest.mark.asyncio
async def test_monitor_promotes_queued_to_downloading_not_completed() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)],
        shared=[()],  # not yet shared → no completion
    )
    quarantine = FakeQuarantine()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING  # NOT completed (bytes ignored)
    assert quarantine.promoted == []


@pytest.mark.asyncio
async def test_shared_files_unreachable_aborts_iteration_gracefully() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    client = FakeDownloadClient(shared_failures=[MuleUnreachableError("flux mort")])
    quarantine = FakeQuarantine()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # does not raise
    assert quarantine.promoted == []
