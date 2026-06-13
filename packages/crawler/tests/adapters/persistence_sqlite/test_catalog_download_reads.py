import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.domain.matching.engine import DownloadCandidate, Explanation, MatchDecision
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import ObservedFile

_A = "a" * 32
_B = "b" * 32
_C = "c" * 32
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _obs(hash_hex: str, *, name: str = "Keroro.avi", size: int = 100) -> FileObservation:
    return FileObservation(
        ed2k_hash=hash_hex,
        filename=name,
        size_bytes=size,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )


def _decision(tier: str) -> MatchDecision:
    return MatchDecision(
        target_id="S2E062A",
        rule_name="r",
        tier=tier,
        explanation=Explanation(
            target_id="S2E062A", rules_fired=("r",), tokens_matched=(), coverage_values=()
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())


def test_download_decisions_includes_hash_whose_latest_verdict_is_download(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("catalog"))
    repository.record_decision(_A, _decision("download"))  # plus récent = download
    assert repository.download_decisions() == (
        DownloadCandidate(ed2k_hash=_A, target_id="S2E062A"),
    )


def test_download_decisions_includes_a_single_download_only_decision(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("download"))  # une seule décision, = download
    assert repository.download_decisions() == (
        DownloadCandidate(ed2k_hash=_A, target_id="S2E062A"),
    )


def test_download_decisions_excludes_hash_whose_latest_verdict_is_not_download(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_B))
    repository.record_decision(_B, _decision("download"))
    repository.record_decision(_B, _decision("catalog"))  # plus récent = catalog
    assert repository.download_decisions() == ()


def test_download_decisions_isolates_per_hash(
    repository: SqliteCatalogRepository,
) -> None:
    # deux hash distincts dans la même requête : _A finit en download, _B finit en catalog.
    # La fenêtre PARTITION BY ed2k_hash doit les isoler → seul _A est rendu.
    repository.record_observation(_obs(_A))
    repository.record_observation(_obs(_B))
    repository.record_decision(_A, _decision("catalog"))
    repository.record_decision(_A, _decision("download"))  # _A latest = download
    repository.record_decision(_B, _decision("download"))
    repository.record_decision(_B, _decision("catalog"))  # _B latest = catalog
    assert repository.download_decisions() == (
        DownloadCandidate(ed2k_hash=_A, target_id="S2E062A"),
    )


def test_download_decisions_is_empty_with_no_decisions(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_C))
    assert repository.download_decisions() == ()


def test_last_observation_returns_filename_and_size(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A, name="Keroro 062A.avi", size=4242))
    assert repository.last_observation(_A) == ObservedFile(
        filename="Keroro 062A.avi", size_bytes=4242
    )


def test_last_observation_returns_the_most_recent(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A, name="old.avi", size=1))
    repository.record_observation(_obs(_A, name="new.avi", size=2))
    assert repository.last_observation(_A) == ObservedFile(filename="new.avi", size_bytes=2)


def test_last_observation_unknown_hash_is_none(repository: SqliteCatalogRepository) -> None:
    assert repository.last_observation(_A) is None
