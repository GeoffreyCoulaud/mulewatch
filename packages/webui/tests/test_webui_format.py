from catalog_webui.domain.format import human_size, seasonal_id, short_hash, short_timestamp


def test_short_hash_truncates_with_ellipsis() -> None:
    assert short_hash("a" * 32) == "aaaaaaaa…"


def test_short_hash_short_input_is_unchanged() -> None:
    assert short_hash("abc") == "abc"


# ---------------------------------------------------------------------------
# human_size — binary (1024-based) units, matching this codebase's existing
# ``size_mb`` convention (see adapters/matching_read.py: "binary Mio").
# ---------------------------------------------------------------------------


def test_human_size_zero_bytes() -> None:
    assert human_size(0) == "0 B"


def test_human_size_just_under_kib_stays_in_bytes() -> None:
    assert human_size(1023) == "1023 B"


def test_human_size_exact_kib_boundary() -> None:
    assert human_size(1024) == "1 KB"


def test_human_size_kb_range_rounds_to_nearest() -> None:
    assert human_size(1_048_000) == "1023 KB"


def test_human_size_exact_mib_boundary() -> None:
    assert human_size(1024 * 1024) == "1 MB"


def test_human_size_matches_task_brief_example() -> None:
    """349 MiB — the exact example from the task brief."""
    assert human_size(349 * 1024 * 1024) == "349 MB"


def test_human_size_exact_gib_boundary() -> None:
    assert human_size(1024**3) == "1 GB"


def test_human_size_multi_gb() -> None:
    assert human_size(5 * 1024**3) == "5 GB"


def test_human_size_exact_tib_boundary() -> None:
    assert human_size(1024**4) == "1 TB"


def test_human_size_multi_tb() -> None:
    assert human_size(2 * 1024**4) == "2 TB"


# ---------------------------------------------------------------------------
# short_timestamp
# ---------------------------------------------------------------------------


def test_short_timestamp_with_microseconds_and_offset() -> None:
    assert short_timestamp("2026-07-03T23:45:24.104990+00:00") == "2026-07-03 23:45Z"


def test_short_timestamp_without_microseconds() -> None:
    assert short_timestamp("2026-07-03T23:45:24+00:00") == "2026-07-03 23:45Z"


def test_short_timestamp_without_timezone_offset() -> None:
    assert short_timestamp("2024-01-01T00:00:00") == "2024-01-01 00:00Z"


# ---------------------------------------------------------------------------
# seasonal_id
# ---------------------------------------------------------------------------


def test_seasonal_id_zero_pads_season_and_number() -> None:
    assert seasonal_id(season=2, seasonal_number=11, letter="a") == "S02E11A"


def test_seasonal_id_uppercases_letter() -> None:
    assert seasonal_id(season=1, seasonal_number=5, letter="b") == "S01E05B"
