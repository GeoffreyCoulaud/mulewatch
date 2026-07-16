"""TDD tests for catalog migration 0003 - read-path indices for the webui explorer.

These composite indices drive the webui's latest-per-group reads (decisions per
(hash, target), latest verification per hash) by index, and give ``file_verifications`` its
first ``ed2k_hash`` index.

0003 also claimed such an index lets SQLite satisfy a PARTITION/ORDER window without a
separate sort; the query plan refutes it (the window still needs a TEMP B-TREE for the
trailing ORDER BY terms). Only a seek-shaped read exploits the index, which is what
migration 0004 pairs with the one it adds on ``file_observations``.
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


def test_match_decisions_has_hash_target_decided_index(connection: sqlite3.Connection) -> None:
    assert _index_columns(connection, "idx_match_decisions_hash_target_decided") == [
        "ed2k_hash",
        "target_id",
        "decided_at",
    ]


def test_file_verifications_has_hash_verified_index(connection: sqlite3.Connection) -> None:
    assert _index_columns(connection, "idx_file_verifications_hash_verified") == [
        "ed2k_hash",
        "verified_at",
    ]
