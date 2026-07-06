"""TDD tests for targets_read.load_targets (Task 9 — W-D7)."""

from pathlib import Path

import pytest

from catalog_matching.models import TargetSegment
from catalog_matching.validation import ConfigError
from mulewatch.webui.adapters.targets_read import load_targets

# ---------------------------------------------------------------------------
# YAML minimal helper
# ---------------------------------------------------------------------------


def _write_targets_yaml(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests — nominal loading
# ---------------------------------------------------------------------------


def test_load_targets_minimal_returns_tuple_with_segment(tmp_path: Path) -> None:
    """Minimal YAML (1 episode, 1 segment) → tuple containing the right target_id."""
    yaml_path = _write_targets_yaml(
        tmp_path / "targets.yaml",
        """\
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - letter: a
        title: "La Grenouille Cosmique"
""",
    )
    result = load_targets(yaml_path)
    assert isinstance(result, tuple)
    assert len(result) == 1
    segment = result[0]
    assert isinstance(segment, TargetSegment)
    assert segment.target_id == "062A"
    assert segment.title == "La Grenouille Cosmique"


def test_load_targets_multiple_segments(tmp_path: Path) -> None:
    """Two segments in one episode → two distinct TargetSegment."""
    yaml_path = _write_targets_yaml(
        tmp_path / "targets.yaml",
        """\
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - letter: a
        title: "Segment A"
      - letter: b
        title: "Segment B"
""",
    )
    result = load_targets(yaml_path)
    assert len(result) == 2
    assert result[0].target_id == "062A"
    assert result[1].target_id == "062B"


# ---------------------------------------------------------------------------
# Tests — errors
# ---------------------------------------------------------------------------


def test_load_targets_file_not_found_raises(tmp_path: Path) -> None:
    """Nonexistent file → OSError (or FileNotFoundError)."""
    missing = tmp_path / "no_such_file.yaml"
    with pytest.raises(OSError):
        load_targets(missing)


def test_load_targets_root_not_mapping_raises(tmp_path: Path) -> None:
    """Non-mapping YAML root → clear error (ConfigError or ValueError)."""
    yaml_path = _write_targets_yaml(
        tmp_path / "targets.yaml",
        "- just_a_list_item\n",
    )
    with pytest.raises((ConfigError, ValueError)):
        load_targets(yaml_path)


def test_load_targets_root_is_none_raises(tmp_path: Path) -> None:
    """Empty YAML file → clear error (ConfigError or ValueError)."""
    yaml_path = _write_targets_yaml(tmp_path / "targets.yaml", "")
    with pytest.raises((ConfigError, ValueError)):
        load_targets(yaml_path)
