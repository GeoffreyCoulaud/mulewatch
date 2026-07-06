"""TDD tests for catalog migration 0003 — read-path indices for the webui explorer.

The webui reads latest-per-group (decisions/observations/verifications) via window
functions; these composite indices let SQLite satisfy each PARTITION/ORDER without a
separate sort, and give ``file_verifications`` its first ``ed2k_hash`` index.
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
