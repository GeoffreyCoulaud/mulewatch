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
from emule_indexer.ports.local_state_repository import ClaimedTask

_START = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_NODE_ID_QUERY = "SELECT value FROM node_runtime WHERE key = 'node_id'"


class _FakeClock:
    """Horloge injectable AVANÇABLE : zéro sleep, zéro flakiness (spec §8)."""

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
    assert uuid.UUID(created)  # un VRAI UUID, vérifiable
    assert repository.node_id() == created  # stable au second appel
    fresh = SqliteLocalStateRepository(connection)  # et pour toute instance future
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
    # 'created_at' pré-existant -> l'INSERT à deux lignes viole la PK -> rollback complet.
    connection.execute("INSERT INTO node_runtime (key, value) VALUES ('created_at', 'déjà')")
    with pytest.raises(PersistenceError, match="UNIQUE"):
        repository.node_id()
    assert connection.execute(_NODE_ID_QUERY).fetchone() is None


# --- enqueue idempotent (spec §6 : l'index UNIQUE partiel absorbe le doublon actif) ----


def test_enqueue_returns_true_then_false_while_pending(
    repository: SqliteLocalStateRepository,
) -> None:
    assert repository.enqueue_verification("aaaa") is True
    assert repository.enqueue_verification("aaaa") is False  # déjà active -> absorbé


def test_enqueue_is_still_refused_while_in_progress(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    assert repository.enqueue_verification("aaaa") is False  # in_progress est ACTIF aussi


# --- claim atomique FIFO (spec §6) -----------------------------------------------------


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
    # Horloge GELÉE : même enqueued_at -> départage déterministe par id croissant.
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
        "2026-06-11T12:05:00.000000+00:00",  # now + lease_duration (constructeur)
    )


def test_two_connections_claim_distinct_tasks(tmp_path: Path, clock: _FakeClock) -> None:
    # Atomicité PROUVÉE : deux connexions distinctes ne prennent JAMAIS la même tâche.
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
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    with pytest.raises(PersistenceError, match="panne injectée"):
        repository.claim_verification()
    status = connection.execute("SELECT status FROM verification_tasks").fetchone()[0]
    assert status == "pending"  # la transaction du claim a été défaite
