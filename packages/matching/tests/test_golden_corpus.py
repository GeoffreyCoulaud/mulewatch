from pathlib import Path
from typing import Any

import pytest
import yaml

from catalog_matching.engine import MatchingEngine
from catalog_matching.models import FileCandidate
from catalog_matching.validation import parse_matcher_config, parse_targets

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
# La policy matcher a une source de vérité unique : la config de déploiement (éditable par
# l'opérateur). Le golden corpus valide donc la policy RÉELLEMENT livrée, pas une copie.
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
