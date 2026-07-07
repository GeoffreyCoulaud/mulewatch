from pathlib import Path

import pytest

from vex_guards import check_source_claims


def test_main_returns_zero_on_the_real_repo() -> None:
    assert check_source_claims.main() == 0


def test_main_flags_a_seeded_import_and_prints_the_cve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "leak.py").write_text("import tarfile\n")
    # Only the source tree is redirected; the real (alpine) Dockerfiles keep the
    # BaseImageIsAlpine guard satisfied, so the seeded import is the sole failure.
    monkeypatch.setattr(check_source_claims, "source_dirs", lambda: [src])

    assert check_source_claims.main() == 1
    out = capsys.readouterr().out
    assert "::error::CVE-2026-11940" in out
