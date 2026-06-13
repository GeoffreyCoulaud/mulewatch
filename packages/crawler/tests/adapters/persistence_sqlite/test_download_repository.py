import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_local
from emule_indexer.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.domain.download.states import DownloadState

_A = "a" * 32
_B = "b" * 32


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteDownloadRepository:
    return SqliteDownloadRepository(connection)


def test_record_queued_inserts_a_new_download(repository: SqliteDownloadRepository) -> None:
    assert repository.record_queued(_A, "S2E062A", 100) is True
    assert repository.is_downloaded(_A) is True


def test_record_queued_is_dedup_safe(repository: SqliteDownloadRepository) -> None:
    assert repository.record_queued(_A, "S2E062A", 100) is True
    assert repository.record_queued(_A, "S2E062A", 100) is False  # doublon ignoré


def test_is_downloaded_is_false_for_unknown_hash(repository: SqliteDownloadRepository) -> None:
    assert repository.is_downloaded(_A) is False


def test_set_state_updates_the_state(repository: SqliteDownloadRepository) -> None:
    repository.record_queued(_A, "S2E062A", 100)
    repository.set_state(_A, DownloadState.DOWNLOADING)
    assert repository.active_states()[_A] is DownloadState.DOWNLOADING


def test_set_state_to_completed_stamps_completed_at(
    connection: sqlite3.Connection,
) -> None:
    repository = SqliteDownloadRepository(connection, clock=_AdvancingClock())
    repository.record_queued(_A, "S2E062A", 100)
    repository.set_state(_A, DownloadState.COMPLETED)
    stamped = connection.execute(
        "SELECT completed_at FROM downloads WHERE ed2k_hash = ?", (_A,)
    ).fetchone()[0]
    assert stamped is not None


def test_set_state_non_completed_leaves_completed_at_null(
    repository: SqliteDownloadRepository, connection: sqlite3.Connection
) -> None:
    repository.record_queued(_A, "S2E062A", 100)
    repository.set_state(_A, DownloadState.DOWNLOADING)
    stamped = connection.execute(
        "SELECT completed_at FROM downloads WHERE ed2k_hash = ?", (_A,)
    ).fetchone()[0]
    assert stamped is None


def test_set_state_on_unknown_hash_raises(repository: SqliteDownloadRepository) -> None:
    with pytest.raises(PersistenceError):
        repository.set_state(_A, DownloadState.DOWNLOADING)


def test_committed_bytes_sums_only_active_downloads(
    repository: SqliteDownloadRepository,
) -> None:
    repository.record_queued(_A, "S2E062A", 100)  # queued (actif)
    repository.record_queued(_B, "S2E063A", 200)  # downloading (actif)
    repository.set_state(_B, DownloadState.DOWNLOADING)
    assert repository.committed_bytes() == 300
    repository.set_state(_A, DownloadState.COMPLETED)  # terminal → ne compte plus
    assert repository.committed_bytes() == 200
    repository.set_state(_B, DownloadState.FAILED)  # terminal → ne compte plus non plus
    assert repository.committed_bytes() == 0


def test_committed_bytes_is_zero_on_empty(repository: SqliteDownloadRepository) -> None:
    assert repository.committed_bytes() == 0


def test_active_states_maps_hash_to_state(repository: SqliteDownloadRepository) -> None:
    repository.record_queued(_A, "S2E062A", 100)
    repository.record_queued(_B, "S2E063A", 200)
    repository.set_state(_B, DownloadState.QUARANTINED)
    states = repository.active_states()
    assert states == {_A: DownloadState.QUEUED, _B: DownloadState.QUARANTINED}


def test_record_queued_is_atomic_on_injected_failure(
    repository: SqliteDownloadRepository, connection: sqlite3.Connection
) -> None:
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON downloads"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    with pytest.raises(PersistenceError, match="panne injectée"):
        repository.record_queued(_A, "S2E062A", 100)
    assert repository.is_downloaded(_A) is False
