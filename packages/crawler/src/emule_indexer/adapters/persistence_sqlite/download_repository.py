"""``SqliteDownloadRepository``: the state of downloads (local.db, download spec §7).

Implements persistence of the downloads managed by the crawler. ``downloads`` is NOT
append-only (mutable state, not the catalog) → UPSERT/UPDATE allowed, no triggers. Same
disciplines as the other repos (data-model spec §7): timestamp stamped BEFORE ``BEGIN``,
``BEGIN IMMEDIATE`` + rollback on ``BaseException`` (a NON-sqlite failure does not leave the
connection ``in_transaction``), ``wrap_sqlite_errors``.

``record_queued`` is dedup-safe (PK = hash, ``ON CONFLICT DO NOTHING``); ``set_state``
stamps ``completed_at`` on completion (injected clock); ``committed_bytes`` sums the
``size_bytes`` of NON-terminal states (application-level disk cap, DECISION D6/D7);
``active_states`` returns the hash→state map (the loop's monitor reconciles against it).
"""

import sqlite3
from contextlib import suppress

from emule_indexer.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError, wrap_sqlite_errors
from emule_indexer.domain.download.states import DownloadState

_INSERT = """
INSERT INTO downloads (ed2k_hash, target_id, state, queued_at, size_bytes)
VALUES (?, ?, 'queued', ?, ?)
ON CONFLICT (ed2k_hash) DO NOTHING
"""

_SET_STATE = "UPDATE downloads SET state = ? WHERE ed2k_hash = ?"

_SET_STATE_COMPLETED = "UPDATE downloads SET state = ?, completed_at = ? WHERE ed2k_hash = ?"

_IS_DOWNLOADED = "SELECT 1 FROM downloads WHERE ed2k_hash = ?"

_ACTIVE_STATES = "SELECT ed2k_hash, state FROM downloads"

_GET_TARGET_ID = "SELECT target_id FROM downloads WHERE ed2k_hash = ?"

# The cap only counts ACTIVE downloads (non-terminal states, DECISION D7).
# The 3 terminal ones listed here MUST stay synchronized with _TERMINAL_STATES (states.py).
_COMMITTED_BYTES = (
    "SELECT COALESCE(SUM(size_bytes), 0) FROM downloads "
    "WHERE state NOT IN ('completed', 'quarantined', 'failed')"
)


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
                    _INSERT, (ed2k_hash, target_id, queued_at, size_bytes)
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
        Only ``completed`` (the first instant of completion) is timestamped;
        ``quarantined``/``failed`` do not overwrite the ``completed_at``.
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

    def committed_bytes(self) -> int:
        """Sum of the ``size_bytes`` of ACTIVE downloads (disk cap, spec §7)."""
        with wrap_sqlite_errors():
            return int(self._connection.execute(_COMMITTED_BYTES).fetchone()[0])

    def active_states(self) -> dict[str, DownloadState]:
        """Hash→state map of ALL known downloads (the monitor reconciles against it)."""
        with wrap_sqlite_errors():
            rows = self._connection.execute(_ACTIVE_STATES).fetchall()
        return {row[0]: DownloadState(row[1]) for row in rows}

    def get_target_id(self, ed2k_hash: str) -> str | None:
        """``target_id`` of a downloaded hash, or ``None`` (never queued) — READ.

        The verification loop (verify spec §6, DECISION DV11) uses it to build a minimal
        ``expected``; the NO-OP ignores it, D-analysis will enrich it. ``None`` is a normal
        case (a task may be claimed for a hash whose download row has been promoted/purged
        — the loop then builds ``expected={}``).
        """
        with wrap_sqlite_errors():
            row = self._connection.execute(_GET_TARGET_ID, (ed2k_hash,)).fetchone()
        if row is None:
            return None
        return str(row[0])
