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
    # DÉCISION D5 : garde conservatrice (« ne pas télécharger ») — jamais déclenchée en prod
    # (l'application ne passe que des décisions tier=download), mais un appelant hors contrat
    # ne crashe pas et ne télécharge rien.
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
    # déjà téléchargé ET au-dessus du plafond → on rend SKIP_DEDUP (rien à re-télécharger).
    assert (
        _verdict(already_downloaded=True, committed_bytes=950, file_size=100, disk_cap=1000)
        is DownloadVerdict.SKIP_DEDUP
    )


def test_over_disk_cap_defers() -> None:
    assert (
        _verdict(committed_bytes=950, file_size=100, disk_cap=1000) is DownloadVerdict.SKIP_DISK_CAP
    )


def test_exactly_at_disk_cap_is_allowed() -> None:
    # committed + size == cap : autorisé (le plafond est un MAX, pas un seuil strict en-dessous).
    assert _verdict(committed_bytes=900, file_size=100, disk_cap=1000) is DownloadVerdict.DOWNLOAD


def test_one_byte_over_disk_cap_defers() -> None:
    assert (
        _verdict(committed_bytes=901, file_size=100, disk_cap=1000) is DownloadVerdict.SKIP_DISK_CAP
    )


def test_complete_takes_precedence_over_dedup() -> None:
    # cible complète : on saute pour COMPLETE même si déjà téléchargé (le statut prime l'ordre).
    assert (
        _verdict(target_status="complete", already_downloaded=True) is DownloadVerdict.SKIP_COMPLETE
    )
