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


def test_open_ro_keeps_temp_in_memory(catalog_db: Path) -> None:
    """The reader must not depend on scratch disk. The hardened webui container mounts a tiny
    (32 MB) ``/tmp`` tmpfs, while the window-function reads materialize larger temp b-trees;
    with the default file-backed temp store SQLite spills there, overflows the tmpfs, and
    raises ``OperationalError: database or disk is full`` (verified on the real 250k-observation
    catalog). ``temp_store=MEMORY`` (2) keeps temp in the process heap, bounded by the
    container mem_limit, instead of on a scratch disk it does not have."""
    conn = open_ro(catalog_db)
    try:
        assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2  # 2 == MEMORY
    finally:
        conn.close()
