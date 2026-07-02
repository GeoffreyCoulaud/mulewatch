import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.domain.observation import FileObservation

_A = "a" * 32
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _obs(hash_hex: str) -> FileObservation:
    return FileObservation(
        ed2k_hash=hash_hex,
        filename="Keroro.avi",
        size_bytes=100,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())


def test_record_verification_inserts_a_row(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_obs(_A))  # FK: the file must exist
    repository.record_verification(_A, "unverified", {"duration": 42}, ["type_sniff"])
    row = connection.execute(
        "SELECT ed2k_hash, verdict, real_meta, checks, node_id FROM file_verifications"
    ).fetchone()
    assert row[0] == _A
    assert row[1] == "unverified"
    assert json.loads(row[2]) == {"duration": 42}
    assert json.loads(row[3]) == ["type_sniff"]
    assert row[4] == _NODE


def test_record_verification_stamps_verified_at(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_verification(_A, "unverified", {}, [])
    stamped = connection.execute("SELECT verified_at FROM file_verifications").fetchone()[0]
    assert stamped is not None


def test_record_verification_serializes_empty_meta_and_checks(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_verification(_A, "error", {}, [])
    row = connection.execute("SELECT real_meta, checks FROM file_verifications").fetchone()
    assert json.loads(row[0]) == {}
    assert json.loads(row[1]) == []


def test_record_verification_preserves_non_ascii(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_verification(_A, "unverified", {"titre": "accentué"}, [])
    real_meta = connection.execute("SELECT real_meta FROM file_verifications").fetchone()[0]
    assert "accentué" in real_meta  # ensure_ascii=False


def test_record_verification_rejects_non_canonical_hash(
    repository: SqliteCatalogRepository,
) -> None:
    with pytest.raises(PersistenceError):
        repository.record_verification("NOT-canonical", "unverified", {}, [])


def test_record_verification_unknown_file_raises(repository: SqliteCatalogRepository) -> None:
    # FK violated (file never observed) → PersistenceError (wrap_sqlite_errors).
    with pytest.raises(PersistenceError):
        repository.record_verification("f" * 32, "unverified", {}, [])
