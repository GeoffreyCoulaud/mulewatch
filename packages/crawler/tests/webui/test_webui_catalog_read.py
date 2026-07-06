"""TDD tests for CatalogReader — coverage, filtered explorer, detail (spec W-D6 / §6)."""

import sqlite3
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite.reader import open_reader
from mulewatch.webui.adapters.catalog_read import CatalogReader

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed(db: Path) -> None:
    """Populate the database with a file, an observation, a decision."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)",
            ("a" * 32, 100),
        )
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "a" * 32,
                "keroro_062.avi",
                100,
                5,
                2,
                "[]",
                "keroro",
                "2026-06-22T10:00:00.000000+00:00",
                "n1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                "a" * 32,
                "062A",
                "id_segment_exact",
                "download",
                "2026-06-22T10:00:01.000000+00:00",
                "n1",
            ),
        )
        conn.commit()


def _seed_with_verdict(db: Path) -> None:
    """Add a verification verdict to the seeded file."""
    _seed(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO file_verifications"
            " (ed2k_hash, verdict, real_meta, checks, verified_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                "a" * 32,
                "ok",
                None,
                None,
                "2026-06-22T11:00:00.000000+00:00",
                "n1",
            ),
        )
        conn.commit()


def _seed_whole_episode(db: Path) -> None:
    """A single whole-episode file (hash a*32) satisfying BOTH segments 072A + 072B (two
    current decisions at tier ``download``) plus one per-file ``clean`` verdict — the core
    multi-target fixture (spec §9). Standalone: never combine with ``_seed`` (same hash)."""
    h = "a" * 32
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)",
            (h, 170_000_000),
        )
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                h,
                "keroro_072.avi",
                170_000_000,
                7,
                3,
                "[]",
                "keroro",
                "2026-07-01T10:00:00.000000+00:00",
                "n1",
            ),
        )
        for tid in ("072A", "072B"):
            conn.execute(
                "INSERT INTO match_decisions"
                " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    h,
                    tid,
                    "numero_nu_confirmed",
                    "download",
                    "2026-07-01T10:00:01.000000+00:00",
                    "n1",
                ),
            )
        conn.execute(
            "INSERT INTO file_verifications"
            " (ed2k_hash, verdict, real_meta, checks, verified_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "clean", None, None, "2026-07-01T12:00:00.000000+00:00", "n1"),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Tests: coverage
# ---------------------------------------------------------------------------


def test_target_coverage_groups_by_target(catalog_db: Path) -> None:
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    coverage = reader.target_coverage()
    assert coverage["062A"] == [("a" * 32, "download")]


def test_target_coverage_empty_db_returns_empty(catalog_db: Path) -> None:
    reader = CatalogReader(open_reader(catalog_db))
    coverage = reader.target_coverage()
    assert coverage == {}


def test_target_coverage_multiple_files_same_target(catalog_db: Path) -> None:
    """Two files matching the same target_id → list of length 2."""
    with sqlite3.connect(catalog_db) as conn:
        for suffix in ("a", "b"):
            h = suffix * 32
            conn.execute(
                "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)",
                (h, 100),
            )
            conn.execute(
                "INSERT INTO match_decisions"
                " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (h, "062A", "rule", "download", "2026-06-22T10:00:00.000000+00:00", "n1"),
            )
        conn.commit()
    reader = CatalogReader(open_reader(catalog_db))
    coverage = reader.target_coverage()
    assert len(coverage["062A"]) == 2


def test_target_coverage_whole_episode_contributes_to_both_targets(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    coverage = CatalogReader(open_reader(catalog_db)).target_coverage()
    assert coverage["072A"] == [("a" * 32, "download")]
    assert coverage["072B"] == [("a" * 32, "download")]


def test_target_coverage_ignores_legacy_empty_target_sentinel(catalog_db: Path) -> None:
    h = "e" * 32
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 100))
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "090A", "id_segment_exact", "download", "2026-07-05T10:00:00.000000+00:00", "n1"),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "", "", "retracted", "2026-07-05T11:00:00.000000+00:00", "n1"),
        )
        conn.commit()
    coverage = CatalogReader(open_reader(catalog_db)).target_coverage()
    assert coverage["090A"] == [(h, "download")]
    assert "" not in coverage


# ---------------------------------------------------------------------------
# Tests: explorer — filters present / absent
# ---------------------------------------------------------------------------


def test_list_files_no_filter_returns_all(catalog_db: Path) -> None:
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(target=None, tier=None, verdict=None, query=None, page=1)
    assert len(rows) == 1
    assert rows[0].ed2k_hash == "a" * 32
    assert rows[0].filename == "keroro_062.avi"
    assert rows[0].source_count == 5


def test_list_files_filter_by_target(catalog_db: Path) -> None:
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    hit = reader.list_files(target="062A", tier=None, verdict=None, query=None, page=1)
    miss = reader.list_files(target="001A", tier=None, verdict=None, query=None, page=1)
    assert len(hit) == 1
    assert miss == []


def test_list_files_filter_by_tier(catalog_db: Path) -> None:
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    hit = reader.list_files(target=None, tier="download", verdict=None, query=None, page=1)
    miss = reader.list_files(target=None, tier="notify", verdict=None, query=None, page=1)
    assert len(hit) == 1
    assert miss == []


def test_list_files_filter_by_verdict(catalog_db: Path) -> None:
    _seed_with_verdict(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    hit = reader.list_files(target=None, tier=None, verdict="ok", query=None, page=1)
    miss = reader.list_files(target=None, tier=None, verdict="malicious", query=None, page=1)
    assert len(hit) == 1
    assert miss == []


def test_list_files_no_verdict_still_returns_file(catalog_db: Path) -> None:
    """A file without verification appears when verdict=None."""
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(target=None, tier=None, verdict=None, query=None, page=1)
    assert len(rows) == 1


def test_list_files_filter_by_query(catalog_db: Path) -> None:
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    hit = reader.list_files(target=None, tier=None, verdict=None, query="keroro", page=1)
    miss = reader.list_files(target=None, tier=None, verdict=None, query="unknown", page=1)
    assert len(hit) == 1
    assert miss == []


def test_list_files_page_two_is_empty(catalog_db: Path) -> None:
    """Page 2 is empty when fewer than PAGE_SIZE results."""
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(target=None, tier=None, verdict=None, query=None, page=2)
    assert rows == []


# ---------------------------------------------------------------------------
# Tests: list_files / count_files — one row per file, decisions aggregated (spec §9)
# ---------------------------------------------------------------------------


def test_list_files_whole_episode_is_one_row_with_two_decisions(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    rows = CatalogReader(open_reader(catalog_db)).list_files(
        target=None, tier=None, verdict=None, query=None, page=1
    )
    assert len(rows) == 1
    assert [(d.target_id, d.tier) for d in rows[0].decisions] == [
        ("072A", "download"),
        ("072B", "download"),
    ]
    assert rows[0].last_verdict == "clean"


def test_list_files_filter_by_one_target_returns_whole_episode(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    rows = CatalogReader(open_reader(catalog_db)).list_files(
        target="072B", tier=None, verdict=None, query=None, page=1
    )
    assert len(rows) == 1
    assert [d.target_id for d in rows[0].decisions] == ["072A", "072B"]


def test_list_files_unmatched_file_has_empty_decisions(catalog_db: Path) -> None:
    _seed_unmatched(catalog_db)
    [row] = CatalogReader(open_reader(catalog_db)).list_files(
        target=None, tier=None, verdict=None, query=None, page=1
    )
    assert row.decisions == ()


def test_count_files_whole_episode_counts_as_one_file(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    matched, total = CatalogReader(open_reader(catalog_db)).count_files(
        target=None, tier=None, verdict=None, query=None
    )
    assert (matched, total) == (1, 1)


# ---------------------------------------------------------------------------
# Tests: detail
# ---------------------------------------------------------------------------


def test_file_detail_carries_observations_and_decisions(catalog_db: Path) -> None:
    _seed(catalog_db)
    detail = CatalogReader(open_reader(catalog_db)).file_detail("a" * 32)
    assert detail is not None
    assert detail.size_bytes == 100
    assert len(detail.decisions) == 1
    assert detail.decisions[0].target_id == "062A"
    assert len(detail.observations) == 1


def test_file_detail_unknown_hash_is_none(catalog_db: Path) -> None:
    _seed(catalog_db)
    assert CatalogReader(open_reader(catalog_db)).file_detail("f" * 32) is None


def test_file_detail_with_verifications(catalog_db: Path) -> None:
    _seed_with_verdict(catalog_db)
    detail = CatalogReader(open_reader(catalog_db)).file_detail("a" * 32)
    assert detail is not None
    assert len(detail.verifications) == 1
    assert detail.verifications[0].verdict == "ok"


def test_file_detail_retracted_target_is_no_decision(catalog_db: Path) -> None:
    """A file whose LATEST decision is the crawler's retraction sentinel exposes NO decision
    from ``file_detail`` — identical to an unmatched file (spec §9). The earlier
    (pre-retraction) real decision must not leak through."""
    _seed_retracted(catalog_db)
    detail = CatalogReader(open_reader(catalog_db)).file_detail("c" * 32)
    assert detail is not None
    assert detail.decisions == ()


def test_file_detail_no_decision(catalog_db: Path) -> None:
    """Detail works even without a decision (unmatched file)."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)",
            ("b" * 32, 200),
        )
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "b" * 32,
                "unknown.avi",
                200,
                1,
                0,
                "[]",
                "unknown",
                "2026-06-22T09:00:00.000000+00:00",
                "n2",
            ),
        )
        conn.commit()
    detail = CatalogReader(open_reader(catalog_db)).file_detail("b" * 32)
    assert detail is not None
    assert detail.decisions == ()
    assert detail.size_bytes == 200


def test_file_detail_whole_episode_lists_both_decisions(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    detail = CatalogReader(open_reader(catalog_db)).file_detail("a" * 32)
    assert detail is not None
    assert [d.target_id for d in detail.decisions] == ["072A", "072B"]


# ---------------------------------------------------------------------------
# Tests: list_files with multiple combined filters
# ---------------------------------------------------------------------------


def test_list_files_combined_target_and_tier_filters(catalog_db: Path) -> None:
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    hit = reader.list_files(target="062A", tier="download", verdict=None, query=None, page=1)
    miss = reader.list_files(target="062A", tier="notify", verdict=None, query=None, page=1)
    assert len(hit) == 1
    assert miss == []


@pytest.mark.parametrize("page", [1, 2])
def test_list_files_pagination(catalog_db: Path, page: int) -> None:
    """Verify pagination doesn't crash (page 1 = results, page 2 = empty)."""
    _seed(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(target=None, tier=None, verdict=None, query=None, page=page)
    if page == 1:
        assert len(rows) == 1
    else:
        assert rows == []


# ---------------------------------------------------------------------------
# Tests: "latest per hash" — tie-break on decided_at then id
# ---------------------------------------------------------------------------


def test_target_coverage_uses_latest_decision_per_hash(catalog_db: Path) -> None:
    """Same hash with two decisions (T1 < T2) → target_coverage returns T2's tier."""
    h = "a" * 32
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 100))
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "062A", "rule", "catalog", "2026-06-22T10:00:00.000000+00:00", "n1"),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "062A", "rule", "download", "2026-06-22T11:00:00.000000+00:00", "n1"),
        )
        conn.commit()
    coverage = CatalogReader(open_reader(catalog_db)).target_coverage()
    assert coverage["062A"] == [(h, "download")]


def test_target_coverage_omits_retracted(catalog_db: Path) -> None:
    """A file whose latest decision is a retraction contributes to NO target's coverage."""
    _seed(catalog_db)
    _seed_retracted(catalog_db)
    coverage = CatalogReader(open_reader(catalog_db)).target_coverage()
    assert coverage["062A"] == [("a" * 32, "download")]
    assert "063A" not in coverage  # the retracted file's earlier (now stale) target


def test_coverage_tie_break_on_id(catalog_db: Path) -> None:
    """Same hash, same decided_at, two different tiers → the larger id wins."""
    h = "b" * 32
    ts = "2026-06-22T10:00:00.000000+00:00"
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 200))
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "062A", "rule", "catalog", ts, "n1"),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "062A", "rule", "download", ts, "n1"),
        )
        conn.commit()
    coverage = CatalogReader(open_reader(catalog_db)).target_coverage()
    assert coverage["062A"] == [(h, "download")]


def test_file_detail_observations_include_media_fields_none(catalog_db: Path) -> None:
    """ObservationRow.media_length_sec and bitrate_kbps are None when absent from the SELECT."""
    _seed(catalog_db)
    detail = CatalogReader(open_reader(catalog_db)).file_detail("a" * 32)
    assert detail is not None
    assert len(detail.observations) == 1
    obs = detail.observations[0]
    assert obs.media_length_sec is None
    assert obs.bitrate_kbps is None


def test_file_detail_observations_include_media_fields_present(catalog_db: Path) -> None:
    """ObservationRow.media_length_sec and bitrate_kbps are filled when present."""
    h = "d" * 32
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 150))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count, complete_source_count,"
            " media_length_sec, bitrate_kbps, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                h,
                "keroro_media.avi",
                150,
                3,
                1,
                1320,
                192,
                "[]",
                "keroro",
                "2026-06-22T10:00:00.000000+00:00",
                "n1",
            ),
        )
        conn.commit()
    detail = CatalogReader(open_reader(catalog_db)).file_detail(h)
    assert detail is not None
    assert len(detail.observations) == 1
    obs = detail.observations[0]
    assert obs.media_length_sec == 1320
    assert obs.bitrate_kbps == 192


def _seed_retracted(db: Path) -> None:
    """Add a third file (c*32) that WAS matched (063A) then had that target retracted
    per-target: a ``(hash, 063A, tier="retracted")`` marker appended after the real decision
    (the new per-target retraction model, spec §6). Its latest 063A row is a retraction, so
    it must be treated as unmatched everywhere."""
    h = "c" * 32
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 300))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                h,
                "keroro_063.avi",
                300,
                2,
                1,
                "[]",
                "keroro",
                "2026-06-22T09:00:00.000000+00:00",
                "n1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "063A", "id_segment_exact", "download", "2026-06-22T10:00:00.000000+00:00", "n1"),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "063A", "", "retracted", "2026-06-22T11:00:00.000000+00:00", "n1"),
        )
        conn.commit()


def _seed_unmatched(db: Path) -> None:
    """Add a second file (b*32) with an observation but NO match decision."""
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", ("b" * 32, 200))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "b" * 32,
                "gallego_ep021.ogm",
                200,
                1,
                0,
                "[]",
                "keroro",
                "2026-06-22T09:00:00.000000+00:00",
                "n2",
            ),
        )
        conn.commit()


def test_list_files_matched_only_excludes_unmatched(catalog_db: Path) -> None:
    _seed(catalog_db)
    _seed_unmatched(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(
        target=None, tier=None, verdict=None, query=None, page=1, matched_only=True
    )
    hashes = {r.ed2k_hash for r in rows}
    assert hashes == {"a" * 32}  # only the matched file


def test_list_files_matched_only_excludes_retracted(catalog_db: Path) -> None:
    """A file whose latest decision is a retraction is NOT matched, even though its
    ``target_id`` column is non-NULL (the crawler's sentinel is an empty string, not NULL)."""
    _seed(catalog_db)
    _seed_retracted(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(
        target=None, tier=None, verdict=None, query=None, page=1, matched_only=True
    )
    hashes = {r.ed2k_hash for r in rows}
    assert hashes == {"a" * 32}  # the retracted file ("c"*32) is excluded


def test_list_files_default_includes_unmatched(catalog_db: Path) -> None:
    _seed(catalog_db)
    _seed_unmatched(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(target=None, tier=None, verdict=None, query=None, page=1)
    hashes = {r.ed2k_hash for r in rows}
    assert hashes == {"a" * 32, "b" * 32}  # default matched_only=False → both


def test_list_files_shows_latest_observation(catalog_db: Path) -> None:
    """Same hash with two observations → list_files returns the most recent filename."""
    h = "c" * 32
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 300))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                h,
                "old_name.avi",
                300,
                1,
                0,
                "[]",
                "keroro",
                "2026-06-22T09:00:00.000000+00:00",
                "n1",
            ),
        )
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                h,
                "new_name.avi",
                300,
                2,
                1,
                "[]",
                "keroro",
                "2026-06-22T12:00:00.000000+00:00",
                "n1",
            ),
        )
        conn.commit()
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(target=None, tier=None, verdict=None, query=None, page=1)
    assert len(rows) == 1
    assert rows[0].filename == "new_name.avi"


# ---------------------------------------------------------------------------
# Tests: count_files — /files summary (matched, total)
# ---------------------------------------------------------------------------


def test_count_files_no_filter_returns_matched_and_total(catalog_db: Path) -> None:
    _seed(catalog_db)  # 1 matched file
    _seed_unmatched(catalog_db)  # 1 unmatched file
    reader = CatalogReader(open_reader(catalog_db))
    matched, total = reader.count_files(target=None, tier=None, verdict=None, query=None)
    assert (matched, total) == (1, 2)


def test_count_files_respects_query_filter(catalog_db: Path) -> None:
    _seed(catalog_db)  # filename keroro_062.avi (matched)
    _seed_unmatched(catalog_db)  # filename gallego_ep021.ogm (unmatched)
    reader = CatalogReader(open_reader(catalog_db))
    matched, total = reader.count_files(target=None, tier=None, verdict=None, query="gallego")
    assert (matched, total) == (0, 1)  # only the unmatched file matches the query


def test_count_files_counts_retracted_as_unmatched(catalog_db: Path) -> None:
    """A retracted file counts toward ``total`` but not ``matched`` (the matched count is
    unchanged by its presence, even though its ``target_id`` column is non-NULL)."""
    _seed(catalog_db)  # 1 matched file
    _seed_retracted(catalog_db)  # 1 retracted (== unmatched) file
    reader = CatalogReader(open_reader(catalog_db))
    matched, total = reader.count_files(target=None, tier=None, verdict=None, query=None)
    assert (matched, total) == (1, 2)


def test_count_files_empty_catalogue_matched_is_zero_not_none(catalog_db: Path) -> None:
    """Regression guard for the COUNT → SUM(CASE ...) rewrite: SUM over zero rows is NULL in
    SQL, unlike COUNT which is 0. An empty catalogue must still report ``matched == 0``."""
    reader = CatalogReader(open_reader(catalog_db))
    matched, total = reader.count_files(target=None, tier=None, verdict=None, query=None)
    assert (matched, total) == (0, 0)


# ---------------------------------------------------------------------------
# Tests: latest verdict — "last verification wins" (tie-break on verified_at then id)
# ---------------------------------------------------------------------------


def test_list_files_shows_latest_verdict(catalog_db: Path) -> None:
    """Same hash with two verdicts (T1 < T2) → list_files shows the most recent one."""
    h = "a" * 32
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 100))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (h, "keroro.avi", 100, 1, 0, "[]", "keroro", "2026-06-22T10:00:00.000000+00:00", "n1"),
        )
        for verdict, ts in (
            ("suspicious", "2026-06-22T11:00:00.000000+00:00"),
            ("ok", "2026-06-22T12:00:00.000000+00:00"),
        ):
            conn.execute(
                "INSERT INTO file_verifications"
                " (ed2k_hash, verdict, real_meta, checks, verified_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (h, verdict, None, None, ts, "n1"),
            )
        conn.commit()
    [row] = CatalogReader(open_reader(catalog_db)).list_files(
        target=None, tier=None, verdict=None, query=None, page=1
    )
    assert row.last_verdict == "ok"


def test_list_files_verdict_tie_break_on_id(catalog_db: Path) -> None:
    """Two verdicts with the SAME verified_at → the larger id (later row) wins."""
    h = "b" * 32
    ts = "2026-06-22T10:00:00.000000+00:00"
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 200))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (h, "x.avi", 200, 1, 0, "[]", "keroro", ts, "n1"),
        )
        for verdict in ("suspicious", "ok"):
            conn.execute(
                "INSERT INTO file_verifications"
                " (ed2k_hash, verdict, real_meta, checks, verified_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (h, verdict, None, None, ts, "n1"),
            )
        conn.commit()
    [row] = CatalogReader(open_reader(catalog_db)).list_files(
        target=None, tier=None, verdict=None, query=None, page=1
    )
    assert row.last_verdict == "ok"  # same verified_at → larger id wins
