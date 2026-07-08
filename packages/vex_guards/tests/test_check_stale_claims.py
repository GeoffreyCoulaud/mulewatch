import json
from pathlib import Path
from typing import cast

import pytest

from vex_guards import check_stale_claims


class _FakeGrypeRunner:
    """A GrypeRunner whose report is preset, so no real Grype is invoked."""

    def __init__(self, reported: set[str]) -> None:
        self._reported = reported

    def run(self, sbom_path: Path) -> set[str]:
        return self._reported


def _write_vex(tmp_path: Path, cves: list[str]) -> Path:
    doc = {
        "statements": [
            {
                "vulnerability": {"name": cve},
                "status": "not_affected",
                "justification": "vulnerable_code_not_present",
            }
            for cve in cves
        ]
    }
    path = tmp_path / "vex.openvex.json"
    path.write_text(json.dumps(doc))
    return path


def test_fail_mode_flags_a_stale_claim_and_prints_the_cve(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Grype still reports CVE-A but no longer CVE-B, so the CVE-B claim is stale.
    vex = _write_vex(tmp_path, ["CVE-A", "CVE-B"])
    runner = _FakeGrypeRunner({"CVE-A"})

    rc = check_stale_claims.main(
        ["--sbom", str(tmp_path / "sbom.json"), "--vex", str(vex)],
        runner=runner,
    )

    assert rc == 1
    out = capsys.readouterr().out
    assert "::error::CVE-B: no longer reported by Grype" in out
    assert "CVE-A" not in out


def test_fail_mode_returns_zero_when_every_claim_is_still_reported(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vex = _write_vex(tmp_path, ["CVE-A", "CVE-B"])
    runner = _FakeGrypeRunner({"CVE-A", "CVE-B"})

    rc = check_stale_claims.main(
        ["--sbom", str(tmp_path / "sbom.json"), "--vex", str(vex)],
        runner=runner,
    )

    assert rc == 0
    assert capsys.readouterr().out == ""


def test_sarif_mode_with_a_stale_claim_writes_matching_results(tmp_path: Path) -> None:
    vex = _write_vex(tmp_path, ["CVE-A", "CVE-B"])
    runner = _FakeGrypeRunner({"CVE-A"})
    output = tmp_path / "out.sarif"

    rc = check_stale_claims.main(
        [
            "--sbom",
            str(tmp_path / "sbom.json"),
            "--vex",
            str(vex),
            "--format",
            "sarif",
            "--output",
            str(output),
        ],
        runner=runner,
    )

    assert rc == 0
    doc = json.loads(output.read_text())
    runs = cast(list[dict[str, object]], doc["runs"])
    results = cast(list[dict[str, object]], runs[0]["results"])
    assert len(results) == 1
    result = results[0]

    assert result["ruleId"] == "stale-vex-entry"
    message = cast(dict[str, object], result["message"])
    assert "CVE-B" in cast(str, message["text"])


def test_sarif_mode_with_no_stale_claim_writes_empty_results(tmp_path: Path) -> None:
    vex = _write_vex(tmp_path, ["CVE-A", "CVE-B"])
    runner = _FakeGrypeRunner({"CVE-A", "CVE-B"})
    output = tmp_path / "out.sarif"

    rc = check_stale_claims.main(
        [
            "--sbom",
            str(tmp_path / "sbom.json"),
            "--vex",
            str(vex),
            "--format",
            "sarif",
            "--output",
            str(output),
        ],
        runner=runner,
    )

    assert rc == 0
    doc = json.loads(output.read_text())
    runs = cast(list[dict[str, object]], doc["runs"])
    assert runs[0]["results"] == []


def test_runner_defaults_to_subprocess_grype_runner_when_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The ``runner is None`` branch: main must construct SubprocessGrypeRunner()
    # itself. We swap that class for a stub reporting nothing (so the sole claim
    # is stale) and confirm main used it, without ever invoking real Grype.
    class _StubSubprocessGrypeRunner:
        def run(self, sbom_path: Path) -> set[str]:
            return set()

    monkeypatch.setattr(
        check_stale_claims,
        "SubprocessGrypeRunner",
        _StubSubprocessGrypeRunner,
    )
    vex = _write_vex(tmp_path, ["CVE-A"])

    rc = check_stale_claims.main(
        ["--sbom", str(tmp_path / "sbom.json"), "--vex", str(vex)],
        runner=None,
    )

    assert rc == 1
    assert "::error::CVE-A: no longer reported by Grype" in capsys.readouterr().out
