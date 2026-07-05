"""``SqliteSchedulerStateRepository``: scheduler state as KV (orchestration spec §4/§7).

STRUCTURALLY implements the ``SchedulerStateRepository`` port. Stores three keys in the
``scheduler_state`` table of ``local.db``: ``cycle_index`` (integer serialized as TEXT),
``last_full_cycle_at`` (ISO-8601 UTC) and ``channel_backoff`` (JSON map of
:class:`ChannelBackoff` keyed by instance/instance:channel). ``write_cycle_state`` does ONE
atomic UPSERT of the index + timestamp under ``BEGIN IMMEDIATE`` (the index only advances at
the END of a cycle, spec §7: atomicity = a crash keeps the old index, so it replays that cycle).
``save_channel_backoff`` replaces the map ENTIRELY (registry snapshot, written at the same
moment as ``write_cycle_state`` — see ``run_search_cycle``). ``read_cycle_index`` returns
``0`` if the key is absent; ``load_channel_backoff`` returns an empty dict.

``scheduler_state`` is NOT append-only (mutable state, not the catalog): no
triggers — the ``ON CONFLICT … DO UPDATE`` UPSERT is allowed.
"""

import json
import sqlite3
from contextlib import suppress
from datetime import datetime
from typing import Any

from mulewatch.adapters.persistence_sqlite.connection import utc_iso
from mulewatch.adapters.persistence_sqlite.errors import wrap_sqlite_errors
from mulewatch.ports.scheduler_state_repository import ChannelBackoff

_SELECT_CYCLE_INDEX = "SELECT value FROM scheduler_state WHERE key = 'cycle_index'"

_SELECT_BACKOFF = "SELECT value FROM scheduler_state WHERE key = 'channel_backoff'"

_UPSERT = """
INSERT INTO scheduler_state (key, value) VALUES (?, ?)
ON CONFLICT (key) DO UPDATE SET value = excluded.value
"""


class SqliteSchedulerStateRepository:
    """SQLite implementation of the ``SchedulerStateRepository`` port (STRUCTURAL satisfaction)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def read_cycle_index(self) -> int:
        """Index of the next cycle, ``0`` if never written (first startup)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_CYCLE_INDEX).fetchone()
        return 0 if row is None else int(row[0])

    def write_cycle_state(self, cycle_index: int, last_full_cycle_at: datetime) -> None:
        """Atomic UPSERT of the index + timestamp (END of cycle, spec §7).

        ``last_full_cycle_at`` is an aware ``datetime``; ``utc_iso`` formats it (and REFUSES
        a naive one, ``Clock`` contract).
        """
        stamped = utc_iso(last_full_cycle_at)
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(_UPSERT, ("cycle_index", str(cycle_index)))
                self._connection.execute(_UPSERT, ("last_full_cycle_at", stamped))
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
        """Re-reads the persisted backoff map, ``{}`` if never written (first startup).

        Each JSON entry ``{"attempts": int, "retry_after": str}`` is reconstructed into a
        :class:`ChannelBackoff`. Harmless read: no explicit transaction.
        """
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_BACKOFF).fetchone()
        if row is None:
            return {}
        raw: dict[str, dict[str, Any]] = json.loads(row[0])
        return {
            key: ChannelBackoff(
                attempts=int(entry["attempts"]), retry_after=str(entry["retry_after"])
            )
            for key, entry in raw.items()
        }

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None:
        """Replaces the persisted map ENTIRELY (registry snapshot, END of cycle).

        Serialized as sorted JSON (``sort_keys`` → stable diff, determinism). Atomic UPSERT
        under ``BEGIN IMMEDIATE`` (same discipline as ``write_cycle_state``).
        """
        blob = json.dumps(
            {
                key: {"attempts": state.attempts, "retry_after": state.retry_after}
                for key, state in backoff.items()
            },
            sort_keys=True,
        )
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(_UPSERT, ("channel_backoff", blob))
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
