"""STRICTLY read-only SQLite connection (webui spec W-D2 / §16).

``open_ro`` opens the database in ``mode=ro`` (URI) and sets ``PRAGMA query_only=ON``: a
double guard, never a write. We do NOT set ``journal_mode=WAL`` (that would be a write);
the database is in WAL on the crawler side (single writer), the reader inherits it.
``row_factory`` = ``sqlite3.Row`` for access by column name in the read adapters.
"""

import sqlite3
from pathlib import Path


def open_ro(path: Path) -> sqlite3.Connection:
    """Open ``path`` read-only (``mode=ro`` + ``query_only``)."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection
