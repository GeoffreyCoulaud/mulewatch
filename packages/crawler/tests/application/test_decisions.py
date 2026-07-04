"""Tests for the shared decision helper (spec §4, plan Task 2).

Real engine + real SQLite catalog repo (mirrors ``test_record_observations.py``'s
convention, spec §8: "real repos on tmp_path"); only ``signal``/``telemetry`` are fakes
(``RecordingSignal``/``RecordingTelemetry``). ``record_decision_if_changed`` writes to
``match_decisions`` via ``record_decision``/``record_retraction``, both FK-constrained on
``files`` — each hash under test is seeded via ``catalog.record_observation`` first (same
seeding discipline as Task 1's adapter tests), EXCEPT where a hash is never seeded on
purpose (the "never matched" case never writes, so the FK never comes into play).
"""

import sqlite3

import pytest

from catalog_matching.engine import DecisionRecord, MatchingEngine
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.application.decisions import record_decision_if_changed
from emule_indexer.application.record_observations import record_observation
from emule_indexer.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from emule_indexer.domain.observability.events import DecisionRecorded
from emule_indexer.domain.observation import FileObservation
from emule_indexer.domain.retraction import RETRACTED_TIER
from tests.application.fakes import RecordingSignal, RecordingTelemetry

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_DISCARD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_HASH_NEVER = "cccccccccccccccccccccccccccccccc"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_CAT_NAME = "keroro something.avi"
_DISCARD_NAME = "random.txt"


def _obs(ed2k_hash: str, filename: str) -> FileObservation:
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


@pytest.mark.asyncio
async def test_new_decision_is_recorded_emitted_signalled_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))  # seed the FK
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    written = await record_decision_if_changed(
        _HASH_DL,
        _obs(_HASH_DL, _DL_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    assert written is True
    assert catalog_connection.execute("SELECT tier FROM match_decisions").fetchone() == (
        "download",
    )
    assert telemetry.events == [DecisionRecorded(target_id="062A", tier="download")]
    assert signal.signalled == [_HASH_DL, DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_changed_decision_is_reappended_emitted_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _CAT_NAME))  # seed the FK
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    await record_decision_if_changed(
        _HASH_DL,
        _obs(_HASH_DL, _CAT_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    written = await record_decision_if_changed(
        _HASH_DL,
        _obs(_HASH_DL, _DL_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    assert written is True
    tiers = [
        row[0]
        for row in catalog_connection.execute(
            "SELECT tier FROM match_decisions ORDER BY id"
        ).fetchall()
    ]
    assert tiers == ["catalog", "download"]
    assert [type(e).__name__ for e in telemetry.events] == ["DecisionRecorded", "DecisionRecorded"]
    assert signal.signalled == [_HASH_DL, _HASH_DL, DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_unchanged_decision_is_not_reappended_emitted_or_signalled(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))  # seed the FK
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    candidate = _obs(_HASH_CAT, _CAT_NAME).to_candidate()
    first = await record_decision_if_changed(
        _HASH_CAT, candidate, catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    second = await record_decision_if_changed(
        _HASH_CAT, candidate, catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert (first, second) == (True, False)
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert len(telemetry.events) == 1
    assert signal.signalled == [_HASH_CAT]


@pytest.mark.asyncio
async def test_was_matched_then_none_appends_a_retraction_without_nudge(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))  # seed the FK
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    await record_decision_if_changed(
        _HASH_CAT,
        _obs(_HASH_CAT, _CAT_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    signalled_before_retraction = list(signal.signalled)
    written = await record_decision_if_changed(
        _HASH_CAT,
        _obs(_HASH_CAT, _DISCARD_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    assert written is True
    assert catalog.last_decision(_HASH_CAT) == DecisionRecord(
        target_id="", rule_name="", tier=RETRACTED_TIER
    )
    assert telemetry.events[-1] == DecisionRecorded(target_id="", tier=RETRACTED_TIER)
    # No hash nudge and no download nudge on retraction.
    assert signal.signalled == signalled_before_retraction
    assert DOWNLOAD_NUDGE_SUBJECT not in signal.signalled


@pytest.mark.asyncio
async def test_already_retracted_then_none_is_a_no_op(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))  # seed the FK
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    await record_decision_if_changed(
        _HASH_CAT,
        _obs(_HASH_CAT, _CAT_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    await record_decision_if_changed(  # first None: was matched -> retracts
        _HASH_CAT,
        _obs(_HASH_CAT, _DISCARD_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    row_count_after_retraction = catalog_connection.execute(
        "SELECT count(*) FROM match_decisions"
    ).fetchone()[0]
    events_after_retraction = len(telemetry.events)
    written = await record_decision_if_changed(  # second None: already retracted -> no-op
        _HASH_CAT,
        _obs(_HASH_CAT, _DISCARD_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    assert written is False
    assert (
        catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0]
        == row_count_after_retraction
    )
    assert len(telemetry.events) == events_after_retraction


@pytest.mark.asyncio
async def test_never_matched_then_none_is_a_no_op(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Deliberately NOT seeded: last_decision is a harmless read regardless of the `files`
    # FK, and this path never writes, so the FK never comes into play.
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    written = await record_decision_if_changed(
        _HASH_NEVER,
        _obs(_HASH_NEVER, _DISCARD_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    assert written is False
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0
    assert telemetry.events == []
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_non_download_tier_decision_does_not_nudge_the_download_subject(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))  # seed the FK
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    written = await record_decision_if_changed(
        _HASH_CAT,
        _obs(_HASH_CAT, _CAT_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    assert written is True
    assert signal.signalled == [_HASH_CAT]
    assert DOWNLOAD_NUDGE_SUBJECT not in signal.signalled


@pytest.mark.asyncio
async def test_record_observation_retracts_a_reobserved_now_discarded_file(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    first = await record_observation(
        _obs(_HASH_DL, _DL_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    # Re-observed under a name the CURRENT matcher no longer matches (spec §4, the live-path
    # improvement): the file is retracted, not silently ignored.
    second = await record_observation(
        _obs(_HASH_DL, _DISCARD_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert (first, second) == (True, True)
    assert catalog.last_decision(_HASH_DL) == DecisionRecord(
        target_id="", rule_name="", tier=RETRACTED_TIER
    )
    assert catalog_connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 2
