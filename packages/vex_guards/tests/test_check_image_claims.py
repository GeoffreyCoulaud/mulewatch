import json
from pathlib import Path
from typing import cast

import pytest

from vex_guards import check_image_claims
from vex_guards.repo import repo_root

# The real published VEX docs double as fixtures: the verifier VEX carries both
# image-family claims (CVE-2026-58055 -> PackageAbsent("nghttp2"),
# CVE-2016-1405 -> PackageMinVersion("clamav", "0.99")) and source-family ones,
# so it exercises both is_image_guard branches; the crawler VEX has no image
# claim at all, so it drives the scoping (no image guards apply) path. Both live
# under the repo, so they take the in-repo relative repo.display_path branch.
_VERIFIER_VEX = repo_root() / "security" / "verifier.vex.openvex.json"
_CRAWLER_VEX = repo_root() / "security" / "crawler.vex.openvex.json"
_VERIFIER_VEX_RELPATH = "security/verifier.vex.openvex.json"


def _write_sbom(tmp_path: Path, artifacts: list[dict[str, str]]) -> Path:
    path = tmp_path / "sbom.syft.json"
    path.write_text(json.dumps({"artifacts": artifacts}))
    return path


def _violating_sbom(tmp_path: Path) -> Path:
    # nghttp2 present contradicts CVE-2026-58055's PackageAbsent("nghttp2") guard.
    return _write_sbom(tmp_path, [{"type": "apk", "name": "nghttp2", "version": "1.64.0-r0"}])


def _clean_sbom(tmp_path: Path) -> Path:
    return _write_sbom(
        tmp_path,
        [
            {"type": "apk", "name": "nghttp2-libs", "version": "1.64.0-r0"},
            {"type": "apk", "name": "clamav", "version": "1.4.4-r0"},
        ],
    )


def _write_vex(tmp_path: Path, cve: str, justification: str) -> Path:
    doc = {
        "statements": [
            {
                "vulnerability": {"name": cve},
                "status": "not_affected",
                "justification": justification,
            }
        ]
    }
    path = tmp_path / "vex.openvex.json"
    path.write_text(json.dumps(doc))
    return path


def test_fail_mode_flags_a_present_package_and_prints_the_cve(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sbom = _violating_sbom(tmp_path)

    rc = check_image_claims.main(["--sbom", str(sbom), "--vex", str(_VERIFIER_VEX)])

    assert rc == 1
    out = capsys.readouterr().out
    assert "::error::CVE-2026-58055" in out
    # The in-repo relative repo.display_path branch: the location renders repo-relative.
    assert f"({_VERIFIER_VEX_RELPATH})" in out


def test_fail_mode_returns_zero_on_a_clean_sbom(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sbom = _clean_sbom(tmp_path)

    rc = check_image_claims.main(["--sbom", str(sbom), "--vex", str(_VERIFIER_VEX)])

    assert rc == 0
    assert capsys.readouterr().out == ""


def test_crawler_vex_has_no_image_guards_so_any_sbom_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Scoping: the crawler VEX carries only source-family claims, so is_image_guard
    # filters them all out and even a package-laden SBOM yields no violations.
    sbom = _violating_sbom(tmp_path)

    rc = check_image_claims.main(["--sbom", str(sbom), "--vex", str(_CRAWLER_VEX)])

    assert rc == 0
    assert capsys.readouterr().out == ""


def test_fail_mode_renders_an_out_of_repo_vex_path_verbatim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The repo.display_path ValueError fallback: a VEX outside the repo (CI /tmp) must
    # not crash; its raw path is echoed as-is in the violation location.
    vex = _write_vex(tmp_path, "CVE-2026-58055", "vulnerable_code_not_present")
    sbom = _violating_sbom(tmp_path)

    rc = check_image_claims.main(["--sbom", str(sbom), "--vex", str(vex)])

    assert rc == 1
    out = capsys.readouterr().out
    assert "::error::CVE-2026-58055" in out
    assert f"({vex})" in out


def test_sarif_mode_with_violation_writes_matching_results(tmp_path: Path) -> None:
    sbom = _violating_sbom(tmp_path)
    output = tmp_path / "out.sarif"

    rc = check_image_claims.main(
        [
            "--sbom",
            str(sbom),
            "--vex",
            str(_VERIFIER_VEX),
            "--format",
            "sarif",
            "--output",
            str(output),
        ]
    )

    assert rc == 0
    doc = json.loads(output.read_text())
    runs = cast(list[dict[str, object]], doc["runs"])
    results = cast(list[dict[str, object]], runs[0]["results"])
    assert len(results) == 1
    result = results[0]

    assert result["ruleId"] == "unsatisfied-image-claim"
    assert result["level"] == "error"

    message = cast(dict[str, object], result["message"])
    assert "CVE-2026-58055" in cast(str, message["text"])

    locations = cast(list[dict[str, object]], result["locations"])
    physical = cast(dict[str, object], locations[0]["physicalLocation"])
    artifact = cast(dict[str, object], physical["artifactLocation"])
    assert artifact["uri"] == _VERIFIER_VEX_RELPATH


def test_sarif_mode_on_a_clean_sbom_writes_empty_results(tmp_path: Path) -> None:
    sbom = _clean_sbom(tmp_path)
    output = tmp_path / "out.sarif"

    rc = check_image_claims.main(
        [
            "--sbom",
            str(sbom),
            "--vex",
            str(_VERIFIER_VEX),
            "--format",
            "sarif",
            "--output",
            str(output),
        ]
    )

    assert rc == 0
    doc = json.loads(output.read_text())
    runs = cast(list[dict[str, object]], doc["runs"])
    assert runs[0]["results"] == []
