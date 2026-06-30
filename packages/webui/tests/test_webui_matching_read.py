"""Tests TDD pour matching_read.MatchingExplainer (Task 9 — W-D7)."""

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
    """Matcher minimal : token keyword 'keroro' + règle catalog."""
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
    """Filename contenant 'keroro' → Explanation avec rules_fired non vide."""
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
        target_id="S2E062A",
    )
    assert isinstance(result, Explanation)
    assert len(result.rules_fired) > 0
    assert result.target_id == "S2E062A"


def test_explainer_returns_none_for_unknown_target(tmp_path: Path) -> None:
    """target_id inconnu de la config → None."""
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
    """size_bytes → size_mb via conversion Mio (1024*1024).

    On vérifie que le FileCandidate est construit sans erreur avec size_bytes fourni.
    Un matcher attr_between sur size_mb permet de vérifier la valeur passée.
    """
    # Matcher avec un token attr_between sur size_mb : 100 MiB exactement
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
    # 100 * 1024 * 1024 = 104857600 octets = exactement 100.0 MiB
    result = explainer.explain(
        filename="keroro_062a.avi",
        size_bytes=104857600,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="S2E062A",
    )
    assert isinstance(result, Explanation)
    assert "catalog" in result.rules_fired


def test_explainer_media_length_and_bitrate_forwarded(tmp_path: Path) -> None:
    """media_length_sec et bitrate_kbps sont transmis tels quels au FileCandidate."""
    # Matcher avec attr_between sur duration_sec et bitrate_kbps
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
        target_id="S2E062A",
    )
    assert isinstance(result, Explanation)
    assert "catalog" in result.rules_fired


def test_explainer_engine_cached_across_calls(tmp_path: Path) -> None:
    """Plusieurs appels à explain() réutilisent le même engine (objet identique)."""
    matcher_path = _minimal_matcher_yaml(tmp_path)
    targets_path = _minimal_targets_yaml(tmp_path)

    explainer = MatchingExplainer(
        matcher_yaml=matcher_path,
        targets_yaml=targets_path,
    )
    # Deux appels successifs — le engine doit être construit UNE fois (attribut privé stable)
    r1 = explainer.explain(
        filename="keroro_vf.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="S2E062A",
    )
    r2 = explainer.explain(
        filename="keroro_vf.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="S2E062A",
    )
    # Les deux résultats sont identiques (engine stable)
    assert r1 == r2
