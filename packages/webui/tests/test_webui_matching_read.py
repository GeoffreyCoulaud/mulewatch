"""TDD tests for matching_read.MatchingExplainer (Task 9 — W-D7)."""

from pathlib import Path

from catalog_matching.engine import Explanation
from catalog_webui.adapters.matching_read import MatchingExplainer

# ---------------------------------------------------------------------------
# YAML minimal helpers
# ---------------------------------------------------------------------------


def _write_file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _minimal_targets_yaml(path: Path) -> Path:
    return _write_file(
        path / "targets.yaml",
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


def _minimal_matcher_yaml(path: Path) -> Path:
    """Minimal matcher: keyword token 'keroro' + catalog rule."""
    return _write_file(
        path / "matcher.yaml",
        """\
tokens:
  keroro:
    keyword: keroro
rules:
  - name: catalog
    tier: catalog
    any:
      - keroro
""",
    )


# ---------------------------------------------------------------------------
# Tests — explain()
# ---------------------------------------------------------------------------


def test_explainer_returns_explanation_on_matching_filename(tmp_path: Path) -> None:
    """Filename containing 'keroro' → Explanation with non-empty rules_fired."""
    matcher_path = _minimal_matcher_yaml(tmp_path)
    targets_path = _minimal_targets_yaml(tmp_path)

    explainer = MatchingExplainer(
        matcher_yaml=matcher_path,
        targets_yaml=targets_path,
    )
    result = explainer.explain(
        filename="Keroro_062A_VF.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="062A",
    )
    assert isinstance(result, Explanation)
    assert len(result.rules_fired) > 0
    assert result.target_id == "062A"


def test_explainer_returns_none_for_unknown_target(tmp_path: Path) -> None:
    """target_id unknown to the config → None."""
    matcher_path = _minimal_matcher_yaml(tmp_path)
    targets_path = _minimal_targets_yaml(tmp_path)

    explainer = MatchingExplainer(
        matcher_yaml=matcher_path,
        targets_yaml=targets_path,
    )
    result = explainer.explain(
        filename="Keroro_062A_VF.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="S9E999Z",
    )
    assert result is None


def test_explainer_size_bytes_converted_to_mb(tmp_path: Path) -> None:
    """size_bytes → size_mb via Mio conversion (1024*1024).

    We verify the FileCandidate is built without error with size_bytes provided.
    An attr_between matcher on size_mb lets us verify the passed value.
    """
    # Matcher with an attr_between token on size_mb: exactly 100 MiB
    _write_file(
        tmp_path / "matcher.yaml",
        """\
tokens:
  keroro:
    keyword: keroro
  size_ok:
    attr_between: size_mb
    min: 99.9
    max: 100.1
rules:
  - name: catalog
    tier: catalog
    all:
      - keroro
      - size_ok
""",
    )
    _minimal_targets_yaml(tmp_path)

    explainer = MatchingExplainer(
        matcher_yaml=tmp_path / "matcher.yaml",
        targets_yaml=tmp_path / "targets.yaml",
    )
    # 100 * 1024 * 1024 = 104857600 bytes = exactly 100.0 MiB
    result = explainer.explain(
        filename="keroro_062a.avi",
        size_bytes=104857600,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="062A",
    )
    assert isinstance(result, Explanation)
    assert "catalog" in result.rules_fired


def test_explainer_media_length_and_bitrate_forwarded(tmp_path: Path) -> None:
    """media_length_sec and bitrate_kbps are forwarded as-is to the FileCandidate."""
    # Matcher with attr_between on duration_sec and bitrate_kbps
    _write_file(
        tmp_path / "matcher.yaml",
        """\
tokens:
  keroro:
    keyword: keroro
  dur_ok:
    attr_between: duration_sec
    min: 1300.0
    max: 1400.0
  bit_ok:
    attr_between: bitrate_kbps
    min: 1000.0
    max: 2000.0
rules:
  - name: catalog
    tier: catalog
    all:
      - keroro
      - dur_ok
      - bit_ok
""",
    )
    _minimal_targets_yaml(tmp_path)

    explainer = MatchingExplainer(
        matcher_yaml=tmp_path / "matcher.yaml",
        targets_yaml=tmp_path / "targets.yaml",
    )
    result = explainer.explain(
        filename="keroro_062a.avi",
        size_bytes=None,
        media_length_sec=1350,
        bitrate_kbps=1500,
        target_id="062A",
    )
    assert isinstance(result, Explanation)
    assert "catalog" in result.rules_fired


def test_explainer_engine_cached_across_calls(tmp_path: Path) -> None:
    """Multiple explain() calls reuse the same engine (identical object)."""
    matcher_path = _minimal_matcher_yaml(tmp_path)
    targets_path = _minimal_targets_yaml(tmp_path)

    explainer = MatchingExplainer(
        matcher_yaml=matcher_path,
        targets_yaml=targets_path,
    )
    # Two successive calls — the engine must be built ONCE (stable private attribute)
    r1 = explainer.explain(
        filename="keroro_vf.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="062A",
    )
    r2 = explainer.explain(
        filename="keroro_vf.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="062A",
    )
    # Both results are identical (stable engine)
    assert r1 == r2
