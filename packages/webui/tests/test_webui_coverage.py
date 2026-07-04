from catalog_webui.domain.coverage import coverage_for


def test_no_decision_is_none() -> None:
    status = coverage_for("062A", [])
    assert status.status == "none"
    assert status.best_tier is None
    assert status.file_count == 0


def test_download_tier_is_found() -> None:
    status = coverage_for("062A", [("h1", "download"), ("h2", "catalog")])
    assert status.status == "found"
    assert status.best_tier == "download"
    assert status.file_count == 2


def test_only_weak_tiers_is_partial() -> None:
    status = coverage_for("062A", [("h1", "catalog"), ("h2", "notify")])
    assert status.status == "partial"
    assert status.best_tier == "notify"
    assert status.file_count == 2
