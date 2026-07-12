import logging
from importlib.metadata import version
from pathlib import Path

import pytest
import uvicorn

import download_verifier.__main__ as entry


def test_main_invokes_uvicorn_with_app_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def _fake_run(target: object, **kwargs: object) -> None:
        calls.append((target, kwargs))

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setenv("VERIFIER_HOST", "0.0.0.0")
    monkeypatch.setenv("VERIFIER_PORT", "9100")
    entry.main()
    assert calls == [("download_verifier.app:app", {"host": "0.0.0.0", "port": 9100})]


def test_main_logs_the_package_version_at_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # main() logs the download-verifier version (from installed metadata) at startup, so a
    # running verifier can be correlated to a release (spec 2026-07-10-git-driven-versioning).
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger="download_verifier.__main__"):
        entry.main()
    assert any(
        r.getMessage() == f"download-verifier version {version('download-verifier')}"
        for r in caplog.records
    )


def test_configure_logging_default_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERIFIER_CONFIG", raising=False)
    entry.configure_logging({})
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "verifier.yaml"
    path.write_text("observability:\n  log_level: WARNING\n", encoding="utf-8")
    entry.configure_logging({"VERIFIER_CONFIG": str(path)})
    assert logging.getLogger().level == logging.WARNING
