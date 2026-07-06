"""Tests for the backfill use-case ``reevaluate_catalog`` (spec §7, plan Task 5).

Real engine + real SQLite catalog repo (same convention as ``test_decisions.py``: "real
repos on tmp_path"); only ``signal``/``telemetry`` are fakes (``RecordingSignal``/
``RecordingTelemetry``). Each hash under test is seeded via ``catalog.record_observation``
first so it shows up in ``iter_reevaluation_rows`` (the source the backfill iterates).
"""

import sqlite3

import pytest

from catalog_matching.engine import MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.application.reevaluate_catalog import ReevalSummary, reevaluate_catalog
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.observation import FileObservation
from tests.application.fakes import RecordingSignal, RecordingTelemetry

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_CAT_NAME = "keroro something.avi"


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
async def test_two_changed_rows_are_all_evaluated_and_written(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))  # never decided -> will change
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))  # never decided -> will change
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=2, written=2)
    tiers = dict(
        catalog_connection.execute("SELECT ed2k_hash, tier FROM match_decisions").fetchall()
    )
    assert tiers == {_HASH_DL: "download", _HASH_CAT: "catalog"}
    assert len(telemetry.events) == 2
    # Iteration is ORDER BY ed2k_hash: "31d6..." sorts before "aaaa..." (ASCII '3' < 'a').
    assert signal.signalled == [_HASH_DL, DOWNLOAD_NUDGE_SUBJECT, _HASH_CAT]


@pytest.mark.asyncio
async def test_unchanged_row_is_evaluated_but_not_written(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    candidate = _obs(_HASH_CAT, _CAT_NAME).to_candidate()
    decisions = engine.evaluate(candidate)
    assert decisions  # non-empty
    catalog.record_decision(_HASH_CAT, decisions[0])  # pre-seed the "already correct" verdict
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=1, written=0)
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert telemetry.events == []
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_repository_error_on_one_row_is_absorbed_and_sweep_continues(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    # TEST trigger: makes the match_decisions INSERT fail for ONE hash only -> that row's
    # helper call raises RepositoryError, the other row must still be processed.
    catalog_connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON match_decisions"
        f" WHEN NEW.ed2k_hash = '{_HASH_DL}'"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=2, written=1)
    rows = catalog_connection.execute("SELECT ed2k_hash, tier FROM match_decisions").fetchall()
    assert rows == [(_HASH_CAT, "catalog")]
    assert catalog.last_decisions(_HASH_DL) == {}


@pytest.mark.asyncio
async def test_empty_catalogue_yields_zero_summary(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=0, written=0)
    assert telemetry.events == []
    assert signal.signalled == []
