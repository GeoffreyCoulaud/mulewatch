import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.domain.observation import FileObservation

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_NODE = "11111111-2222-3333-4444-555555555555"
_FROZEN_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_FROZEN_ISO = "2026-06-11T12:00:00.000000+00:00"


def _frozen_clock() -> datetime:
    return _FROZEN_NOW


def _observation(
    *,
    filename: str = "Keroro 062A.avi",
    size_bytes: int = 234567890,
    media_length_sec: int | None = None,
    bitrate_kbps: int | None = None,
    codec: str | None = None,
    file_type: str | None = None,
) -> FileObservation:
    # média None par défaut (EC n'expose AUCUNE métadonnée média — rapport 2026-06-11) ;
    # raw_meta avec DOUBLON, ordre wire et non-ASCII (les trois propriétés à préserver).
    return FileObservation(
        ed2k_hash=_HASH,
        filename=filename,
        size_bytes=size_bytes,
        source_count=5,
        complete_source_count=2,
        keyword="keroro",
        media_length_sec=media_length_sec,
        bitrate_kbps=bitrate_kbps,
        codec=codec,
        file_type=file_type,
        raw_meta=(("0x0308", "0"), ("0x0308", "0"), ("0x0999", "mystère")),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_frozen_clock)


def test_record_observation_round_trip(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    file_row = connection.execute("SELECT ed2k_hash, size_bytes, aich_hash FROM files").fetchone()
    assert file_row == (_HASH, 234567890, None)
    row = connection.execute(
        "SELECT ed2k_hash, filename, size_bytes, source_count, complete_source_count,"
        " media_length_sec, bitrate_kbps, codec, file_type, raw_meta, keyword,"
        " observed_at, node_id FROM file_observations"
    ).fetchone()
    assert row == (
        _HASH,
        "Keroro 062A.avi",
        234567890,
        5,
        2,
        None,
        None,
        None,
        None,
        '[["0x0308", "0"], ["0x0308", "0"], ["0x0999", "mystère"]]',
        "keroro",
        _FROZEN_ISO,
        _NODE,
    )


def test_raw_meta_preserves_order_duplicates_and_non_ascii(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    stored = connection.execute("SELECT raw_meta FROM file_observations").fetchone()[0]
    assert "mystère" in stored  # ensure_ascii=False : l'accent est stocké TEL QUEL
    assert json.loads(stored) == [["0x0308", "0"], ["0x0308", "0"], ["0x0999", "mystère"]]


def test_record_observation_twice_first_seen_wins_in_files(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    # Même hash, taille et nom DIFFÉRENTS (entrée hostile, déviation 1 spec §5).
    repository.record_observation(_observation(filename="leurre.avi", size_bytes=999))
    assert connection.execute("SELECT size_bytes FROM files").fetchall() == [(234567890,)]
    observed_sizes = connection.execute(
        "SELECT size_bytes FROM file_observations ORDER BY id"
    ).fetchall()
    assert observed_sizes == [(234567890,), (999,)]  # l'anomalie reste VISIBLE


def test_record_observation_with_media_metadata_and_default_clock(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        repository = SqliteCatalogRepository(connection, _NODE)  # horloge par défaut (utc_now)
        repository.record_observation(
            _observation(media_length_sec=1474, bitrate_kbps=1200, codec="xvid", file_type="Video")
        )
        row = connection.execute(
            "SELECT media_length_sec, bitrate_kbps, codec, file_type, observed_at"
            " FROM file_observations"
        ).fetchone()
        assert row[:4] == (1474, 1200, "xvid", "Video")
        stamped = datetime.fromisoformat(row[4])
        assert stamped.tzinfo == UTC  # l'horloge par défaut stamppe bien de l'UTC aware
    finally:
        connection.close()


def test_record_observation_is_one_transaction(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # Panne injectée ENTRE les deux INSERT : un trigger de TEST fait échouer le second.
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON file_observations"
        " WHEN NEW.filename = '__boom__'"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    with pytest.raises(PersistenceError, match="panne injectée"):
        repository.record_observation(_observation(filename="__boom__"))
    # ATOMICITÉ : le INSERT OR IGNORE dans files a été défait avec la transaction.
    assert connection.execute("SELECT count(*) FROM files").fetchone()[0] == 0
