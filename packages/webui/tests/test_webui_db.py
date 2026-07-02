"""Tests for catalog_webui.adapters.db.open_ro — TDD (spec W-D2 / §16)."""

import sqlite3
from pathlib import Path

import pytest

from catalog_webui.adapters.db import open_ro


def test_open_ro_reads_rows(catalog_db: Path) -> None:
    with sqlite3.connect(catalog_db) as seed:
        seed.execute(
            "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)",
            ("a" * 32, 10),
        )
        seed.commit()
    conn = open_ro(catalog_db)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
        assert count == 1
    finally:
        conn.close()


def test_open_ro_refuses_writes(catalog_db: Path) -> None:
    conn = open_ro(catalog_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)",
                ("b" * 32, 1),
            )
    finally:
        conn.close()


def test_open_ro_rows_are_dict_addressable(catalog_db: Path) -> None:
    conn = open_ro(catalog_db)
    try:
        row = conn.execute("SELECT 1 AS n").fetchone()
        assert row["n"] == 1
    finally:
        conn.close()
