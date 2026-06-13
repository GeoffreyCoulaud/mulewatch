import asyncio
import sqlite3

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.application.record_observations import record_observation
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.observation import FileObservation
from tests.application.fakes import RecordingSignal

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_DISCARD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"


def _obs(ed2k_hash: str, filename: str) -> FileObservation:
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


def test_observation_is_always_recorded_even_when_discarded(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    signal = RecordingSignal()
    changed = record_observation(
        _obs(_HASH_DISCARD, "random.txt"), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is False
    assert catalog_connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 1
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0
    assert signal.signalled == []


def test_new_verdict_is_persisted_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    signal = RecordingSignal()
    changed = record_observation(
        _obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is True
    assert catalog_connection.execute("SELECT tier FROM match_decisions").fetchone() == (
        "download",
    )
    assert signal.signalled == [_HASH_DL]


def test_unchanged_verdict_is_not_reappended_or_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    signal = RecordingSignal()
    observation = _obs(_HASH_CAT, "keroro something.avi")
    assert record_observation(observation, catalog=catalog, engine=engine, signal=signal) is True
    # Deuxième observation du MÊME fichier : même verdict catalog → pas de ré-append.
    assert record_observation(observation, catalog=catalog, engine=engine, signal=signal) is False
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    # Mais l'observation, elle, est re-persistée (re-observation périodique = le but).
    assert catalog_connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 2
    assert signal.signalled == [_HASH_CAT]  # une seule fois


def test_changed_verdict_is_reappended_and_nudged_again(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    signal = RecordingSignal()
    record_observation(
        _obs(_HASH_DL, "keroro something.avi"), catalog=catalog, engine=engine, signal=signal
    )
    # 2e vue du MÊME hash, nom DOWNLOAD → verdict change → ré-append + nudge.
    changed = record_observation(
        _obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is True
    tiers = [
        row[0]
        for row in catalog_connection.execute(
            "SELECT tier FROM match_decisions ORDER BY id"
        ).fetchall()
    ]
    assert tiers == ["catalog", "download"]
    assert signal.signalled == [_HASH_DL, _HASH_DL]


def test_persistence_error_is_absorbed_and_cycle_continues(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Trigger de TEST : fait échouer l'INSERT d'observation → RepositoryError absorbée.
    catalog_connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON file_observations"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    signal = RecordingSignal()
    changed = record_observation(
        _obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is False  # absorbée, le cycle continue
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_signal_consumer_awaits_the_nudge(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # Le hub EST consommé par un await (pas du code mort, DÉCISION 9) : un consommateur dort
    # sur le sujet et est réveillé par le nudge post-commit.
    signal = RecordingSignal()
    waiter = asyncio.create_task(signal.wait(_HASH_DL))
    await asyncio.sleep(0)
    assert not waiter.done()
    record_observation(_obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal)
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()
