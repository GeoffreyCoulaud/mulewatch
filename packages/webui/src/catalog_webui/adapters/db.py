"""Connexion SQLite STRICTEMENT en lecture seule (spec webui W-D2 / §16).

``open_ro`` ouvre la base en ``mode=ro`` (URI) et pose ``PRAGMA query_only=ON`` : double
garde, jamais d'écriture. On NE pose PAS ``journal_mode=WAL`` (ce serait une écriture) ;
la base est en WAL côté crawler (writer unique), le lecteur en hérite. ``row_factory`` =
``sqlite3.Row`` pour un accès par nom de colonne dans les adapters de lecture.
"""

import sqlite3
from pathlib import Path


def open_ro(path: Path) -> sqlite3.Connection:
    """Ouvre ``path`` en lecture seule (``mode=ro`` + ``query_only``)."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection
