"""``SqliteSchedulerStateRepository`` : état d'ordonnancement en KV (spec orchestration §4/§7).

Implémente STRUCTURELLEMENT le port ``SchedulerStateRepository``. Stocke trois clés dans la
table ``scheduler_state`` de ``local.db`` : ``cycle_index`` (entier sérialisé en TEXT),
``last_full_cycle_at`` (ISO-8601 UTC) et ``channel_backoff`` (map JSON des
:class:`ChannelBackoff` par clé instance/instance:canal). ``write_cycle_state`` fait UN
UPSERT atomique de l'index + horodatage sous ``BEGIN IMMEDIATE`` (l'index n'avance qu'en FIN
de cycle, spec §7 : atomicité = un crash laisse l'ancien index, donc rejoue ce cycle).
``save_channel_backoff`` remplace ENTIÈREMENT la map (snapshot du registre, écrit au même
moment que ``write_cycle_state`` — voir ``run_search_cycle``). ``read_cycle_index`` rend
``0`` si la clé est absente ; ``load_channel_backoff`` rend un dict vide.

``scheduler_state`` n'est PAS append-only (état mutable, pas le catalogue) : pas de
triggers — l'UPSERT ``ON CONFLICT … DO UPDATE`` est licite.
"""

import json
import sqlite3
from contextlib import suppress
from datetime import datetime
from typing import Any

from emule_indexer.adapters.persistence_sqlite.connection import utc_iso
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors
from emule_indexer.ports.scheduler_state_repository import ChannelBackoff

_SELECT_CYCLE_INDEX = "SELECT value FROM scheduler_state WHERE key = 'cycle_index'"

_SELECT_BACKOFF = "SELECT value FROM scheduler_state WHERE key = 'channel_backoff'"

_UPSERT = """
INSERT INTO scheduler_state (key, value) VALUES (?, ?)
ON CONFLICT (key) DO UPDATE SET value = excluded.value
"""


class SqliteSchedulerStateRepository:
    """Implémentation SQLite du port ``SchedulerStateRepository`` (satisfaction STRUCTURELLE)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def read_cycle_index(self) -> int:
        """Index du prochain cycle, ``0`` si jamais écrit (premier démarrage)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_CYCLE_INDEX).fetchone()
        return 0 if row is None else int(row[0])

    def write_cycle_state(self, cycle_index: int, last_full_cycle_at: datetime) -> None:
        """UPSERT atomique de l'index + horodatage (FIN de cycle, spec §7).

        ``last_full_cycle_at`` est un ``datetime`` aware ; ``utc_iso`` le formate (et REFUSE
        un naïf, contrat de ``Clock``).
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
        """Relit la map de backoff persistée, ``{}`` si jamais écrite (premier démarrage).

        Chaque entrée JSON ``{"attempts": int, "retry_after": str}`` est reconstruite en
        :class:`ChannelBackoff`. Lecture inoffensive : aucune transaction explicite.
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
        """Remplace ENTIÈREMENT la map persistée (snapshot du registre, FIN de cycle).

        Sérialisé en JSON trié (``sort_keys`` → diff stable, déterminisme). UPSERT atomique
        sous ``BEGIN IMMEDIATE`` (même discipline que ``write_cycle_state``).
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
