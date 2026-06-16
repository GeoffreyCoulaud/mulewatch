"""``merge_catalogs`` : fusion idempotente de N ``catalog.db`` en une sortie unique.

Mécanisme (spec fusion §3/§4) : la sortie est créée/ouverte via ``open_catalog`` (schéma
+ triggers append-only, migration ``0001`` — AUCUN DDL dupliqué). Pour chaque source : on
l'``ATTACH`` (hors transaction — SQLite refuse d'attacher dans une transaction ouverte),
puis DANS UNE transaction explicite (``BEGIN``…``COMMIT``, ``ROLLBACK`` best-effort sur
erreur) on copie les 6 tables dans l'**ordre FK** (identités d'abord), puis ``COMMIT`` et
``DETACH`` (hors transaction). Une source à moitié copiée n'est jamais commitée.

Idempotence (spec §4) :
- ``files``/``sources`` (PK de contenu globale) → ``INSERT OR IGNORE`` (première vue
  gagne) ; JAMAIS ``OR REPLACE`` (= DELETE + INSERT → heurte le trigger append-only).
- les 4 journaux (``id`` LOCAL, sans sens global) → colonnes explicites SANS ``id`` (la
  base réattribue l'``id``) + dédup par **clé naturelle complète** via ``WHERE NOT EXISTS``,
  comparaisons à l'opérateur ``IS`` (et non ``=``) car des colonnes sont nullable
  (``NULL = NULL`` est faux en SQL → ré-insertion → non idempotent ; ``NULL IS NULL`` est
  vrai). Re-merger = no-op (chaque ligne source a déjà son jumeau exact).

Le SQL est en **constantes Python** (cohérent avec ``catalog_repository.py`` ; pas de
nouveau ``.sql`` à linter par sqlfluff). On n'écrit JAMAIS dans la source (que des SELECT).
"""

import sqlite3
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.merge.errors import MergeError

# Alias d'attache de la source courante (une seule à la fois → on reste à 1 base attachée
# quel que soit N, bien sous le plafond SQLITE_MAX_ATTACHED de 10, spec §8).
_SRC = "src"

# --- Tables d'identité-contenu : INSERT OR IGNORE, colonnes explicites (pas de SELECT *). ---

_COPY_FILES = (
    f"INSERT OR IGNORE INTO main.files (ed2k_hash, size_bytes, aich_hash) "
    f"SELECT ed2k_hash, size_bytes, aich_hash FROM {_SRC}.files"
)

_COPY_SOURCES = (
    f"INSERT OR IGNORE INTO main.sources (user_hash, client_name, client_version) "
    f"SELECT user_hash, client_name, client_version FROM {_SRC}.sources"
)


def _copy_journal(table: str, columns: Sequence[str]) -> str:
    """SQL d'une copie de journal : colonnes explicites (sans ``id``) + dédup ``IS``.

    La clé naturelle = TOUTES les colonnes du journal sauf ``id``. Deux niveaux de dédup,
    tous deux à la granularité « clé naturelle complète » (§4.2) :

    - ``SELECT DISTINCT`` collapse les doublons **internes à la source** en un seul passage
      (le ``WHERE NOT EXISTS`` ne les verrait pas : au moment du SELECT, ``main`` est encore
      vide pour cette source, donc deux lignes jumelles internes passeraient toutes les
      deux). ``DISTINCT`` traite ``NULL`` comme égal à ``NULL`` (cohérent avec ``IS``) et ne
      collapse JAMAIS deux lignes légitimement distinctes (elles diffèrent sur ≥ 1 colonne).
      C'est la normalisation des doublons « at-least-once » d'un seul catalogue (§1/§8).
    - ``WHERE NOT EXISTS`` (comparaisons ``IS``, NULL-safe) dédupe contre la **destination**
      (cross-source et re-merge). Re-merge ⇒ 0 insertion.
    """
    projection = ", ".join(columns)
    not_exists = "\n      AND ".join(f"d.{column} IS s.{column}" for column in columns)
    return (
        f"INSERT INTO main.{table} ({projection})\n"
        f"SELECT DISTINCT {projection}\n"
        f"FROM {_SRC}.{table} AS s\n"
        f"WHERE NOT EXISTS (\n"
        f"    SELECT 1 FROM main.{table} AS d\n"
        f"    WHERE {not_exists}\n"
        f")"
    )


_COPY_FILE_OBSERVATIONS = _copy_journal(
    "file_observations",
    (
        "ed2k_hash",
        "filename",
        "size_bytes",
        "source_count",
        "complete_source_count",
        "media_length_sec",
        "bitrate_kbps",
        "codec",
        "file_type",
        "raw_meta",
        "keyword",
        "observed_at",
        "node_id",
    ),
)

_COPY_SOURCE_OBSERVATIONS = _copy_journal(
    "source_observations",
    (
        "user_hash",
        "ed2k_hash",
        "ip",
        "port",
        "nickname",
        "client_name",
        "client_version",
        "country",
        "id_type",
        "has_complete_file",
        "origin",
        "raw_meta",
        "observed_at",
        "node_id",
    ),
)

_COPY_MATCH_DECISIONS = _copy_journal(
    "match_decisions",
    ("ed2k_hash", "target_id", "rule_name", "tier", "decided_at", "node_id"),
)

_COPY_FILE_VERIFICATIONS = _copy_journal(
    "file_verifications",
    ("ed2k_hash", "verdict", "real_meta", "checks", "verified_at", "node_id"),
)

# Ordre FK IMPÉRATIF (spec §4.3) : identités (files, sources) AVANT les journaux qui les
# référencent. On exécute ces 6 copies pour UNE source dans UNE transaction.
_COPY_STATEMENTS = (
    _COPY_FILES,
    _COPY_SOURCES,
    _COPY_FILE_OBSERVATIONS,
    _COPY_SOURCE_OBSERVATIONS,
    _COPY_MATCH_DECISIONS,
    _COPY_FILE_VERIFICATIONS,
)


def merge_catalogs(output: Path, sources: Sequence[Path], *, dest_is_source: bool = False) -> None:
    """Fusionne ``sources`` dans ``output`` (créé/ouvert via ``open_catalog``), idempotent.

    ``output`` est ouvert via ``open_catalog`` : s'il est neuf, la migration ``0001`` pose
    le schéma + les triggers ; s'il existe déjà comme ``catalog.db`` valide, aucune
    migration n'est rejouée et le merge AJOUTE dedans (jamais de truncate).

    ``dest_is_source`` (mode ``--into``) : la sortie est elle-même l'une des ``sources`` ;
    on ne se ré-attache pas à soi-même (l'idempotence garantit qu'on ne s'y duplique rien),
    on saute donc la source dont le chemin résout vers ``output``.

    Toute ``sqlite3.Error`` (source corrompue, schéma incompatible, FK…) est enveloppée en
    ``MergeError`` (fail-fast, message clair). Le ``ROLLBACK`` est best-effort.
    """
    connection = open_catalog(output)
    try:
        output_resolved = Path(output).resolve()
        for source in sources:
            if dest_is_source and Path(source).resolve() == output_resolved:
                continue
            _merge_one(connection, source)
    finally:
        connection.close()


def _merge_one(connection: sqlite3.Connection, source: Path) -> None:
    """Attache ``source``, copie ses 6 tables dans UNE transaction, détache.

    ``ATTACH``/``DETACH`` sont HORS transaction (SQLite refuse d'attacher dans une
    transaction ouverte). La copie est enveloppée par ``BEGIN``/``COMMIT`` ; une erreur
    déclenche un ``ROLLBACK`` best-effort puis un ``DETACH`` best-effort, et remonte en
    ``MergeError`` — la sortie ne garde aucune copie partielle de cette source.
    """
    try:
        # Chemin RÉSOLU (cohérent avec le skip --into qui compare ``Path.resolve()``) :
        # évite un self-attach si la même base est passée sous deux formes de chemin.
        connection.execute(f"ATTACH DATABASE ? AS {_SRC}", (str(Path(source).resolve()),))
    except sqlite3.Error as error:
        raise MergeError(f"impossible d'attacher la source {source} : {error}") from error
    try:
        connection.execute("BEGIN")
        try:
            for statement in _COPY_STATEMENTS:
                connection.execute(statement)
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise MergeError(f"échec de la copie de {source} : {error}") from error
    finally:
        with suppress(sqlite3.Error):
            connection.execute(f"DETACH DATABASE {_SRC}")
