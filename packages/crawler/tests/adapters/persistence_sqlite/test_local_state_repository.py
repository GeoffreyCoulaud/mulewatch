import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_local
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from emule_indexer.ports.local_state_repository import ClaimedTask, LocalStateRepository

_START = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_NODE_ID_QUERY = "SELECT value FROM node_runtime WHERE key = 'node_id'"


class _FakeClock:
    """Injectable ADVANCEABLE clock: zero sleep, zero flakiness (spec §8)."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def repository(connection: sqlite3.Connection, clock: _FakeClock) -> SqliteLocalStateRepository:
    return SqliteLocalStateRepository(connection, clock=clock)


# --- node_id (spec §3) ---------------------------------------------------------------


def test_node_id_is_created_on_first_call_and_stable(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    created = repository.node_id()
    assert uuid.UUID(created)  # a REAL UUID, verifiable
    assert repository.node_id() == created  # stable on the second call
    fresh = SqliteLocalStateRepository(connection)  # and for any future instance
    assert fresh.node_id() == created


def test_node_id_persists_created_at_alongside(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.node_id()
    row = connection.execute("SELECT value FROM node_runtime WHERE key = 'created_at'").fetchone()
    assert row == ("2026-06-11T12:00:00.000000+00:00",)


def test_node_id_creation_failure_is_wrapped_and_rolled_back(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    # 'created_at' pre-existing -> the two-row INSERT violates the PK -> full rollback.
    connection.execute("INSERT INTO node_runtime (key, value) VALUES ('created_at', 'already')")
    with pytest.raises(PersistenceError, match="UNIQUE"):
        repository.node_id()
    assert connection.execute(_NODE_ID_QUERY).fetchone() is None


def test_node_id_clock_failure_does_not_wedge_the_connection(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    # BUGGY clock (naive): utc_iso raises ValueError — a NON-sqlite failure must
    # NEVER leave the connection in_transaction (otherwise every subsequent call dies with
    # "cannot start a transaction within a transaction", a misleading diagnostic).
    broken = SqliteLocalStateRepository(connection, clock=lambda: datetime(2026, 6, 11, 12, 0, 0))
    with pytest.raises(ValueError, match="aware"):
        broken.node_id()
    assert connection.in_transaction is False  # healthy connection, no zombie transaction
    assert connection.execute(_NODE_ID_QUERY).fetchone() is None  # nothing half-written
    healthy = SqliteLocalStateRepository(connection, clock=clock)
    assert uuid.UUID(healthy.node_id())  # the SAME connection stays fully usable


# --- idempotent enqueue (spec §6: the partial UNIQUE index absorbs the active duplicate) ----


def test_enqueue_returns_true_then_false_while_pending(
    repository: SqliteLocalStateRepository,
) -> None:
    assert repository.enqueue_verification("aaaa") is True
    assert repository.enqueue_verification("aaaa") is False  # already active -> absorbed


def test_enqueue_is_still_refused_while_in_progress(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    assert repository.enqueue_verification("aaaa") is False  # in_progress is ACTIVE too


# --- atomic FIFO claim (spec §6) -------------------------------------------------------


def test_claim_is_fifo_by_enqueue_time(
    repository: SqliteLocalStateRepository, clock: _FakeClock
) -> None:
    repository.enqueue_verification("premier")
    clock.advance(timedelta(seconds=1))
    repository.enqueue_verification("second")
    first = repository.claim_verification()
    second = repository.claim_verification()
    assert first == ClaimedTask(task_id=1, ed2k_hash="premier", attempts=1)
    assert second == ClaimedTask(task_id=2, ed2k_hash="second", attempts=1)


def test_claim_breaks_enqueue_time_ties_by_id(
    repository: SqliteLocalStateRepository,
) -> None:
    # FROZEN clock: same enqueued_at -> deterministic tie-break by ascending id.
    repository.enqueue_verification("a")
    repository.enqueue_verification("b")
    first = repository.claim_verification()
    assert first is not None
    assert first.ed2k_hash == "a"


def test_claim_on_empty_queue_returns_none(repository: SqliteLocalStateRepository) -> None:
    assert repository.claim_verification() is None


def test_claim_stamps_lease_and_marks_in_progress(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    repository = SqliteLocalStateRepository(
        connection, clock=clock, lease_duration=timedelta(minutes=5)
    )
    repository.enqueue_verification("aaaa")
    repository.claim_verification()
    row = connection.execute(
        "SELECT status, claimed_at, lease_until FROM verification_tasks"
    ).fetchone()
    assert row == (
        "in_progress",
        "2026-06-11T12:00:00.000000+00:00",
        "2026-06-11T12:05:00.000000+00:00",  # now + lease_duration (constructor)
    )


def test_two_connections_claim_distinct_tasks(tmp_path: Path, clock: _FakeClock) -> None:
    # Atomicity PROVEN: two distinct connections NEVER take the same task.
    path = tmp_path / "local.db"
    first_connection = open_local(path)
    second_connection = open_local(path)
    try:
        producer = SqliteLocalStateRepository(first_connection, clock=clock)
        producer.enqueue_verification("t1")
        clock.advance(timedelta(seconds=1))
        producer.enqueue_verification("t2")
        consumer = SqliteLocalStateRepository(second_connection, clock=clock)
        first = producer.claim_verification()
        second = consumer.claim_verification()
        assert first is not None
        assert second is not None
        assert {first.ed2k_hash, second.ed2k_hash} == {"t1", "t2"}
    finally:
        first_connection.close()
        second_connection.close()


def test_claim_failure_is_wrapped_and_rolled_back(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.enqueue_verification("aaaa")
    connection.execute(
        "CREATE TRIGGER boom BEFORE UPDATE ON verification_tasks"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    with pytest.raises(PersistenceError, match="injected failure"):
        repository.claim_verification()
    status = connection.execute("SELECT status FROM verification_tasks").fetchone()[0]
    assert status == "pending"  # the claim's transaction was rolled back


# --- complete / fail / dead-letter (spec §6) -------------------------------------------


def test_complete_marks_done_and_keeps_the_row(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    repository.complete_verification(claimed.task_id)
    # done STAYS in the table (local history, spec §6).
    assert connection.execute("SELECT status FROM verification_tasks").fetchone() == ("done",)


def test_complete_requires_an_in_progress_task(
    repository: SqliteLocalStateRepository,
) -> None:
    with pytest.raises(PersistenceError, match="not found"):
        repository.complete_verification(42)  # unknown id
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    repository.complete_verification(claimed.task_id)
    with pytest.raises(PersistenceError, match="not found"):
        repository.complete_verification(claimed.task_id)  # already done: caller bug


def test_fail_below_max_attempts_requeues_as_pending(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    repository.fail_verification(claimed.task_id)
    row = connection.execute(
        "SELECT status, attempts, claimed_at, lease_until FROM verification_tasks"
    ).fetchone()
    assert row == ("pending", 1, None, None)  # attempts PRESERVED, lease cleared


def test_fail_at_max_attempts_dead_letters(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    repository = SqliteLocalStateRepository(connection, clock=clock, max_attempts=2)
    repository.enqueue_verification("aaaa")
    first = repository.claim_verification()
    assert first is not None
    assert first.attempts == 1
    repository.fail_verification(first.task_id)  # 1 < 2 -> pending
    second = repository.claim_verification()
    assert second is not None
    assert second.attempts == 2
    repository.fail_verification(second.task_id)  # 2 >= 2 -> dead_letter (likely poison)
    status = connection.execute("SELECT status FROM verification_tasks").fetchone()[0]
    assert status == "dead_letter"
    assert repository.claim_verification() is None  # a dead_letter is NEVER retried


def test_default_max_attempts_is_three(repository: SqliteLocalStateRepository) -> None:
    repository.enqueue_verification("aaaa")
    for expected_attempts in (1, 2, 3):
        claimed = repository.claim_verification()
        assert claimed is not None
        assert claimed.attempts == expected_attempts
        repository.fail_verification(claimed.task_id)
    assert repository.claim_verification() is None  # dead_letter on the 3rd failure (default)


def test_fail_requires_an_in_progress_task(repository: SqliteLocalStateRepository) -> None:
    with pytest.raises(PersistenceError, match="not found"):
        repository.fail_verification(42)


def test_enqueue_is_allowed_again_once_the_task_is_done(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    repository.complete_verification(claimed.task_id)
    assert repository.enqueue_verification("aaaa") is True  # done is NO LONGER active


# --- lease / reclaim (spec §6) ---------------------------------------------------------


def test_reclaim_expired_requeues_only_expired_leases(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    repository = SqliteLocalStateRepository(
        connection, clock=clock, lease_duration=timedelta(minutes=15)
    )
    repository.enqueue_verification("expired")
    repository.claim_verification()  # lease until 12:15
    clock.advance(timedelta(minutes=10))
    repository.enqueue_verification("fresh")
    repository.claim_verification()  # lease until 12:25
    clock.advance(timedelta(minutes=6))  # 12:16: the 1st has expired, not the 2nd
    assert repository.reclaim_expired() == 1
    rows = dict(connection.execute("SELECT ed2k_hash, status FROM verification_tasks").fetchall())
    assert rows == {"expired": "pending", "fresh": "in_progress"}
    reclaimed = repository.claim_verification()
    assert reclaimed is not None
    assert reclaimed.ed2k_hash == "expired"
    assert reclaimed.attempts == 2  # attempts counted AT CLAIM time, the re-claim counts


def test_reclaim_with_nothing_expired_returns_zero(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.enqueue_verification("aaaa")
    repository.claim_verification()
    assert repository.reclaim_expired() == 0  # lease still valid: nothing to reclaim


def test_reclaim_ignores_done_and_dead_letter(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    repository = SqliteLocalStateRepository(connection, clock=clock, max_attempts=1)
    repository.enqueue_verification("finie")
    done = repository.claim_verification()
    assert done is not None
    repository.complete_verification(done.task_id)
    repository.enqueue_verification("poison")
    poisoned = repository.claim_verification()
    assert poisoned is not None
    repository.fail_verification(poisoned.task_id)  # max_attempts=1 -> straight to dead_letter
    clock.advance(timedelta(days=1))  # all leases would have expired long ago
    assert repository.reclaim_expired() == 0


def test_repository_satisfies_the_port_structurally(
    repository: SqliteLocalStateRepository,
) -> None:
    port: LocalStateRepository = repository  # mypy proves structural satisfaction
    assert port.claim_verification() is None


# --- count_pending_verifications (Plan E.2 — observability) --------------------------------


def test_count_pending_verifications(repository: SqliteLocalStateRepository) -> None:
    assert repository.count_pending_verifications() == 0
    repository.enqueue_verification("a" * 32)
    repository.enqueue_verification("b" * 32)
    assert repository.count_pending_verifications() == 2
    claimed = repository.claim_verification()
    assert claimed is not None  # one task moves to in_progress → no longer 'pending'
    assert repository.count_pending_verifications() == 1
