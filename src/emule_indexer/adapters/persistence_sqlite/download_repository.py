"""``SqliteDownloadRepository`` : l'état des downloads (local.db, spec download §7).

Implémente la persistance des downloads gérés par le crawler. ``downloads`` n'est PAS
append-only (état mutable, pas le catalogue) → UPSERT/UPDATE licites, pas de triggers. Mêmes
disciplines que les autres repos (spec data-model §7) : timestamp stampé AVANT ``BEGIN``,
``BEGIN IMMEDIATE`` + rollback sur ``BaseException`` (une panne NON-sqlite ne laisse pas la
connexion ``in_transaction``), ``wrap_sqlite_errors``.

``record_queued`` est dédup-safe (PK = hash, ``ON CONFLICT DO NOTHING``) ; ``set_state``
stampe ``completed_at`` à la complétion (horloge injectée) ; ``committed_bytes`` somme les
``size_bytes`` des états NON terminaux (plafond disque applicatif, DÉCISION D6/D7) ;
``active_states`` rend la map hash→état (le monitor de la boucle réconcilie dessus).
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

# Le plafond ne compte que les downloads ACTIFS (états non terminaux, DÉCISION D7).
_COMMITTED_BYTES = (
    "SELECT COALESCE(SUM(size_bytes), 0) FROM downloads "
    "WHERE state NOT IN ('completed', 'quarantined', 'failed')"
)


class SqliteDownloadRepository:
    """Implémentation SQLite de la persistance des downloads (satisfaction STRUCTURELLE)."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock = utc_now) -> None:
        self._connection = connection
        self._clock = clock

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool:
        """INSERT d'un download ``queued`` (dédup-safe). ``True`` si créé, ``False`` si doublon."""
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
        """UPDATE de l'état ; stampe ``completed_at`` si l'état est ``completed`` (horloge inj.).

        Exige un download existant (un hash inconnu → ``PersistenceError`` : bug du code
        appelant). Seul ``completed`` (premier instant de complétion) est horodaté ;
        ``quarantined``/``failed`` n'écrasent pas le ``completed_at``.
        """
        with wrap_sqlite_errors():
            if state == DownloadState.COMPLETED:
                cursor = self._connection.execute(
                    _SET_STATE_COMPLETED, (state.value, utc_iso(self._clock()), ed2k_hash)
                )
            else:
                cursor = self._connection.execute(_SET_STATE, (state.value, ed2k_hash))
        if cursor.rowcount != 1:
            raise PersistenceError(f"download {ed2k_hash} introuvable (bug du code appelant)")

    def is_downloaded(self, ed2k_hash: str) -> bool:
        """``True`` si ce hash est déjà connu de ``downloads`` (dédup, spec §6)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_IS_DOWNLOADED, (ed2k_hash,)).fetchone()
        return row is not None

    def committed_bytes(self) -> int:
        """Somme des ``size_bytes`` des downloads ACTIFS (plafond disque, spec §7)."""
        with wrap_sqlite_errors():
            return int(self._connection.execute(_COMMITTED_BYTES).fetchone()[0])

    def active_states(self) -> dict[str, DownloadState]:
        """Map hash→état de TOUS les downloads connus (le monitor réconcilie dessus)."""
        with wrap_sqlite_errors():
            rows = self._connection.execute(_ACTIVE_STATES).fetchall()
        return {row[0]: DownloadState(row[1]) for row in rows}
