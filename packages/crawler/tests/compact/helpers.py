"""Test helpers for compaction: seed/read of file_observation_ranges.

The other tables (files, file_observations) go through tests.merge.helpers.make_catalog
(real file-based catalogs, never :memory:). Here, what is specific to the rollup.
"""

from collections.abc import Sequence
from pathlib import Path

from mulewatch.adapters.persistence_sqlite.connection import open_catalog

RANGE_COLUMNS = (
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


def insert_ranges(path: Path, rows: Sequence[tuple[object, ...]]) -> None:
    placeholders = ", ".join("?" for _ in RANGE_COLUMNS)
    statement = (
        f"INSERT INTO file_observation_ranges ({', '.join(RANGE_COLUMNS)}) VALUES ({placeholders})"
    )
    connection = open_catalog(path)
    try:
        for row in rows:
            connection.execute(statement, row)
    finally:
        connection.close()


def read_ranges(path: Path) -> list[tuple[object, ...]]:
    connection = open_catalog(path)
    try:
        cursor = connection.execute(
            f"SELECT {', '.join(RANGE_COLUMNS)} FROM file_observation_ranges"
        )
        return sorted(cursor.fetchall())
    finally:
        connection.close()


def read_observation_days(path: Path) -> list[str]:
    """The `observed_at` of the RECENT raw data kept (to check the window)."""
    connection = open_catalog(path)
    try:
        cursor = connection.execute(
            "SELECT observed_at FROM file_observations ORDER BY observed_at"
        )
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()
