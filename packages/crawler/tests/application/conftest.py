"""Fixtures partagées des tests application : moteur réel + repos SQLite réels (spec §8)."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.validation import parse_matcher_config, parse_targets

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_NODE = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def engine() -> MatchingEngine:
    """Moteur RÉEL sur la config/targets canoniques (corpus golden, fixtures partagées)."""
    config = parse_matcher_config(load_yaml(_FIXTURES / "canonical_config.yaml"))
    targets = parse_targets(load_yaml(_FIXTURES / "canonical_targets.yaml"))
    return MatchingEngine(config, targets)


@pytest.fixture
def catalog_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    yield connection
    connection.close()


@pytest.fixture
def catalog(catalog_connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(catalog_connection, _NODE)
