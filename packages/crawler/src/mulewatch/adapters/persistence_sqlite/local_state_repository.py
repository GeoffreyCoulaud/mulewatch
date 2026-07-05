"""``SqliteLocalStateRepository``: node identity + task queue (spec §4/§6).

The queue (MVP spec §12): atomic FIFO claim under ``BEGIN IMMEDIATE`` + ``RETURNING``
(defence in depth — the single writer is guaranteed by the deployment, spec §3),
lease configurable at the constructor, bounded retries → ``dead_letter`` ("likely
poison", plan E will turn it into an alert), idempotent enqueue (the partial UNIQUE index
on the active statuses absorbs the duplicate: ``ON CONFLICT … DO NOTHING``, verified
empirically with an explicit conflict target, SQLite 3.47.1). ``done``/``dead_letter``
stay in the table (local history, reconstructible — spec §6).

``node_id`` (spec §3): UUID generated on the first call, persisted in ``node_runtime``
with ``created_at``, stable thereafter (scheduler seed §6 MVP + observation tag).
"""

import sqlite3
import uuid
from contextlib import suppress
from datetime import timedelta

from mulewatch.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from mulewatch.adapters.persistence_sqlite.errors import (
    PersistenceError,
    wrap_sqlite_errors,
)
from mulewatch.ports.local_state_repository import ClaimedTask

_SELECT_NODE_ID = "SELECT value FROM node_runtime WHERE key = 'node_id'"

_INSERT_NODE_IDENTITY = """
INSERT INTO node_runtime (key, value)
VALUES ('node_id', ?), ('created_at', ?)
"""

_ENQUEUE = """
INSERT INTO verification_tasks (ed2k_hash, status, enqueued_at)
VALUES (?, 'pending', ?)
ON CONFLICT (ed2k_hash) WHERE status IN ('pending', 'in_progress') DO NOTHING
"""

_CLAIM = """
UPDATE verification_tasks
SET
    status = 'in_progress',
    claimed_at = :now,
    lease_until = :lease,
    attempts = attempts + 1
WHERE id = (
    SELECT id FROM verification_tasks
    WHERE status = 'pending'
    ORDER BY enqueued_at, id
    LIMIT 1
)
RETURNING id, ed2k_hash, attempts
"""

_COMPLETE = "UPDATE verification_tasks SET status = 'done' WHERE id = ? AND status = 'in_progress'"

_FAIL = """
UPDATE verification_tasks
SET
    status = CASE WHEN attempts >= :max_attempts THEN 'dead_letter' ELSE 'pending' END,
    claimed_at = NULL,
    lease_until = NULL
WHERE id = :task_id AND status = 'in_progress'
"""

_RECLAIM = """
UPDATE verification_tasks
SET status = 'pending', claimed_at = NULL, lease_until = NULL
WHERE status = 'in_progress' AND lease_until < ?
"""

_COUNT_PENDING = "SELECT COUNT(*) FROM verification_tasks WHERE status = 'pending'"

_SELECT_BACKFILL_POLICY = "SELECT policy_sha256 FROM backfill_state WHERE id = 1"

_UPSERT_BACKFILL_POLICY = """
INSERT INTO backfill_state (id, policy_sha256)
VALUES (1, ?)
ON CONFLICT (id) DO UPDATE SET policy_sha256 = excluded.policy_sha256
"""


class SqliteLocalStateRepository:
    """SQLite implementation of the ``LocalStateRepository`` port (STRUCTURAL satisfaction)."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Clock = utc_now,
        lease_duration: timedelta = timedelta(minutes=15),
        max_attempts: int = 3,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._lease_duration = lease_duration
        self._max_attempts = max_attempts

    def node_id(self) -> str:
        """UUID created (and persisted with ``created_at``) on the first call, stable after."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_NODE_ID).fetchone()
            if row is not None:
                return str(row[0])
            generated = str(uuid.uuid4())
            # Stamp computed BEFORE the BEGIN (same hygiene as claim_verification): a
            # buggy clock must not raise in the middle of a transaction.
            created_at = utc_iso(self._clock())
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(_INSERT_NODE_IDENTITY, (generated, created_at))
                self._connection.execute("COMMIT")
            except BaseException:
                # Rollback on BaseException (same discipline as catalog_repository):
                # a NON-sqlite failure must not leave the connection in_transaction —
                # otherwise the repository would be permanently broken.
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        return generated

    def enqueue_verification(self, ed2k_hash: str) -> bool:
        """``True`` if a task was created; ``False`` if an ACTIVE task already existed."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(_ENQUEUE, (ed2k_hash, utc_iso(self._clock())))
        return cursor.rowcount == 1

    def claim_verification(self) -> ClaimedTask | None:
        """Atomic FIFO claim (``BEGIN IMMEDIATE`` + ``RETURNING``); empty queue → ``None``.

        FIFO = ``ORDER BY enqueued_at, id`` (the fixed-width UTC ISO makes lexicographic
        order chronological; ``id`` breaks clock ties). ``attempts`` is counted AT CLAIM
        time (spec §6).
        """
        now = self._clock()
        parameters = {"now": utc_iso(now), "lease": utc_iso(now + self._lease_duration)}
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(_CLAIM, parameters).fetchone()
                self._connection.execute("COMMIT")
            except sqlite3.Error:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        if row is None:
            return None
        return ClaimedTask(task_id=row[0], ed2k_hash=row[1], attempts=row[2])

    def complete_verification(self, task_id: int) -> None:
        """Marks ``done`` (the row STAYS: local history). Requires an ``in_progress`` task."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(_COMPLETE, (task_id,))
        if cursor.rowcount != 1:
            raise PersistenceError(f"task {task_id} not found in in_progress (caller bug)")

    def fail_verification(self, task_id: int) -> None:
        """Back to ``pending``, unless ``attempts >= max_attempts`` → ``dead_letter`` (§12)."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(
                _FAIL, {"max_attempts": self._max_attempts, "task_id": task_id}
            )
        if cursor.rowcount != 1:
            raise PersistenceError(f"task {task_id} not found in in_progress (caller bug)")

    def reclaim_expired(self) -> int:
        """Returns to ``pending`` every ``in_progress`` whose lease expired; returns the count."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(_RECLAIM, (utc_iso(self._clock()),))
        return cursor.rowcount

    def count_pending_verifications(self) -> int:
        """Number of pending tasks (observability gauge — harmless read)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_COUNT_PENDING).fetchone()
        return int(row[0])

    def last_backfill_policy(self) -> str | None:
        """The stored policy fingerprint, or ``None`` if no backfill ever ran (spec §7.1)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_BACKFILL_POLICY).fetchone()
        return str(row[0]) if row is not None else None

    def set_last_backfill_policy(self, sha256: str) -> None:
        """Upserts the single-row marker (called only AFTER a full backfill pass)."""
        with wrap_sqlite_errors():
            self._connection.execute(_UPSERT_BACKFILL_POLICY, (sha256,))
