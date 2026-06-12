"""``SqliteLocalStateRepository`` : identité du nœud + file de tâches (spec §4/§6).

La file (spec MVP §12) : claim atomique FIFO sous ``BEGIN IMMEDIATE`` + ``RETURNING``
(défense en profondeur — le writer unique est garanti par le déploiement, spec §3),
lease configurable au constructeur, retries bornés → ``dead_letter`` (« poison
probable », le plan E en fera une alerte), enqueue idempotent (l'index UNIQUE partiel
sur les statuts actifs absorbe le doublon : ``ON CONFLICT … DO NOTHING``, vérifié
empiriquement avec cible de conflit explicite, SQLite 3.47.1). ``done``/``dead_letter``
restent en table (historique local, reconstructible — spec §6).

``node_id`` (spec §3) : UUID généré au premier appel, persisté dans ``node_runtime``
avec ``created_at``, stable ensuite (seed du scheduler §6 MVP + tag des observations).
"""

import sqlite3
import uuid
from contextlib import suppress
from datetime import timedelta

from emule_indexer.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors
from emule_indexer.ports.local_state_repository import ClaimedTask

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


class SqliteLocalStateRepository:
    """Implémentation SQLite du port ``LocalStateRepository`` (satisfaction STRUCTURELLE)."""

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
        """UUID créé (et persisté avec ``created_at``) au premier appel, stable ensuite."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_NODE_ID).fetchone()
            if row is not None:
                return str(row[0])
            generated = str(uuid.uuid4())
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(_INSERT_NODE_IDENTITY, (generated, utc_iso(self._clock())))
                self._connection.execute("COMMIT")
            except sqlite3.Error:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        return generated

    def enqueue_verification(self, ed2k_hash: str) -> bool:
        """``True`` si une tâche a été créée ; ``False`` si une tâche ACTIVE existait déjà."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(_ENQUEUE, (ed2k_hash, utc_iso(self._clock())))
        return cursor.rowcount == 1

    def claim_verification(self) -> ClaimedTask | None:
        """Claim atomique FIFO (``BEGIN IMMEDIATE`` + ``RETURNING``) ; file vide → ``None``.

        FIFO = ``ORDER BY enqueued_at, id`` (l'ISO UTC à largeur fixe rend l'ordre
        lexicographique chronologique ; ``id`` départage les égalités d'horloge).
        ``attempts`` est compté AU CLAIM (spec §6).
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
