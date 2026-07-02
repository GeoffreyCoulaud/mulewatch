from emule_indexer.domain.download.policy import DownloadVerdict, download_policy


def _verdict(
    *,
    tier: str = "download",
    target_status: str = "lost",
    already_downloaded: bool = False,
    committed_bytes: int = 0,
    file_size: int = 100,
    disk_cap: int = 1000,
) -> DownloadVerdict:
    return download_policy(
        tier=tier,
        target_status=target_status,
        already_downloaded=already_downloaded,
        committed_bytes=committed_bytes,
        file_size=file_size,
        disk_cap=disk_cap,
    )


def test_verdict_is_a_closed_enum() -> None:
    assert set(DownloadVerdict) == {
        DownloadVerdict.DOWNLOAD,
        DownloadVerdict.SKIP_COMPLETE,
        DownloadVerdict.SKIP_DEDUP,
        DownloadVerdict.SKIP_DISK_CAP,
    }


def test_nominal_lost_target_downloads() -> None:
    assert _verdict() is DownloadVerdict.DOWNLOAD


def test_non_download_tier_is_a_conservative_guard() -> None:
    # DECISION D5: conservative guard ("do not download") — never triggered in prod
    # (the application only passes tier=download decisions), but an out-of-contract caller
    # does not crash and downloads nothing.
    assert _verdict(tier="catalog") is DownloadVerdict.SKIP_COMPLETE
    assert _verdict(tier="notify") is DownloadVerdict.SKIP_COMPLETE


def test_complete_target_skips() -> None:
    assert _verdict(target_status="complete") is DownloadVerdict.SKIP_COMPLETE


def test_partial_and_poor_targets_still_download() -> None:
    assert _verdict(target_status="partial") is DownloadVerdict.DOWNLOAD
    assert _verdict(target_status="poor") is DownloadVerdict.DOWNLOAD


def test_already_downloaded_is_deduped() -> None:
    assert _verdict(already_downloaded=True) is DownloadVerdict.SKIP_DEDUP


def test_dedup_takes_precedence_over_disk_cap() -> None:
    # already downloaded AND above the cap → we return SKIP_DEDUP (nothing to re-download).
    assert (
        _verdict(already_downloaded=True, committed_bytes=950, file_size=100, disk_cap=1000)
        is DownloadVerdict.SKIP_DEDUP
    )


def test_over_disk_cap_defers() -> None:
    assert (
        _verdict(committed_bytes=950, file_size=100, disk_cap=1000) is DownloadVerdict.SKIP_DISK_CAP
    )


def test_exactly_at_disk_cap_is_allowed() -> None:
    # committed + size == cap: allowed (the cap is a MAX, not a strictly-below threshold).
    assert _verdict(committed_bytes=900, file_size=100, disk_cap=1000) is DownloadVerdict.DOWNLOAD


def test_one_byte_over_disk_cap_defers() -> None:
    assert (
        _verdict(committed_bytes=901, file_size=100, disk_cap=1000) is DownloadVerdict.SKIP_DISK_CAP
    )


def test_complete_takes_precedence_over_dedup() -> None:
    # complete target: we skip for COMPLETE even if already downloaded (status wins the order).
    assert (
        _verdict(target_status="complete", already_downloaded=True) is DownloadVerdict.SKIP_COMPLETE
    )


def test_found_target_still_downloads_a_new_file() -> None:
    # Product invariant (spec search-simplification, Batch C): an already-"found" episode is
    # re-downloaded when a NEW file matches it (intended archival redundancy).
    # Only target_status == "complete" skips; "found" never does in PROD.
    verdict = download_policy(
        tier="download",
        target_status="found",
        already_downloaded=False,
        committed_bytes=0,
        file_size=100_000_000,
        disk_cap=10_000_000_000,
    )
    assert verdict is DownloadVerdict.DOWNLOAD
