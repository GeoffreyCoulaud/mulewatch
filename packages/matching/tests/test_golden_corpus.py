from pathlib import Path
from typing import Any

import pytest
import yaml

from catalog_matching.config import RegexDef
from catalog_matching.engine import MatchingEngine
from catalog_matching.matchers import RegexMatcher
from catalog_matching.models import FileCandidate
from catalog_matching.validation import parse_matcher_config, parse_targets

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
# The matcher policy has a single source of truth: the deployment config (operator-
# editable). So the golden corpus validates the policy ACTUALLY shipped, not a copy.
_MATCHER = Path(__file__).resolve().parents[3] / "deploy" / "config" / "crawler" / "matcher.yml"


def _engine() -> MatchingEngine:
    config = parse_matcher_config(yaml.safe_load(_MATCHER.read_text(encoding="utf-8")))
    targets = parse_targets(
        yaml.safe_load((_FIXTURES / "golden_targets.yaml").read_text(encoding="utf-8"))
    )
    return MatchingEngine(config, targets)


def _corpus_cases() -> list[dict[str, Any]]:
    raw: dict[str, Any] = yaml.safe_load(
        (_FIXTURES / "golden_corpus.yaml").read_text(encoding="utf-8")
    )
    cases = raw["cases"]
    assert isinstance(cases, list)
    return [dict(case) for case in cases]


_CASES = _corpus_cases()


@pytest.mark.parametrize("case", _CASES, ids=[str(c["id"]) for c in _CASES])
def test_golden_corpus(case: dict[str, Any]) -> None:
    engine = _engine()
    decision = engine.evaluate(FileCandidate(filename=str(case["filename"])))
    if case.get("discarded", False):
        assert decision is None, f"{case['id']}: expected discarded, got {decision}"
        return
    assert decision is not None, f"{case['id']}: expected a decision, got None"
    assert decision.tier == case["tier"], f"{case['id']}: tier"
    assert decision.target_id == case["target_id"], f"{case['id']}: target"
    assert decision.rule_name == case["rule_name"], f"{case['id']}: rule"


def test_corpus_covers_every_tier_and_a_discard() -> None:
    # Completeness guard: the corpus exercises the 3 tiers + at least one discard.
    tiers = {c.get("tier") for c in _CASES if not c.get("discarded", False)}
    assert {"download", "notify", "catalog"} <= tiers
    assert any(c.get("discarded", False) for c in _CASES)


# --- is_archive token contract (shipped policy) ------------------------------------
# The catalogue's media gate keeps is_video OR is_archive. is_archive is meant for the
# GENERIC archives that may ship an episode inside them; comic-book containers (.cbz/.cbr,
# technically ZIP/RAR) are deliberately NOT archives so mangas never enter the catalogue.


def _is_archive_matcher() -> RegexMatcher:
    config = parse_matcher_config(yaml.safe_load(_MATCHER.read_text(encoding="utf-8")))
    token = config.tokens["is_archive"]
    assert isinstance(token, RegexDef)  # the shipped is_archive is a plain regex token
    return RegexMatcher(token.pattern, token.flags)


@pytest.mark.parametrize("filename", ["x.zip", "x.7z", "x.rar", "x.r01", "x.z01", "x.part1.rar"])
def test_is_archive_matches_generic_archive_extensions(filename: str) -> None:
    assert _is_archive_matcher().matches(FileCandidate(filename=filename)) is True


@pytest.mark.parametrize("filename", ["x.cbz", "x.cbr"])
def test_is_archive_rejects_comic_book_formats(filename: str) -> None:
    # cbz/cbr are comic formats, not the generic archives is_archive is meant to catch;
    # adding them to is_archive would silently re-catalogue mangas.
    assert _is_archive_matcher().matches(FileCandidate(filename=filename)) is False
