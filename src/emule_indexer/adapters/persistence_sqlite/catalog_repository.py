"""``SqliteCatalogRepository`` : ``FileObservation``/``MatchDecision`` → lignes durables.

L'adapter stamppe ce que le domaine ignore (spec data-model §3) : ``observed_at``/
``decided_at`` (horloge injectable, ``utc_now`` par défaut) et ``node_id`` (fourni au
constructeur — le plan C le lira du ``LocalStateRepository``). ``raw_meta`` est sérialisé
en JSON LISTE de paires (``[["0x0308", "0"], …]``), ordre du fil et doublons préservés,
``ensure_ascii=False``, pas de tri (spec §3). ``record_observation`` fait UNE transaction
(spec §4) : ``INSERT OR IGNORE`` dans ``files`` (première vue gagne) puis ``INSERT`` dans
``file_observations`` — la taille OBSERVÉE est TOUJOURS écrite dans l'observation
(déviation 1, spec §5 : une anomalie de taille ne doit pas devenir invisible).
"""

import json
import sqlite3
from contextlib import suppress

from emule_indexer.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors
from emule_indexer.domain.observation import FileObservation

_INSERT_FILE = "INSERT OR IGNORE INTO files (ed2k_hash, size_bytes, aich_hash) VALUES (?, ?, NULL)"

_INSERT_OBSERVATION = """
INSERT INTO file_observations (
    ed2k_hash, filename, size_bytes, source_count, complete_source_count,
    media_length_sec, bitrate_kbps, codec, file_type, raw_meta,
    keyword, observed_at, node_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class SqliteCatalogRepository:
    """Implémentation SQLite du port ``CatalogRepository`` (satisfaction STRUCTURELLE)."""

    def __init__(
        self, connection: sqlite3.Connection, node_id: str, *, clock: Clock = utc_now
    ) -> None:
        self._connection = connection
        self._node_id = node_id
        self._clock = clock

    def record_observation(self, observation: FileObservation) -> None:
        """UNE transaction : fichier (première vue gagne) + observation stampée."""
        raw_meta = json.dumps(observation.raw_meta, ensure_ascii=False)
        observed_at = utc_iso(self._clock())
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN")
            try:
                self._connection.execute(
                    _INSERT_FILE, (observation.ed2k_hash, observation.size_bytes)
                )
                self._connection.execute(
                    _INSERT_OBSERVATION,
                    (
                        observation.ed2k_hash,
                        observation.filename,
                        observation.size_bytes,
                        observation.source_count,
                        observation.complete_source_count,
                        observation.media_length_sec,
                        observation.bitrate_kbps,
                        observation.codec,
                        observation.file_type,
                        raw_meta,
                        observation.keyword,
                        observed_at,
                        self._node_id,
                    ),
                )
                self._connection.execute("COMMIT")
            except sqlite3.Error:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
