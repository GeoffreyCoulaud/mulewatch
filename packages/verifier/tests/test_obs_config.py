"""Verifier observability mini YAML loader: log_level validated, default INFO, fail-fast."""

from pathlib import Path

import pytest

from download_verifier.obs_config import ObsConfigError, load_observability


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "verifier.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_log_level(tmp_path: Path) -> None:
    path = _write(tmp_path, "observability:\n  log_level: DEBUG\n")
    assert load_observability(path).log_level == "DEBUG"


def test_defaults_to_info_when_absent(tmp_path: Path) -> None:
    path = _write(tmp_path, "other: 1\n")
    assert load_observability(path).log_level == "INFO"


def test_rejects_unknown_level(tmp_path: Path) -> None:
    path = _write(tmp_path, "observability:\n  log_level: LOUD\n")
    with pytest.raises(ObsConfigError, match="log_level"):
        load_observability(path)


def test_defaults_to_info_when_yaml_is_not_dict(tmp_path: Path) -> None:
    """If the root YAML is not a dict (e.g. null scalar), default INFO."""
    path = _write(tmp_path, "null\n")
    assert load_observability(path).log_level == "INFO"


def test_defaults_to_info_when_section_is_not_dict(tmp_path: Path) -> None:
    """If observability is present but non-dict (e.g. null), default INFO."""
    path = _write(tmp_path, "observability: null\n")
    assert load_observability(path).log_level == "INFO"
