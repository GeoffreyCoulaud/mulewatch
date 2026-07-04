import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from catalog_matching.engine import (
    DecisionRecord,
    Explanation,
    MatchDecision,
)
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.domain.observation import FileObservation

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    """Horloge fausse qui avance d'1 min par lecture (pour ordonner decided_at)."""

    def __init__(self) -> None:
        self._now = datetime(2026, 6, 12, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _observation() -> FileObservation:
    return FileObservation(
        ed2k_hash=_HASH,
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )


def _decision(rule_name: str, tier: str) -> MatchDecision:
    return MatchDecision(
        target_id="062A",
        rule_name=rule_name,
        tier=tier,
        explanation=Explanation(
            target_id="062A", rules_fired=(rule_name,), tokens_matched=(), coverage_values=()
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


def test_last_decision_is_none_when_never_decided(connection: sqlite3.Connection) -> None:
    repository = SqliteCatalogRepository(connection, _NODE)
    repository.record_observation(_observation())
    assert repository.last_decision(_HASH) is None


def test_last_decision_returns_the_most_recent_record(connection: sqlite3.Connection) -> None:
    repository = SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())
    repository.record_observation(_observation())
    repository.record_decision(_HASH, _decision("keroro_large", "catalog"))
    repository.record_decision(_HASH, _decision("id_segment_exact", "download"))
    assert repository.last_decision(_HASH) == DecisionRecord(
        target_id="062A", rule_name="id_segment_exact", tier="download"
    )


def test_last_decision_for_unknown_hash_is_none(connection: sqlite3.Connection) -> None:
    repository = SqliteCatalogRepository(connection, _NODE)
    assert repository.last_decision("f" * 32) is None
