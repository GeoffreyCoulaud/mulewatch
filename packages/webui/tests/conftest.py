"""Fixtures pytest partagées pour le webui — schémas DDL sans import emule_indexer."""

import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers DDL (module-level, non exportés)
# ---------------------------------------------------------------------------


def _apply_catalog_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE files (
            ed2k_hash TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL,
            aich_hash TEXT,
            CHECK (LENGTH(ed2k_hash) = 32 AND ed2k_hash NOT GLOB '*[^0-9a-f]*')
        );

        CREATE TABLE file_observations (
            id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL,
            filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            source_count INTEGER NOT NULL,
            complete_source_count INTEGER NOT NULL,
            media_length_sec INTEGER,
            bitrate_kbps INTEGER,
            codec TEXT,
            file_type TEXT,
            raw_meta TEXT NOT NULL,
            keyword TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            node_id TEXT NOT NULL
        );

        CREATE TABLE match_decisions (
            id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL,
            target_id TEXT NOT NULL,
            rule_name TEXT NOT NULL,
            tier TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            node_id TEXT NOT NULL
        );

        CREATE TABLE file_verifications (
            id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL,
            verdict TEXT NOT NULL,
            real_meta TEXT,
            checks TEXT,
            verified_at TEXT NOT NULL,
            node_id TEXT NOT NULL
        );

        PRAGMA journal_mode=WAL;
    """)


def _apply_local_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE node_runtime (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE verification_tasks (
            id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'in_progress', 'done', 'dead_letter')),
            attempts INTEGER NOT NULL DEFAULT 0,
            enqueued_at TEXT NOT NULL,
            claimed_at TEXT,
            lease_until TEXT
        );

        CREATE TABLE downloads (
            ed2k_hash TEXT PRIMARY KEY,
            target_id TEXT NOT NULL,
            state TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            completed_at TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE scheduler_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        PRAGMA journal_mode=WAL;
    """)


# ---------------------------------------------------------------------------
# Fixtures pytest
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog_db(tmp_path: Path) -> Path:
    """Crée une base catalog.db au schéma réaliste (WAL, vide), retourne le Path."""
    path = tmp_path / "catalog.db"
    with sqlite3.connect(path) as conn:
        _apply_catalog_schema(conn)
        conn.commit()
    return path


@pytest.fixture
def local_db(tmp_path: Path) -> Path:
    """Crée une base local.db au schéma réaliste (WAL, vide), retourne le Path."""
    path = tmp_path / "local.db"
    with sqlite3.connect(path) as conn:
        _apply_local_schema(conn)
        conn.commit()
    return path
