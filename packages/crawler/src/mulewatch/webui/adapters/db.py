"""STRICTLY read-only SQLite connection (webui spec W-D2 / §16).

``open_ro`` opens the database in ``mode=ro`` (URI) and sets ``PRAGMA query_only=ON``: a
double guard, never a write. We do NOT set ``journal_mode=WAL`` (that would be a write);
the database is in WAL on the crawler side (single writer), the reader inherits it.
``row_factory`` = ``sqlite3.Row`` for access by column name in the read adapters.
"""

import sqlite3
from pathlib import Path


def open_ro(path: Path) -> sqlite3.Connection:
    """Open ``path`` read-only (``mode=ro`` + ``query_only``).

    ``temp_store=MEMORY``: the reads materialize temp b-trees (window functions, group_concat,
    sorts) that with the default file-backed temp store spill to ``/tmp`` and overflow the
    hardened container's tiny (32 MB) tmpfs, raising ``database or disk is full``. A read-only
    reader has RAM (the container mem_limit) but no scratch disk, so temp belongs in memory.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection
