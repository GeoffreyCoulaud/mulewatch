import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from mulewatch.application.edge_state import EdgeState
from mulewatch.application.run_verification_cycle import VerifyDeps, run_verification_cycle
from mulewatch.domain.observability.events import (
    VerificationCompleted,
    VerificationQueueDepthSampled,
    VerifierUnavailable,
)
from mulewatch.ports.content_verifier import VerificationResult
from mulewatch.ports.local_state_repository import ClaimedTask
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.verifier_errors import VerifierUnavailableError
from tests.application.fakes import RecordingTelemetry

_A = "a" * 32


class FakeQueue:
    """Scripted verification queue (subset of SqliteLocalStateRepository).

    ``claim_raises``/``complete_raises``/``fail_raises`` inject a ``RepositoryError`` on
    the matching step (proof of the top-level net + the at-least-once semantics).
    """

    def __init__(
        self,
        *,
        claims: list[ClaimedTask | None] | None = None,
        claim_raises: bool = False,
        complete_raises: bool = False,
        fail_raises: bool = False,
    ) -> None:
        self._claims = list(claims or [None])
        self._claim_raises = claim_raises
        self._complete_raises = complete_raises
        self._fail_raises = fail_raises
        self.reclaimed = 0
        self.completed: list[int] = []
        self.failed: list[int] = []

    def reclaim_expired(self) -> int:
        self.reclaimed += 1
        return 0

    def claim_verification(self) -> ClaimedTask | None:
        if self._claim_raises:
            raise RepositoryError("claim impossible (SQLITE_BUSY)")
        return self._claims.pop(0) if self._claims else None

    def complete_verification(self, task_id: int) -> None:
        if self._complete_raises:
            raise RepositoryError("complete impossible (SQLITE_BUSY)")
        self.completed.append(task_id)

    def fail_verification(self, task_id: int) -> None:
        if self._fail_raises:
            raise RepositoryError("fail impossible (SQLITE_BUSY)")
        self.failed.append(task_id)

    def count_pending_verifications(self) -> int:
        return len(self._claims)


class FakeTargets:
    """Scripted get_target_id (subset of SqliteDownloadRepository)."""

    def __init__(self, *, mapping: dict[str, str] | None = None) -> None:
        self._mapping = mapping or {}

    def get_target_id(self, ed2k_hash: str) -> str | None:
        return self._mapping.get(ed2k_hash)


class FakeWriter:
    """record_verification captured (subset of SqliteCatalogRepository)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[tuple[str, str]] = []
        self._fail = fail

    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: "list[object] | tuple[object, ...]",
    ) -> None:
        if self._fail:
            raise RepositoryError("verdict write failed")
        self.records.append((ed2k_hash, verdict))


class FakeVerifier:
    """Scripted ContentVerifier: canned verdict or an injected transient error."""

    def __init__(
        self,
        *,
        result: VerificationResult | None = None,
        verify_error: Exception | None = None,
        healthy: bool = True,
    ) -> None:
        self._result = result or VerificationResult(verdict="unverified", real_meta={}, checks=())
        self._verify_error = verify_error
        self._healthy = healthy
        self.verified: list[tuple[str, Mapping[str, object]]] = []

    async def verify(self, ed2k_hash: str, expected: Mapping[str, object]) -> VerificationResult:
        self.verified.append((ed2k_hash, expected))
        if self._verify_error is not None:
            raise self._verify_error
        return self._result

    async def health(self) -> bool:
        return self._healthy


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


def _deps(
    *,
    queue: FakeQueue,
    verifier: FakeVerifier,
    writer: FakeWriter,
    targets: FakeTargets,
    clock: FakeClock | None = None,
    telemetry: RecordingTelemetry | None = None,
    edge: EdgeState | None = None,
) -> VerifyDeps:
    return VerifyDeps(
        queue=queue,
        verifier=verifier,
        writer=writer,
        targets=targets,
        poll_interval_seconds=10.0,
        clock=clock or FakeClock(),
        telemetry=telemetry or RecordingTelemetry(),
        edge=edge or EdgeState(),
    )


@pytest.mark.asyncio
async def test_empty_queue_reclaims_then_sleeps() -> None:
    queue = FakeQueue(claims=[None])
    clock = FakeClock()
    deps = _deps(
        queue=queue,
        verifier=FakeVerifier(),
        writer=FakeWriter(),
        targets=FakeTargets(),
        clock=clock,
    )
    await run_verification_cycle(deps)
    assert queue.reclaimed == 1
    assert clock.sleeps == [10.0]  # empty queue → sleeps the poll
    assert queue.completed == []


@pytest.mark.asyncio
async def test_claimed_task_is_verified_recorded_completed() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=7, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier(
        result=VerificationResult(verdict="unverified", real_meta={}, checks=())
    )
    writer = FakeWriter()
    deps = _deps(
        queue=queue,
        verifier=verifier,
        writer=writer,
        targets=FakeTargets(mapping={_A: "062A"}),
    )
    await run_verification_cycle(deps)
    assert verifier.verified == [(_A, {"target_id": "062A"})]
    assert writer.records == [(_A, "unverified")]
    assert queue.completed == [7]
    assert queue.failed == []


@pytest.mark.asyncio
async def test_expected_is_empty_when_target_unknown() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=1, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier()
    deps = _deps(
        queue=queue,
        verifier=verifier,
        writer=FakeWriter(),
        targets=FakeTargets(mapping={}),  # no known target
    )
    await run_verification_cycle(deps)
    assert verifier.verified == [(_A, {})]  # minimal empty expected (DECISION DV11)
    assert queue.completed == [1]


@pytest.mark.asyncio
async def test_error_verdict_is_recorded_and_completed_not_failed() -> None:
    # a malformed 200 response arrives as VerificationResult(verdict="error") (adapter):
    # DETERMINISTIC → recorded + complete, NEVER fail (no infinite loop, DECISION DV6).
    queue = FakeQueue(claims=[ClaimedTask(task_id=2, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier(result=VerificationResult(verdict="error", real_meta={}, checks=()))
    writer = FakeWriter()
    deps = _deps(queue=queue, verifier=verifier, writer=writer, targets=FakeTargets())
    await run_verification_cycle(deps)
    assert writer.records == [(_A, "error")]
    assert queue.completed == [2]
    assert queue.failed == []


@pytest.mark.asyncio
async def test_unavailable_verifier_fails_the_task_and_backs_off() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=3, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier(verify_error=VerifierUnavailableError("down"))
    writer = FakeWriter()
    clock = FakeClock()
    deps = _deps(queue=queue, verifier=verifier, writer=writer, targets=FakeTargets(), clock=clock)
    await run_verification_cycle(deps)  # does not raise
    assert writer.records == []  # no invented verdict
    assert queue.completed == []
    assert queue.failed == [3]  # lease → retry / dead-letter
    assert clock.sleeps == [10.0]  # backoff: no spin on a transient verifier failure


@pytest.mark.asyncio
async def test_record_failure_fails_the_task() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=4, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier()
    writer = FakeWriter(fail=True)  # record_verification raises RepositoryError
    deps = _deps(queue=queue, verifier=verifier, writer=writer, targets=FakeTargets())
    await run_verification_cycle(deps)  # does not raise
    assert queue.completed == []
    assert queue.failed == [4]  # retry (the verifier is idempotent/stateless)


@pytest.mark.asyncio
async def test_non_empty_queue_does_not_sleep() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=5, ed2k_hash=_A, attempts=1)])
    clock = FakeClock()
    deps = _deps(
        queue=queue,
        verifier=FakeVerifier(),
        writer=FakeWriter(),
        targets=FakeTargets(),
        clock=clock,
    )
    await run_verification_cycle(deps)
    assert clock.sleeps == []  # one task processed → no poll sleep


@pytest.mark.asyncio
async def test_repository_error_on_claim_is_absorbed_and_sleeps() -> None:
    # LAST-RESORT net: a RepositoryError from claim (e.g. SQLITE_BUSY) does NOT raise and
    # SLEEPS (poll_interval) to avoid a tight spin if the DB is durably down.
    queue = FakeQueue(claim_raises=True)
    clock = FakeClock()
    deps = _deps(
        queue=queue,
        verifier=FakeVerifier(),
        writer=FakeWriter(),
        targets=FakeTargets(),
        clock=clock,
    )
    await run_verification_cycle(deps)  # does not raise
    assert clock.sleeps == [10.0]  # error path → backoff (no spin)
    assert queue.completed == []
    assert queue.failed == []


@pytest.mark.asyncio
async def test_repository_error_on_complete_fails_the_task_and_backs_off() -> None:
    # complete raises RepositoryError AFTER a successful record: at-least-once → fail_verification
    # (the lease will re-verify, a possible duplicate that D-analysis will dedupe). No crash, and
    # backoff: without this sleep, a durably-down complete would spin (RPC + duplicate row/cycle).
    queue = FakeQueue(
        claims=[ClaimedTask(task_id=6, ed2k_hash=_A, attempts=1)], complete_raises=True
    )
    writer = FakeWriter()
    clock = FakeClock()
    deps = _deps(
        queue=queue, verifier=FakeVerifier(), writer=writer, targets=FakeTargets(), clock=clock
    )
    await run_verification_cycle(deps)  # does not raise
    assert writer.records == [(_A, "unverified")]  # the verdict was recorded
    assert queue.completed == []
    assert queue.failed == [6]  # retry via lease
    assert clock.sleeps == [10.0]  # backoff on the record/complete path (anti-spin)


@pytest.mark.asyncio
async def test_repository_error_from_fail_verification_itself_is_absorbed() -> None:
    # fail_verification itself raises (SQLITE_BUSY) on the verifier-unreachable path:
    # the top-level net absorbs → no crash, and SLEEPS (error path).
    queue = FakeQueue(claims=[ClaimedTask(task_id=8, ed2k_hash=_A, attempts=1)], fail_raises=True)
    clock = FakeClock()
    deps = _deps(
        queue=queue,
        verifier=FakeVerifier(verify_error=VerifierUnavailableError("down")),
        writer=FakeWriter(),
        targets=FakeTargets(),
        clock=clock,
    )
    await run_verification_cycle(deps)  # does not raise
    assert queue.completed == []
    assert queue.failed == []  # fail raised before recording
    assert clock.sleeps == [10.0]  # top-level net → backoff


@pytest.mark.asyncio
async def test_emits_verification_completed_and_queue_depth() -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    queue = FakeQueue(claims=[ClaimedTask(task_id=9, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier(result=VerificationResult(verdict="clean", real_meta={}, checks=()))
    deps = _deps(
        queue=queue,
        verifier=verifier,
        writer=FakeWriter(),
        targets=FakeTargets(mapping={_A: "062A"}),
        telemetry=telemetry,
        edge=edge,
    )
    await run_verification_cycle(deps)
    assert any(isinstance(e, VerificationQueueDepthSampled) for e in telemetry.events)
    assert any(
        isinstance(e, VerificationCompleted) and e.verdict == "clean" and e.target_id == "062A"
        for e in telemetry.events
    )


@pytest.mark.asyncio
async def test_verifier_unavailable_is_edge_triggered() -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    queue = FakeQueue(claims=[ClaimedTask(task_id=10, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier(verify_error=VerifierUnavailableError("down"))
    deps = _deps(
        queue=queue,
        verifier=verifier,
        writer=FakeWriter(),
        targets=FakeTargets(),
        telemetry=telemetry,
        edge=edge,
    )
    await run_verification_cycle(deps)
    unav = [e for e in telemetry.events if isinstance(e, VerifierUnavailable)]
    assert unav and unav[0].first_occurrence is True

    telemetry.events.clear()
    queue2 = FakeQueue(claims=[ClaimedTask(task_id=11, ed2k_hash=_A, attempts=1)])
    deps2 = _deps(
        queue=queue2,
        verifier=FakeVerifier(verify_error=VerifierUnavailableError("down")),
        writer=FakeWriter(),
        targets=FakeTargets(),
        telemetry=telemetry,
        edge=edge,
    )
    await run_verification_cycle(deps2)
    unav2 = [e for e in telemetry.events if isinstance(e, VerifierUnavailable)]
    assert unav2 and unav2[0].first_occurrence is False
