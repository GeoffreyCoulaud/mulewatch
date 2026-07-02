"""Read-only reads of local.db (webui spec W-D8 §node_state).

``LocalReader`` exposes one read:

- ``node_state()`` — full node state: downloads, verification tasks,
  scheduler KV, node identity.

All SQL lives in module constants, parameterized (no value interpolation).
"""

import sqlite3

from catalog_webui.domain.views import DownloadRow, NodeState, VerifTaskRow

# ---------------------------------------------------------------------------
# SQL constants
# ---------------------------------------------------------------------------

_SQL_DOWNLOADS = """\
SELECT
    ed2k_hash,
    target_id,
    state,
    queued_at,
    completed_at,
    size_bytes
FROM downloads
ORDER BY queued_at ASC, ed2k_hash ASC
"""

_SQL_VERIF_TASKS = """\
SELECT
    ed2k_hash,
    status,
    attempts,
    enqueued_at,
    lease_until
FROM verification_tasks
ORDER BY enqueued_at ASC, id ASC
"""

_SQL_SCHEDULER = """\
SELECT key, value FROM scheduler_state
"""

_SQL_NODE_RUNTIME_KEY = """\
SELECT value FROM node_runtime WHERE key = ?
"""


# ---------------------------------------------------------------------------
# LocalReader
# ---------------------------------------------------------------------------


class LocalReader:
    """Read-only access to local.db via a SQLite connection (open_ro)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def node_state(self) -> NodeState:
        """Return the full node state read from local.db."""
        dl_rows = self._conn.execute(_SQL_DOWNLOADS).fetchall()
        vt_rows = self._conn.execute(_SQL_VERIF_TASKS).fetchall()
        sched_rows = self._conn.execute(_SQL_SCHEDULER).fetchall()

        node_id_row = self._conn.execute(_SQL_NODE_RUNTIME_KEY, ("node_id",)).fetchone()
        created_at_row = self._conn.execute(_SQL_NODE_RUNTIME_KEY, ("created_at",)).fetchone()

        downloads = tuple(
            DownloadRow(
                ed2k_hash=row["ed2k_hash"],
                target_id=row["target_id"],
                state=row["state"],
                queued_at=row["queued_at"],
                completed_at=row["completed_at"],
                size_bytes=row["size_bytes"],
            )
            for row in dl_rows
        )

        verification_tasks = tuple(
            VerifTaskRow(
                ed2k_hash=row["ed2k_hash"],
                status=row["status"],
                attempts=row["attempts"],
                enqueued_at=row["enqueued_at"],
                lease_until=row["lease_until"],
            )
            for row in vt_rows
        )

        scheduler = {row["key"]: row["value"] for row in sched_rows}

        node_id: str | None = node_id_row["value"] if node_id_row is not None else None
        created_at: str | None = created_at_row["value"] if created_at_row is not None else None

        return NodeState(
            downloads=downloads,
            verification_tasks=verification_tasks,
            scheduler=scheduler,
            node_id=node_id,
            created_at=created_at,
        )
