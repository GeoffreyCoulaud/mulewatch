"""Helpers de test partagés pour le script de merge (catalogues réels, jamais ``:memory:``).

WAL exige un fichier réel (``open_catalog`` refuse ``:memory:``) : chaque helper crée un
``catalog.db`` sur disque via ``open_catalog`` (schéma + triggers append-only), insère des
lignes données par colonnes explicites, ferme, rend le chemin. Style aligné sur
``tests/adapters/persistence_sqlite/test_append_only.py`` (INSERT directs, FK : ``files``/
``sources`` avant les journaux).
"""

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

from emule_indexer.adapters.persistence_sqlite.connection import open_catalog

# Colonnes hors id, dans l'ordre du schéma (0001_initial.sql) — pour les INSERT directs et
# les lectures de clé naturelle dans les assertions.
FILE_COLUMNS = ("ed2k_hash", "size_bytes", "aich_hash")
SOURCE_COLUMNS = ("user_hash", "client_name", "client_version")
FILE_OBSERVATION_COLUMNS = (
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
)
SOURCE_OBSERVATION_COLUMNS = (
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
)
MATCH_DECISION_COLUMNS = (
    "ed2k_hash",
    "target_id",
    "rule_name",
    "tier",
    "decided_at",
    "node_id",
)
FILE_VERIFICATION_COLUMNS = (
    "ed2k_hash",
    "verdict",
    "real_meta",
    "checks",
    "verified_at",
    "node_id",
)
FILE_OBSERVATION_RANGE_COLUMNS = (
    "ed2k_hash",
    "bucket",
    "filenames",
    "node_ids",
    "observation_count",
    "first_observed_at",
    "last_observed_at",
    "source_count_min",
    "source_count_max",
    "source_count_sum",
    "complete_source_count_min",
    "complete_source_count_max",
    "complete_source_count_sum",
)

_COLUMNS_BY_TABLE: Mapping[str, Sequence[str]] = {
    "files": FILE_COLUMNS,
    "sources": SOURCE_COLUMNS,
    "file_observations": FILE_OBSERVATION_COLUMNS,
    "source_observations": SOURCE_OBSERVATION_COLUMNS,
    "match_decisions": MATCH_DECISION_COLUMNS,
    "file_verifications": FILE_VERIFICATION_COLUMNS,
    "file_observation_ranges": FILE_OBSERVATION_RANGE_COLUMNS,
}

# Un hash eD2k canonique (32 hex minuscules) par lettre — satisfait le CHECK sur files.
HASH_A = "a" * 32
HASH_B = "b" * 32
HASH_C = "c" * 32


def hash_for(letter: str) -> str:
    """Un hash canonique de 32 caractères répétant ``letter`` (1 seul caractère hex)."""
    return letter * 32


def insert_rows(
    connection: sqlite3.Connection, table: str, rows: Sequence[Mapping[str, object]]
) -> None:
    """INSERT direct de ``rows`` dans ``table`` (colonnes explicites, ordre du schéma)."""
    columns = _COLUMNS_BY_TABLE[table]
    placeholders = ", ".join("?" for _ in columns)
    statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    for row in rows:
        connection.execute(statement, tuple(row.get(column) for column in columns))


def make_catalog(
    path: Path, content: Mapping[str, Sequence[Mapping[str, object]]] | None = None
) -> Path:
    """Crée un ``catalog.db`` réel à ``path`` et insère ``content`` (par table, ordre FK).

    ``content`` mappe un nom de table → des lignes (dict colonne→valeur). On insère dans
    l'ordre FK (``files``/``sources`` avant les journaux) pour que les références soient
    satisfaites. Rend ``path`` pour chaînage.
    """
    connection = open_catalog(path)
    try:
        if content is not None:
            for table in (
                "files",
                "sources",
                "file_observations",
                "source_observations",
                "match_decisions",
                "file_verifications",
                "file_observation_ranges",
            ):
                rows = content.get(table)
                if rows:
                    insert_rows(connection, table, rows)
    finally:
        connection.close()
    return path


def count(path: Path, table: str) -> int:
    """Nombre de lignes de ``table`` dans le catalogue ``path``."""
    connection = open_catalog(path)
    try:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def rows_without_id(path: Path, table: str) -> list[tuple[object, ...]]:
    """Toutes les lignes de ``table`` (colonnes hors ``id``, ordre du schéma), triées."""
    columns = _COLUMNS_BY_TABLE[table]
    connection = open_catalog(path)
    try:
        cursor = connection.execute(f"SELECT {', '.join(columns)} FROM {table}")
        return sorted(cursor.fetchall(), key=lambda row: tuple(str(value) for value in row))
    finally:
        connection.close()


def ids(path: Path, table: str) -> list[int]:
    """Les ``id`` (réassignés) de ``table``, triés croissant."""
    connection = open_catalog(path)
    try:
        cursor = connection.execute(f"SELECT id FROM {table} ORDER BY id")
        return [int(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()
