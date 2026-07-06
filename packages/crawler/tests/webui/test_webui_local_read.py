"""Read-only tests of local.db via LocalReader (Task 8 — W-D8 §node_state)."""

import sqlite3
from pathlib import Path

from mulewatch.webui.adapters.local_read import LocalReader
from mulewatch.webui.domain.views import DownloadRow, NodeState, VerifTaskRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_local(path: Path) -> sqlite3.Connection:
    """Open local.db in read-only mode (row_factory enabled)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Populated DB
# ---------------------------------------------------------------------------


def test_node_state_populated(local_db: Path) -> None:
    """Populated DB: downloads, verification_tasks, scheduler_state, node_runtime."""
    # --- populate ---
    with sqlite3.connect(local_db) as w:
        w.execute(
            "INSERT INTO downloads VALUES (?,?,?,?,?,?)",
            ("aabbccdd" * 4, "062A", "active", "2026-06-22T10:00:00Z", None, 1024),
        )
        w.execute(
            "INSERT INTO verification_tasks (ed2k_hash, status, attempts, enqueued_at)"
            " VALUES (?,?,?,?)",
            ("aabbccdd" * 4, "pending", 0, "2026-06-22T10:01:00Z"),
        )
        w.execute(
            "INSERT INTO scheduler_state VALUES (?,?)",
            ("cycle_index", "5"),
        )
        w.execute(
            "INSERT INTO node_runtime VALUES (?,?)",
            ("node_id", "test-node-42"),
        )
        w.execute(
            "INSERT INTO node_runtime VALUES (?,?)",
            ("created_at", "2026-06-22T00:00:00Z"),
        )
        w.commit()

    conn = _open_local(local_db)
    state = LocalReader(conn).node_state()
    conn.close()

    # downloads
    assert len(state.downloads) == 1
    dl = state.downloads[0]
    assert isinstance(dl, DownloadRow)
    assert dl.ed2k_hash == "aabbccdd" * 4
    assert dl.target_id == "062A"
    assert dl.state == "active"
    assert dl.queued_at == "2026-06-22T10:00:00Z"
    assert dl.completed_at is None
    assert dl.size_bytes == 1024

    # verification tasks
    assert len(state.verification_tasks) == 1
    vt = state.verification_tasks[0]
    assert isinstance(vt, VerifTaskRow)
    assert vt.ed2k_hash == "aabbccdd" * 4
    assert vt.status == "pending"
    assert vt.attempts == 0
    assert vt.enqueued_at == "2026-06-22T10:01:00Z"
    assert vt.lease_until is None

    # scheduler KV
    assert state.scheduler == {"cycle_index": "5"}

    # node_runtime
    assert state.node_id == "test-node-42"
    assert state.created_at == "2026-06-22T00:00:00Z"


# ---------------------------------------------------------------------------
# Empty DB (observer mode) — covers the None branches
# ---------------------------------------------------------------------------


def test_node_state_empty_db(local_db: Path) -> None:
    """Empty DB (observer mode): everything empty / None — no error."""
    conn = _open_local(local_db)
    state = LocalReader(conn).node_state()
    conn.close()

    assert isinstance(state, NodeState)
    assert state.downloads == ()
    assert state.verification_tasks == ()
    assert state.scheduler == {}
    assert state.node_id is None
    assert state.created_at is None
