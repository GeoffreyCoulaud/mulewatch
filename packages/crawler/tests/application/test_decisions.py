"""Tests for the shared decision helper (spec §7): set diff keyed by (hash, target_id).

Real engine + real SQLite catalog repo (mirrors ``test_record_observations.py``: "real repos
on tmp_path"); only ``signal``/``telemetry`` are fakes. ``record_decision_if_changed`` writes
to ``match_decisions`` (FK-constrained on ``files``), so each hash is seeded via
``record_observation`` first, EXCEPT the never-matched case (never writes → FK never applies).
"""

import sqlite3

import pytest

from catalog_matching.engine import DecisionRecord, MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.application.decisions import record_decision_if_changed
from mulewatch.application.record_observations import record_observation
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.observability.events import DecisionRecorded
from mulewatch.domain.observation import FileObservation
from mulewatch.domain.retraction import RETRACTED_TIER
from tests.application.fakes import RecordingSignal, RecordingTelemetry

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_DISCARD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_HASH_NEVER = "cccccccccccccccccccccccccccccccc"
_HASH_MULTI = "dddddddddddddddddddddddddddddddd"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_CAT_NAME = "keroro something.avi"
_DISCARD_NAME = "random.txt"
_MULTI_NAME = "Keroro 062 teletoon.avi"  # bare number + source marker → 062A + 062B download
# A file that pins a STABLE, specific target via its unique title (062A/notify/title_review),
# unlike _CAT_NAME whose catch-all target_id is an arbitrary min-key over the present targets.
_NOTIFY_NAME = "Keroro Les demoiselles cambrioleuses.avi"


def _obs(ed2k_hash: str, filename: str) -> FileObservation:
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


async def _record(
    ed2k_hash: str,
    filename: str,
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    signal: RecordingSignal,
    telemetry: RecordingTelemetry,
) -> int:
    return await record_decision_if_changed(
        ed2k_hash,
        _obs(ed2k_hash, filename).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )


@pytest.mark.asyncio
async def test_new_decision_is_recorded_emitted_signalled_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_DL, _DL_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert catalog_connection.execute("SELECT target_id, tier FROM match_decisions").fetchone() == (
        "062A",
        "download",
    )
    assert telemetry.events == [DecisionRecorded(target_id="062A", tier="download")]
    assert signal.signalled == [_HASH_DL, DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_multi_segment_file_records_both_segments_then_is_idempotent(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_MULTI, _MULTI_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_MULTI, _MULTI_NAME, catalog, engine, signal, telemetry)
    assert written == 2
    assert catalog_connection.execute(
        "SELECT target_id, rule_name, tier FROM match_decisions ORDER BY id"
    ).fetchall() == [
        ("062A", "numero_nu_confirmed", "download"),
        ("062B", "numero_nu_confirmed", "download"),
    ]
    assert telemetry.events == [
        DecisionRecorded(target_id="062A", tier="download"),
        DecisionRecorded(target_id="062B", tier="download"),
    ]
    assert signal.signalled == [
        _HASH_MULTI,
        DOWNLOAD_NUDGE_SUBJECT,
        _HASH_MULTI,
        DOWNLOAD_NUDGE_SUBJECT,
    ]
    again = await _record(_HASH_MULTI, _MULTI_NAME, catalog, engine, signal, telemetry)
    assert again == 0
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_changed_decision_is_reappended_emitted_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _NOTIFY_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_DL, _NOTIFY_NAME, catalog, engine, signal, telemetry)
    written = await _record(_HASH_DL, _DL_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    tiers = [
        row[0]
        for row in catalog_connection.execute(
            "SELECT tier FROM match_decisions ORDER BY id"
        ).fetchall()
    ]
    assert tiers == ["notify", "download"]
    assert [type(e).__name__ for e in telemetry.events] == ["DecisionRecorded", "DecisionRecorded"]
    assert signal.signalled == [_HASH_DL, _HASH_DL, DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_unchanged_decision_is_not_reappended_emitted_or_signalled(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    first = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    second = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    assert (first, second) == (1, 0)
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert len(telemetry.events) == 1
    assert signal.signalled == [_HASH_CAT]


@pytest.mark.asyncio
async def test_was_matched_then_none_retracts_that_target_without_nudge(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _NOTIFY_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_CAT, _NOTIFY_NAME, catalog, engine, signal, telemetry)
    signalled_before = list(signal.signalled)
    written = await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert catalog.last_decisions(_HASH_CAT) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }
    assert telemetry.events[-1] == DecisionRecorded(target_id="062A", tier=RETRACTED_TIER)
    assert signal.signalled == signalled_before
    assert DOWNLOAD_NUDGE_SUBJECT not in signal.signalled


@pytest.mark.asyncio
async def test_multi_segment_then_discard_retracts_both_segments(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_MULTI, _MULTI_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_MULTI, _MULTI_NAME, catalog, engine, signal, telemetry)
    written = await _record(_HASH_MULTI, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 2
    assert catalog.last_decisions(_HASH_MULTI) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER),
        "062B": DecisionRecord(target_id="062B", rule_name="", tier=RETRACTED_TIER),
    }
    assert telemetry.events[-2:] == [
        DecisionRecorded(target_id="062A", tier=RETRACTED_TIER),
        DecisionRecorded(target_id="062B", tier=RETRACTED_TIER),
    ]


@pytest.mark.asyncio
async def test_already_retracted_then_none_is_a_no_op(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    rows_after = catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0]
    events_after = len(telemetry.events)
    written = await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 0
    assert (
        catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0]
        == rows_after
    )
    assert len(telemetry.events) == events_after


@pytest.mark.asyncio
async def test_never_matched_then_none_is_a_no_op(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_NEVER, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 0
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0
    assert telemetry.events == []
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_non_download_tier_decision_does_not_nudge_the_download_subject(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert signal.signalled == [_HASH_CAT]
    assert DOWNLOAD_NUDGE_SUBJECT not in signal.signalled


@pytest.mark.asyncio
async def test_record_observation_retracts_a_reobserved_now_discarded_file(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    first = await record_observation(
        _obs(_HASH_DL, _DL_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    second = await record_observation(
        _obs(_HASH_DL, _DISCARD_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert (first, second) == (1, 1)
    assert catalog.last_decisions(_HASH_DL) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }
    assert catalog_connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 2
