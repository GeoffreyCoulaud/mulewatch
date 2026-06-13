"""``SqliteCatalogRepository`` : ``FileObservation``/``MatchDecision`` → lignes durables.

L'adapter stamppe ce que le domaine ignore (spec data-model §3) : ``observed_at``/
``decided_at`` (horloge injectable, ``utc_now`` par défaut) et ``node_id`` (fourni au
constructeur — le plan C le lira du ``LocalStateRepository``). ``raw_meta`` est sérialisé
en JSON LISTE de paires (``[["0x0308", "0"], …]``), ordre du fil et doublons préservés,
``ensure_ascii=False``, pas de tri (spec §3). ``record_observation`` fait UNE transaction
(spec §4) : ``INSERT OR IGNORE`` dans ``files`` (première vue gagne) puis ``INSERT`` dans
``file_observations`` — la taille OBSERVÉE est TOUJOURS écrite dans l'observation
(déviation 1, spec §5 : une anomalie de taille ne doit pas devenir invisible).

Le canon du hash (32 hex minuscules, canon v0.5.0) est validé EN PYTHON avant la
transaction : ``INSERT OR IGNORE`` avale silencieusement une violation de CHECK
(comportement SQLite documenté) — sans cette garde, un hash non canonique ne serait
arrêté que par le pragma ``foreign_keys`` (diagnostic opaque), et une connexion sans
ce pragma commettrait une observation ORPHELINE. Le rollback attrape ``BaseException``
(même discipline que ``connection._open``) : une panne NON-sqlite au binding (p.ex.
``UnicodeEncodeError`` sur un surrogate isolé) ne doit pas laisser la connexion
``in_transaction`` — sinon le repository serait définitivement cassé.
"""

import json
import re
import sqlite3
from contextlib import suppress

from emule_indexer.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError, wrap_sqlite_errors
from emule_indexer.domain.matching.engine import (
    DecisionRecord,
    DownloadCandidate,
    MatchDecision,
)
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import ObservedFile

_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")

_INSERT_FILE = "INSERT OR IGNORE INTO files (ed2k_hash, size_bytes, aich_hash) VALUES (?, ?, NULL)"

_INSERT_OBSERVATION = """
INSERT INTO file_observations (
    ed2k_hash, filename, size_bytes, source_count, complete_source_count,
    media_length_sec, bitrate_kbps, codec, file_type, raw_meta,
    keyword, observed_at, node_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_DECISION = """
INSERT INTO match_decisions (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)
VALUES (?, ?, ?, ?, ?, ?)
"""

# Dernier verdict connu pour un hash (anti-redondance, spec orchestration §3). Tri par
# (decided_at, id) DÉCROISSANT : decided_at à largeur fixe rend l'ordre lexicographique
# chronologique ; id départage deux décisions de la même microseconde (l'INSERT le plus
# récent a l'id le plus grand). L'index idx_match_decisions_ed2k_hash sert le filtre.
_SELECT_LAST_DECISION = """
SELECT target_id, rule_name, tier FROM match_decisions
WHERE ed2k_hash = ?
ORDER BY decided_at DESC, id DESC
LIMIT 1
"""

# Hash dont le DERNIER verdict est tier=download (spec download §5). Fenêtre :
# ROW_NUMBER par hash, ordre (decided_at, id) DÉCROISSANT (le plus récent = rang 1) ; on ne
# garde que rang 1 ET tier='download'. Tri stable par hash pour un résultat déterministe.
# La fenêtre fait actuellement un scan complet ; un index couvrant
# (ed2k_hash, decided_at DESC, id DESC) la servirait (prématuré à l'échelle MVP — note seule).
_SELECT_DOWNLOAD_DECISIONS = """
SELECT ed2k_hash, target_id FROM (
    SELECT
        ed2k_hash, target_id, tier,
        ROW_NUMBER() OVER (PARTITION BY ed2k_hash ORDER BY decided_at DESC, id DESC) AS rn
    FROM match_decisions
) WHERE rn = 1 AND tier = 'download'
ORDER BY ed2k_hash
"""

# Dernière observation d'un hash (nom + taille pour le lien ed2k, spec download §5).
_SELECT_LAST_OBSERVATION = """
SELECT filename, size_bytes FROM file_observations
WHERE ed2k_hash = ?
ORDER BY observed_at DESC, id DESC
LIMIT 1
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
        if not _CANONICAL_HASH_RE.fullmatch(observation.ed2k_hash):
            raise PersistenceError(f"hash eD2k non canonique : {observation.ed2k_hash!r}")
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
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None:
        """INSERT seul (autocommit) ; fichier inconnu → FK violée → ``PersistenceError``.

        Seules les 3 colonnes de ``MatchDecision`` sont persistées (spec moteur) ;
        ``explanation`` est de l'explicabilité runtime, JAMAIS une colonne.
        """
        if not _CANONICAL_HASH_RE.fullmatch(ed2k_hash):
            raise PersistenceError(f"hash eD2k non canonique : {ed2k_hash!r}")
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_DECISION,
                (
                    ed2k_hash,
                    decision.target_id,
                    decision.rule_name,
                    decision.tier,
                    utc_iso(self._clock()),
                    self._node_id,
                ),
            )

    def last_decision(self, ed2k_hash: str) -> DecisionRecord | None:
        """Dernier verdict connu pour ce hash, ou ``None`` (jamais décidé) — LECTURE.

        Anti-redondance (spec orchestration §3) : l'application compare ce
        ``DecisionRecord`` au verdict frais et ne ré-``record_decision`` que s'il diffère.
        Le hash n'est PAS validé canonique ici : c'est une lecture inoffensive (un hash
        non canonique ne matche simplement rien → ``None``).
        """
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_LAST_DECISION, (ed2k_hash,)).fetchone()
        if row is None:
            return None
        return DecisionRecord(target_id=row[0], rule_name=row[1], tier=row[2])

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        """Hash dont le DERNIER verdict est tier=download, à rejouer (download §5) — LECTURE."""
        with wrap_sqlite_errors():
            rows = self._connection.execute(_SELECT_DOWNLOAD_DECISIONS).fetchall()
        return tuple(DownloadCandidate(ed2k_hash=row[0], target_id=row[1]) for row in rows)

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        """Dernière observation d'un hash (nom+taille pour le lien ed2k), ou ``None`` — LECTURE."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_LAST_OBSERVATION, (ed2k_hash,)).fetchone()
        if row is None:
            return None
        return ObservedFile(filename=row[0], size_bytes=row[1])
