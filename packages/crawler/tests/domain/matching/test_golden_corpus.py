from pathlib import Path
from typing import Any

import pytest

from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.models import FileCandidate
from emule_indexer.domain.matching.validation import parse_matcher_config, parse_targets

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _engine() -> MatchingEngine:
    config = parse_matcher_config(load_yaml(_FIXTURES / "canonical_config.yaml"))
    targets = parse_targets(load_yaml(_FIXTURES / "canonical_targets.yaml"))
    return MatchingEngine(config, targets)


def _corpus_cases() -> list[dict[str, Any]]:
    raw = load_yaml(_FIXTURES / "golden_corpus.yaml")
    cases = raw["cases"]
    assert isinstance(cases, list)
    return [dict(case) for case in cases]


_CASES = _corpus_cases()


@pytest.mark.parametrize("case", _CASES, ids=[str(c["id"]) for c in _CASES])
def test_golden_corpus(case: dict[str, Any]) -> None:
    engine = _engine()
    decision = engine.evaluate(FileCandidate(filename=str(case["filename"])))
    if case.get("discarded", False):
        assert decision is None, f"{case['id']}: attendu écarté, obtenu {decision}"
        return
    assert decision is not None, f"{case['id']}: attendu une décision, obtenu None"
    assert decision.tier == case["tier"], f"{case['id']}: palier"
    assert decision.target_id == case["target_id"], f"{case['id']}: cible"
    assert decision.rule_name == case["rule_name"], f"{case['id']}: règle"


def test_corpus_covers_every_tier_and_a_discard() -> None:
    # Garde-fou de complétude : le corpus exerce les 3 paliers + au moins un écart.
    tiers = {c.get("tier") for c in _CASES if not c.get("discarded", False)}
    assert {"download", "notify", "catalog"} <= tiers
    assert any(c.get("discarded", False) for c in _CASES)
