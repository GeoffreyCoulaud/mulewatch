"""TDD tests for catalog migration 0004 - the latest-observation seek index.

``file_observations`` is append-only and re-observes the same file on every cycle, so it
grows without bound (1.18M rows for 1402 files on the real node, ~844 observations per
file) while the webui explorer only ever wants the LATEST row per file. This composite
index lets that read seek straight to a file's newest observation instead of walking the
whole table. Migration 0003 deliberately left it out on the assumption that there were
"few observations per file"; the real node refutes that.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite.connection import open_catalog


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


def _index_columns(connection: sqlite3.Connection, index: str) -> list[str]:
    return [str(row[2]) for row in connection.execute(f"PRAGMA index_info({index})")]


def test_file_observations_has_hash_observed_index(connection: sqlite3.Connection) -> None:
    assert _index_columns(connection, "idx_file_observations_hash_observed") == [
        "ed2k_hash",
        "observed_at",
    ]
