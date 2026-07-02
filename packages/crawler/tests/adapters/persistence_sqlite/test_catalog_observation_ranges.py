"""catalog/0002: rollup table file_observation_ranges (append-only + CHECK).

Append-only ENFORCED BY THE DATABASE (like the 0001 tables): UPDATE/DELETE → RAISE(ABORT) →
sqlite3.IntegrityError. The CHECKs (observation_count > 0, first <= last, LENGTH(bucket) = 10)
also surface as sqlite3.IntegrityError. Real file required (WAL; open_catalog refuses :memory:).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_catalog

_HASH = "a" * 32
_RANGE = (
    "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
    " observation_count, first_observed_at, last_observed_at, source_count_min,"
    " source_count_max, source_count_sum, complete_source_count_min,"
    " complete_source_count_max, complete_source_count_sum) VALUES"
    f" ('{_HASH}', '2026-03-01', '[\"f.avi\"]', '[\"n\"]', 3,"
    " '2026-03-01T00:00:00.000000+00:00', '2026-03-01T23:00:00.000000+00:00',"
    " 1, 9, 15, 0, 2, 3)"
)


@pytest.fixture
def seeded(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    connection.execute(f"INSERT INTO files (ed2k_hash, size_bytes) VALUES ('{_HASH}', 1)")
    connection.execute(_RANGE)
    yield connection
    connection.close()


def test_insert_is_allowed(seeded: sqlite3.Connection) -> None:
    assert seeded.execute("SELECT count(*) FROM file_observation_ranges").fetchone()[0] == 1


def test_update_is_rejected(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="file_observation_ranges is append-only"):
        seeded.execute("UPDATE file_observation_ranges SET observation_count = 4")


def test_delete_is_rejected(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="file_observation_ranges is append-only"):
        seeded.execute("DELETE FROM file_observation_ranges")


def test_check_observation_count_positive(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
            " observation_count, first_observed_at, last_observed_at, source_count_min,"
            " source_count_max, source_count_sum, complete_source_count_min,"
            " complete_source_count_max, complete_source_count_sum) VALUES"
            f" ('{_HASH}', '2026-03-02', '[]', '[]', 0, '2026-03-02', '2026-03-02',"
            " 0, 0, 0, 0, 0, 0)"
        )


def test_check_bucket_length(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
            " observation_count, first_observed_at, last_observed_at, source_count_min,"
            " source_count_max, source_count_sum, complete_source_count_min,"
            " complete_source_count_max, complete_source_count_sum) VALUES"
            f" ('{_HASH}', '2026-03', '[]', '[]', 1, '2026-03', '2026-03', 0, 0, 0, 0, 0, 0)"
        )


def test_check_first_before_last(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
            " observation_count, first_observed_at, last_observed_at, source_count_min,"
            " source_count_max, source_count_sum, complete_source_count_min,"
            " complete_source_count_max, complete_source_count_sum) VALUES"
            f" ('{_HASH}', '2026-03-03', '[]', '[]', 1, '2026-03-03T05:00', '2026-03-03T01:00',"
            " 0, 0, 0, 0, 0, 0)"
        )
