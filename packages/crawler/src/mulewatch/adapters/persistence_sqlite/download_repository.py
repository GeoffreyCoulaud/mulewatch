"""``SqliteDownloadRepository``: the state of downloads (local.db, download spec §7).

Implements persistence of the downloads managed by the crawler. ``downloads`` is NOT
append-only (mutable state, not the catalog) → UPSERT/UPDATE allowed, no triggers. Same
disciplines as the other repos (data-model spec §7): timestamp stamped BEFORE ``BEGIN``,
``BEGIN IMMEDIATE`` + rollback on ``BaseException`` (a NON-sqlite failure does not leave the
connection ``in_transaction``), ``wrap_sqlite_errors``.

``record_queued`` is dedup-safe (PK = hash, ``ON CONFLICT DO NOTHING``); ``set_state``
stamps ``completed_at`` on completion (injected clock); ``mark_seen``/``expire_lost`` carry
the lost-download TTL (2026-09-13 spec §2); ``active_states`` returns the hash→state map (the
loop's monitor reconciles against it).
"""

import sqlite3
from collections.abc import Iterable
from contextlib import suppress
from datetime import timedelta

from mulewatch.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from mulewatch.adapters.persistence_sqlite.errors import PersistenceError, wrap_sqlite_errors
from mulewatch.domain.download.states import DownloadState

_INSERT = """
INSERT INTO downloads (ed2k_hash, target_id, state, queued_at, size_bytes, last_seen_at)
VALUES (?, ?, 'queued', ?, ?, ?)
ON CONFLICT (ed2k_hash) DO NOTHING
"""

_SET_STATE = "UPDATE downloads SET state = ? WHERE ed2k_hash = ?"

_SET_STATE_COMPLETED = "UPDATE downloads SET state = ?, completed_at = ? WHERE ed2k_hash = ?"

_IS_DOWNLOADED = "SELECT 1 FROM downloads WHERE ed2k_hash = ?"

_ACTIVE_STATES = "SELECT ed2k_hash, state FROM downloads"

_GET_TARGET_ID = "SELECT target_id FROM downloads WHERE ed2k_hash = ?"

_MARK_SEEN = "UPDATE downloads SET last_seen_at = ? WHERE ed2k_hash = ?"

# The non-terminal states listed here MUST stay synchronized with _TERMINAL_STATES (states.py).
_EXPIRE_LOST = """
UPDATE downloads SET state = 'failed'
WHERE state IN ('queued', 'downloading') AND last_seen_at < ?
RETURNING ed2k_hash
"""


class SqliteDownloadRepository:
    """SQLite implementation of download persistence (STRUCTURAL satisfaction)."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock = utc_now) -> None:
        self._connection = connection
        self._clock = clock

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool:
        """INSERT of a ``queued`` download (dedup-safe). ``True`` if new, ``False`` if duplicate."""
        queued_at = utc_iso(self._clock())
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    _INSERT, (ed2k_hash, target_id, queued_at, size_bytes, queued_at)
                )
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        return cursor.rowcount == 1

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
        """UPDATE the state; stamps ``completed_at`` if the state is ``completed`` (injected clock).

        Requires an existing download (an unknown hash → ``PersistenceError``: caller-code bug).
        Only ``completed`` is timestamped; ``failed`` does not overwrite the ``completed_at``.
        """
        with wrap_sqlite_errors():
            if state == DownloadState.COMPLETED:
                cursor = self._connection.execute(
                    _SET_STATE_COMPLETED, (state.value, utc_iso(self._clock()), ed2k_hash)
                )
            else:
                cursor = self._connection.execute(_SET_STATE, (state.value, ed2k_hash))
        if cursor.rowcount != 1:
            raise PersistenceError(f"download {ed2k_hash} not found (caller bug)")

    def is_downloaded(self, ed2k_hash: str) -> bool:
        """``True`` if this hash is already known to ``downloads`` (dedup, spec §6)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_IS_DOWNLOADED, (ed2k_hash,)).fetchone()
        return row is not None

    def mark_seen(self, ed2k_hashes: Iterable[str]) -> None:
        """Stamps ``last_seen_at`` for the hashes amuled still knows (queue or shared files).

        An unknown hash updates nothing (a shared file the crawler never queued): no error.
        """
        seen_at = utc_iso(self._clock())
        with wrap_sqlite_errors():
            self._connection.executemany(
                _MARK_SEEN, [(seen_at, ed2k_hash) for ed2k_hash in ed2k_hashes]
            )

    def expire_lost(self, max_age_seconds: float) -> tuple[str, ...]:
        """Fails the non-terminal downloads amuled has not shown for ``max_age_seconds``.

        Returns the hashes it failed, for the caller to log. An entry stays in amuled's queue
        even with zero sources, so absence is a strong signal: the entry was removed, or the
        file completed and was moved out of IncomingDir before the next poll.
        """
        cutoff = utc_iso(self._clock() - timedelta(seconds=max_age_seconds))
        with wrap_sqlite_errors():
            rows = self._connection.execute(_EXPIRE_LOST, (cutoff,)).fetchall()
        return tuple(str(row[0]) for row in rows)

    def active_states(self) -> dict[str, DownloadState]:
        """Hash→state map of ALL known downloads (the monitor reconciles against it)."""
        with wrap_sqlite_errors():
            rows = self._connection.execute(_ACTIVE_STATES).fetchall()
        return {row[0]: DownloadState(row[1]) for row in rows}

    def get_target_id(self, ed2k_hash: str) -> str | None:
        """``target_id`` of a downloaded hash, or ``None`` (never queued) — READ.

        The download loop uses it to label the completion notification; ``None`` is a normal
        case (a shared hash the crawler never queued), reported as ``unknown``.
        """
        with wrap_sqlite_errors():
            row = self._connection.execute(_GET_TARGET_ID, (ed2k_hash,)).fetchone()
        if row is None:
            return None
        return str(row[0])
