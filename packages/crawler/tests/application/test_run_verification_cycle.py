import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from emule_indexer.application.run_verification_cycle import VerifyDeps, run_verification_cycle
from emule_indexer.ports.content_verifier import VerificationResult
from emule_indexer.ports.local_state_repository import ClaimedTask
from emule_indexer.ports.repository_errors import RepositoryError
from emule_indexer.ports.verifier_errors import VerifierUnavailableError

_A = "a" * 32


class FakeQueue:
    """File de vérification scriptée (sous-ensemble de SqliteLocalStateRepository).

    ``claim_raises``/``complete_raises``/``fail_raises`` injectent une ``RepositoryError`` sur
    l'étape correspondante (preuve du filet top-level + de la sémantique at-least-once).
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


class FakeTargets:
    """get_target_id scripté (sous-ensemble de SqliteDownloadRepository)."""

    def __init__(self, *, mapping: dict[str, str] | None = None) -> None:
        self._mapping = mapping or {}

    def get_target_id(self, ed2k_hash: str) -> str | None:
        return self._mapping.get(ed2k_hash)


class FakeWriter:
    """record_verification capturé (sous-ensemble de SqliteCatalogRepository)."""

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
            raise RepositoryError("écriture verdict échouée")
        self.records.append((ed2k_hash, verdict))


class FakeVerifier:
    """ContentVerifier scripté : verdict en conserve ou erreur transitoire injectée."""

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
) -> VerifyDeps:
    return VerifyDeps(
        queue=queue,
        verifier=verifier,
        writer=writer,
        targets=targets,
        poll_interval_seconds=10.0,
        clock=clock or FakeClock(),
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
    assert clock.sleeps == [10.0]  # file vide → dort le poll
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
        targets=FakeTargets(mapping={_A: "S2E062A"}),
    )
    await run_verification_cycle(deps)
    assert verifier.verified == [(_A, {"target_id": "S2E062A"})]
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
        targets=FakeTargets(mapping={}),  # pas de target connu
    )
    await run_verification_cycle(deps)
    assert verifier.verified == [(_A, {})]  # expected minimal vide (DÉCISION DV11)
    assert queue.completed == [1]


@pytest.mark.asyncio
async def test_error_verdict_is_recorded_and_completed_not_failed() -> None:
    # une réponse 200 malformée arrive en VerificationResult(verdict="error") (adapter) :
    # DÉTERMINISTE → enregistrée + complete, JAMAIS fail (pas de boucle infinie, DÉCISION DV6).
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
    await run_verification_cycle(deps)  # ne lève pas
    assert writer.records == []  # pas de verdict inventé
    assert queue.completed == []
    assert queue.failed == [3]  # lease → retry / dead-letter
    assert clock.sleeps == [10.0]  # backoff : pas de spin sur panne transitoire du verifier


@pytest.mark.asyncio
async def test_record_failure_fails_the_task() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=4, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier()
    writer = FakeWriter(fail=True)  # record_verification lève RepositoryError
    deps = _deps(queue=queue, verifier=verifier, writer=writer, targets=FakeTargets())
    await run_verification_cycle(deps)  # ne lève pas
    assert queue.completed == []
    assert queue.failed == [4]  # retry (le verifier est idempotent/stateless)


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
    assert clock.sleeps == []  # une tâche traitée → pas de sleep de poll


@pytest.mark.asyncio
async def test_repository_error_on_claim_is_absorbed_and_sleeps() -> None:
    # Filet ULTIME : une RepositoryError depuis claim (p.ex. SQLITE_BUSY) NE LÈVE PAS et
    # DORT (poll_interval) pour éviter un spin serré si la DB est durablement KO.
    queue = FakeQueue(claim_raises=True)
    clock = FakeClock()
    deps = _deps(
        queue=queue,
        verifier=FakeVerifier(),
        writer=FakeWriter(),
        targets=FakeTargets(),
        clock=clock,
    )
    await run_verification_cycle(deps)  # ne lève pas
    assert clock.sleeps == [10.0]  # chemin d'erreur → backoff (pas de spin)
    assert queue.completed == []
    assert queue.failed == []


@pytest.mark.asyncio
async def test_repository_error_on_complete_fails_the_task_and_backs_off() -> None:
    # complete lève RepositoryError APRÈS un record réussi : at-least-once → fail_verification
    # (le lease re-vérifiera, duplicate possible que D-analysis dédupliquera). Pas de crash, et
    # backoff : sans ce sleep, un complete durablement KO spinnerait (RPC + ligne dupliquée/cycle).
    queue = FakeQueue(
        claims=[ClaimedTask(task_id=6, ed2k_hash=_A, attempts=1)], complete_raises=True
    )
    writer = FakeWriter()
    clock = FakeClock()
    deps = _deps(
        queue=queue, verifier=FakeVerifier(), writer=writer, targets=FakeTargets(), clock=clock
    )
    await run_verification_cycle(deps)  # ne lève pas
    assert writer.records == [(_A, "unverified")]  # le verdict a été enregistré
    assert queue.completed == []
    assert queue.failed == [6]  # retry via lease
    assert clock.sleeps == [10.0]  # backoff sur le chemin record/complete (anti-spin)


@pytest.mark.asyncio
async def test_repository_error_from_fail_verification_itself_is_absorbed() -> None:
    # fail_verification lui-même lève (SQLITE_BUSY) sur le chemin verifier-injoignable :
    # le filet top-level absorbe → pas de crash, et DORT (chemin d'erreur).
    queue = FakeQueue(claims=[ClaimedTask(task_id=8, ed2k_hash=_A, attempts=1)], fail_raises=True)
    clock = FakeClock()
    deps = _deps(
        queue=queue,
        verifier=FakeVerifier(verify_error=VerifierUnavailableError("down")),
        writer=FakeWriter(),
        targets=FakeTargets(),
        clock=clock,
    )
    await run_verification_cycle(deps)  # ne lève pas
    assert queue.completed == []
    assert queue.failed == []  # fail a levé avant d'enregistrer
    assert clock.sleeps == [10.0]  # filet top-level → backoff
