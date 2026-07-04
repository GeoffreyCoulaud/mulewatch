import dataclasses
from pathlib import Path

import pytest
import yaml

from catalog_matching.config import TIERS, MatcherConfig
from catalog_matching.engine import (
    _TIER_RANK,
    Explanation,
    MatchDecision,
    MatchingEngine,
    _first_matching_rule,
)
from catalog_matching.models import FileCandidate, TargetSegment
from catalog_matching.resolver import MatcherResolver, ResolvedTarget
from catalog_matching.validation import parse_matcher_config


def test_tier_rank_orders_download_above_notify_above_catalog() -> None:
    assert _TIER_RANK["download"] > _TIER_RANK["notify"]
    assert _TIER_RANK["notify"] > _TIER_RANK["catalog"]


def test_tier_rank_catalog_is_lowest() -> None:
    assert _TIER_RANK["catalog"] < _TIER_RANK["download"]
    assert _TIER_RANK["catalog"] < _TIER_RANK["notify"]


def test_tier_rank_covers_exactly_the_valid_tiers() -> None:
    # Consistency: every licit tier (TIERS) has a rank, and no orphan rank.
    assert set(_TIER_RANK) == TIERS


def test_explanation_is_frozen_and_holds_fields() -> None:
    explanation = Explanation(
        target_id="062A",
        rules_fired=("id_segment_exact", "keroro_large"),
        tokens_matched=("is_video", "keroro", "segment_id"),
        coverage_values=(("title_hit", 1.0),),
    )
    assert explanation.target_id == "062A"
    assert explanation.rules_fired == ("id_segment_exact", "keroro_large")
    assert explanation.tokens_matched == ("is_video", "keroro", "segment_id")
    assert explanation.coverage_values == (("title_hit", 1.0),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        explanation.target_id = "062B"  # type: ignore[misc]


def test_match_decision_is_frozen_and_holds_persisted_columns_plus_explanation() -> None:
    explanation = Explanation(
        target_id="062A",
        rules_fired=("id_segment_exact",),
        tokens_matched=("keroro",),
        coverage_values=(),
    )
    decision = MatchDecision(
        target_id="062A",
        rule_name="id_segment_exact",
        tier="download",
        explanation=explanation,
    )
    # The three columns that match_decisions will persist (spec §11).
    assert decision.target_id == "062A"
    assert decision.rule_name == "id_segment_exact"
    assert decision.tier == "download"
    assert decision.explanation is explanation
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.tier = "notify"  # type: ignore[misc]


_TARGET_62A = TargetSegment(
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="a",
    title="Les demoiselles cambrioleuses",
    status="partial",
)

# Minimal config with two rules of distinct index to exercise "1st true" and "loop".
_TWO_RULE_RAW: dict[str, object] = {
    "tokens": {
        "is_video": {"regex": r"\.(avi|mkv)$"},
        "seg": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        "keroro": {"keyword": "keroro"},
    },
    "rules": [
        {"name": "exact", "tier": "download", "all": ["is_video", "seg"]},
        {"name": "large", "tier": "catalog", "any": ["keroro"]},
    ],
}


def _resolve(raw: dict[str, object], target: TargetSegment) -> tuple[MatcherConfig, ResolvedTarget]:
    config = parse_matcher_config(raw)
    resolved = MatcherResolver(config).resolve_all(target)
    return config, resolved


def test_first_matching_rule_returns_index_zero_when_first_rule_true() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    candidate = FileCandidate(filename="Keroro N°062A.avi")
    assert _first_matching_rule(config, resolved, candidate) == (0, "exact", "download")


def test_first_matching_rule_skips_to_later_rule_when_first_false() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    # Not video + no 062A segment -> "exact" false; "keroro" true -> 2nd rule.
    candidate = FileCandidate(filename="keroro autre chose.txt")
    assert _first_matching_rule(config, resolved, candidate) == (1, "large", "catalog")


def test_first_matching_rule_returns_none_when_no_rule_true() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    candidate = FileCandidate(filename="naruto 062.txt")
    assert _first_matching_rule(config, resolved, candidate) is None


# --- Canonical config §8.3 (reused by several tests) ---
# Single source of truth: the deployment matcher config (operator-editable), not an inline
# copy. So these tests validate the policy actually shipped. Cf. test_golden_corpus.
_CANONICAL_MATCHER = (
    Path(__file__).resolve().parents[3] / "deploy" / "config" / "crawler" / "matcher.yml"
)
_CANONICAL_RAW: dict[str, object] = yaml.safe_load(_CANONICAL_MATCHER.read_text(encoding="utf-8"))

_TARGET_62B = TargetSegment(
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="b",
    title="Le grand combat sous-marin",
    status="lost",
)

_REAL_62A_FILENAME = (
    "[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » "
    "[Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi"
)


def _canonical_engine() -> MatchingEngine:
    config = parse_matcher_config(_CANONICAL_RAW)
    return MatchingEngine(config, (_TARGET_62A, _TARGET_62B))


def test_evaluate_real_62a_is_download_via_first_rule_on_62a() -> None:
    decision = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.rule_name == "id_segment_exact"
    assert decision.target_id == "062A"


def test_evaluate_discards_non_keroro_file() -> None:
    decision = _canonical_engine().evaluate(FileCandidate(filename="Naruto épisode 062 VF.avi"))
    assert decision is None


def test_evaluate_highest_tier_comes_from_a_different_target() -> None:
    # "keroro N°062B.avi": 62A -> catalog (keroro_large); 62B -> download (id_segment_exact).
    # The highest tier comes from a DIFFERENT target than the lowest -> isolates the
    # cross-target aggregation (a bug looking only at the 1st target would return catalog/062A).
    decision = _canonical_engine().evaluate(FileCandidate(filename="keroro N°062B.avi"))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.rule_name == "id_segment_exact"
    assert decision.target_id == "062B"


def test_evaluate_notify_tier_via_title_review() -> None:
    # Close title, NO source marker -> title_review (notify), not download.
    candidate = FileCandidate(filename="KERORO Les demoiselles cambrioleuses.avi")
    decision = _canonical_engine().evaluate(candidate)
    assert decision is not None
    assert decision.tier == "notify"
    assert decision.rule_name == "title_review"
    assert decision.target_id == "062A"


def test_evaluate_tiebreak_same_tier_lowest_target_id_wins() -> None:
    # "Keroro" file alone -> 62A and 62B BOTH give keroro_large (catalog, index 3).
    # Same tier AND same index -> tie-break by target_id: 062A < 062B.
    # NB: neutral filler (not "Gunso", now vetoed by foreign_lang as a Japanese title).
    decision = _canonical_engine().evaluate(FileCandidate(filename="Keroro rediffusion.mkv"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"
    assert decision.target_id == "062A"


# --- Tie-break by rule INDEX (isolated from target_id) ---
# Two download rules; the target with the LARGER target_id matches the rule with the
# SMALLER index. If only target_id broke ties, the wrong target would win; index must prevail.
_INDEX_TIEBREAK_RAW: dict[str, object] = {
    "tokens": {
        "is_video": {"regex": r"\.(avi|mkv)$"},
        "seg": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        "title_hit": {"coverage": "title", "min": 0.6},
    },
    "rules": [
        {"name": "by_segment", "tier": "download", "all": ["is_video", "seg"]},
        {
            "name": "by_title",
            "tier": "download",
            "all": ["is_video", {"token": "title_hit", "min": 0.6}],
        },
    ],
}


def test_evaluate_tiebreak_same_tier_lowest_rule_index_wins_over_target_id() -> None:
    config = parse_matcher_config(_INDEX_TIEBREAK_RAW)
    # target_high: large target_id (099Z), matches by_segment (index 0).
    target_high = TargetSegment(
        season=2, seasonal_number=99, absolute_number=99, segment="z", title="zzz unrelated"
    )
    # target_low: small target_id (001A), matches by_title (index 1).
    target_low = TargetSegment(
        season=2,
        seasonal_number=1,
        absolute_number=1,
        segment="a",
        title="Les demoiselles cambrioleuses",
    )
    engine = MatchingEngine(config, (target_low, target_high))
    candidate = FileCandidate(filename="N°099Z Les demoiselles cambrioleuses.avi")
    decision = engine.evaluate(candidate)
    assert decision is not None
    # Index 0 (by_segment on 099Z) prevails over index 1 (by_title on 001A),
    # DESPITE 001A < 099Z: the index breaks ties BEFORE the target_id.
    assert decision.rule_name == "by_segment"
    assert decision.target_id == "099Z"


def test_evaluate_rejects_filename_over_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=16)
    # A name that would match (download) but exceeds 16 characters -> discarded.
    assert engine.evaluate(FileCandidate(filename="Keroro N°062A.avi")) is None


def test_evaluate_accepts_filename_at_or_below_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=4096)
    decision = engine.evaluate(FileCandidate(filename="Keroro N°062A.avi"))
    assert decision is not None
    assert decision.tier == "download"


def test_engine_resolves_each_target_once_at_construction() -> None:
    # Pre-resolution happens at construction: evaluate does not rebuild a tree.
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A, _TARGET_62B))
    assert len(engine._resolved) == 2
    assert {rt.target.target_id for rt in engine._resolved} == {"062A", "062B"}


def test_evaluate_explanation_lists_coverage_value_even_below_threshold() -> None:
    # "keroro rediffusion.mkv" wins in catalog (keroro) on 062A; title_hit (62A title) has a
    # score 0.0 < 0.6: it is NOT in tokens_matched but ITS SCORE appears in
    # coverage_values (useful to debug a threshold).
    decision = _canonical_engine().evaluate(FileCandidate(filename="keroro rediffusion.mkv"))
    assert decision is not None
    coverage_names = [name for name, _ in decision.explanation.coverage_values]
    assert "title_hit" in coverage_names
    assert "title_hit" not in decision.explanation.tokens_matched


def test_explanation_on_real_62a_lists_fired_rules_tokens_and_coverage() -> None:
    decision = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert decision is not None
    explanation = decision.explanation
    assert explanation.target_id == "062A"
    # The real 62A fires on 4 rules (empirically verified) -> several rules_fired.
    # segment_id_loose never fires (bi-segment -> {mono_gate} never-match) so
    # numero_nu_confirmed/numero_nu are absent; archive_candidate absent (no archive).
    assert explanation.rules_fired == (
        "id_segment_exact",
        "title_confirmed",
        "title_review",
        "keroro_large",
    )
    # Named tokens that match (sorted). title_hit is a coverage and matches (value 1.0).
    assert "title_hit" in explanation.tokens_matched
    assert "keroro" in explanation.tokens_matched
    assert "segment_id" in explanation.tokens_matched
    assert explanation.tokens_matched == tuple(sorted(explanation.tokens_matched))
    # coverage_values: title_hit present with its value (isinstance branch TRUE).
    assert explanation.coverage_values == (("title_hit", 1.0),)


def test_explanation_single_rule_fired_and_no_coverage_token() -> None:
    # Config WITHOUT any coverage token -> empty coverage_values (isinstance branch FALSE).
    raw: dict[str, object] = {
        "tokens": {
            "is_video": {"regex": r"\.(avi|mkv)$"},
            "seg": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        },
        "rules": [{"name": "only", "tier": "download", "all": ["is_video", "seg"]}],
    }
    engine = MatchingEngine(parse_matcher_config(raw), (_TARGET_62A,))
    decision = engine.evaluate(FileCandidate(filename="N°062A.avi"))
    assert decision is not None
    assert decision.explanation.rules_fired == ("only",)  # a single rule
    assert decision.explanation.coverage_values == ()  # no coverage
    assert decision.explanation.tokens_matched == ("is_video", "seg")


# --- Mono B-safe routing: segment_id_loose / numero_nu / numero_nu_confirmed (Task 4) ---
# A mono target (sole_segment=True) with a BARE number ("Keroro 10.avi") is routed to
# notify (human review), NEVER to download EXCEPT with a source marker (numero_nu_confirmed).
# For a bi-segment target, {mono_gate} neutralizes segment_id_loose (never-match): the
# bare-number rules never concern it.

_TARGET_MONO = TargetSegment(
    season=1,
    seasonal_number=10,
    absolute_number=10,
    segment="a",
    title="Episode mono",
    sole_segment=True,
)

_MONO_ROUTING_RAW: dict[str, object] = {
    "tokens": {
        "is_video": {"regex": r"\.(avi|mkv)$"},
        "keroro": {"keyword": "keroro"},
        "keroro_titar": {"any": ["keroro"]},
        "foreign_lang": {"regex": r"\b(ITA|KOR)\b"},
        "french_safe": {"not": "foreign_lang"},
        "is_keroro": {"all": ["french_safe", "keroro_titar"]},
        "not_episode": {"regex": r"opening|ending|\bsample\b"},
        "is_episode": {"all": ["is_keroro", {"not": "not_episode"}]},
        "teletoon": {"regex": "t[eé]l[eé]toon"},
        "source_marker": {"any": ["teletoon"]},
        "segment_id": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        "segment_id_loose": {
            "regex": r"{mono_gate}(?:^|[^0-9])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9]|$)"
        },
    },
    "rules": [
        {
            "name": "id_segment_exact",
            "tier": "download",
            "all": ["is_episode", "is_video", "segment_id"],
        },
        {
            "name": "numero_nu_confirmed",
            "tier": "download",
            "all": ["is_episode", "is_video", "segment_id_loose", "source_marker"],
        },
        {
            "name": "numero_nu",
            "tier": "notify",
            "all": ["is_episode", "is_video", "segment_id_loose"],
        },
        {"name": "keroro_large", "tier": "catalog", "all": ["is_keroro"]},
    ],
}


def _mono_routing_engine() -> MatchingEngine:
    # 2 targets: target_mono (sole_segment=True) and the existing bi-segment 62A.
    config = parse_matcher_config(_MONO_ROUTING_RAW)
    return MatchingEngine(config, (_TARGET_MONO, _TARGET_62A))


def test_evaluate_bare_number_on_mono_target_is_notify_numero_nu() -> None:
    # "Keroro 10.avi": bare number on mono target -> notify/numero_nu, NEVER download.
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 10.avi"))
    assert decision is not None
    assert decision.tier == "notify"
    assert decision.rule_name == "numero_nu"
    assert decision.target_id == "010A"


def test_evaluate_bare_number_on_bi_segment_target_never_fires_numero_nu() -> None:
    # "Keroro 62.avi": 62 is the absolute number of the BI-segment target 62A -> {mono_gate}
    # neutralizes segment_id_loose for it (never-match) -> numero_nu/numero_nu_confirmed
    # never fire for it; only keroro_large (catalog) matches (tie-break
    # target_id -> 010A).
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 62.avi"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"


def test_evaluate_lettered_mono_number_stays_download_via_strict_segment_id() -> None:
    # Structured mono WITH a letter ("N°010A") stays download-eligible via the strict segment_id
    # (unchanged, Task 2): the mandatory letter is authoritative, independent of segment_id_loose.
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro N°010A.avi"))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.rule_name == "id_segment_exact"
    assert decision.target_id == "010A"


def test_evaluate_bare_number_digit_boundary_guard_rejects_substring() -> None:
    # Digit-boundary guard: "10" is a SUBSTRING of "105", NOT an isolated bare number ->
    # segment_id_loose does not match -> no numero_nu (only keroro_large matches).
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 105.avi"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"


def test_evaluate_bare_number_on_mono_with_source_marker_is_download() -> None:
    # Bare number + source marker (teletoon) on mono target -> numero_nu_confirmed (download).
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 10 teletoon.avi"))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.rule_name == "numero_nu_confirmed"
    assert decision.target_id == "010A"


def test_evaluate_opening_with_bare_number_demoted_to_catalog_by_not_episode() -> None:
    # "Keroro 10 opening.avi": bare mono number BUT "opening" -> not_episode -> is_episode
    # false -> neither numero_nu nor numero_nu_confirmed; keroro_large (catalog) remains.
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 10 opening.avi"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"
    assert decision.target_id == "010A"
