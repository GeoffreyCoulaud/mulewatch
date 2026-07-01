import dataclasses

import pytest

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
    # Cohérence : tout palier licite (TIERS) a un rang, et aucun rang orphelin.
    assert set(_TIER_RANK) == TIERS


def test_explanation_is_frozen_and_holds_fields() -> None:
    explanation = Explanation(
        target_id="S2E062A",
        rules_fired=("id_segment_exact", "keroro_large"),
        tokens_matched=("is_video", "keroro", "segment_id"),
        coverage_values=(("title_hit", 1.0),),
    )
    assert explanation.target_id == "S2E062A"
    assert explanation.rules_fired == ("id_segment_exact", "keroro_large")
    assert explanation.tokens_matched == ("is_video", "keroro", "segment_id")
    assert explanation.coverage_values == (("title_hit", 1.0),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        explanation.target_id = "S2E062B"  # type: ignore[misc]


def test_match_decision_is_frozen_and_holds_persisted_columns_plus_explanation() -> None:
    explanation = Explanation(
        target_id="S2E062A",
        rules_fired=("id_segment_exact",),
        tokens_matched=("keroro",),
        coverage_values=(),
    )
    decision = MatchDecision(
        target_id="S2E062A",
        rule_name="id_segment_exact",
        tier="download",
        explanation=explanation,
    )
    # Les trois colonnes que match_decisions persistera (spec §11).
    assert decision.target_id == "S2E062A"
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

# Config minimale à deux règles d'index distinct pour exercer "1re vraie" et "boucle".
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
    # Pas vidéo + pas de segment 062A -> "exact" faux ; "keroro" vrai -> 2e règle.
    candidate = FileCandidate(filename="keroro autre chose.txt")
    assert _first_matching_rule(config, resolved, candidate) == (1, "large", "catalog")


def test_first_matching_rule_returns_none_when_no_rule_true() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    candidate = FileCandidate(filename="naruto 062.txt")
    assert _first_matching_rule(config, resolved, candidate) is None


# --- Config canonique §8.3 (réutilisée par plusieurs tests) ---
_CANONICAL_RAW: dict[str, object] = {
    "tokens": {
        "keroro": {"keyword": "keroro"},
        "titar": {"keyword": "titar"},
        "keroro_titar": {"any": ["keroro", "titar"]},
        "foreign_lang": {
            "regex": (
                r"\b(ITA|KOR|Korean|Italiano|Coreano|VOSTFR|VOSTA|Subs?FR|"
                r"Espa[nñ]ol|English\s?Dub|ENG)\b|dino-riders|guerriero|risveglio|"
                r"sarxento|sargento|benjo|fatacolorata|catala|signor|\((?:ita|j|jp|k|kr|ks)\)"
            ),
        },
        "french_safe": {"not": "foreign_lang"},
        "is_keroro": {"all": ["french_safe", "keroro_titar"]},
        "not_episode": {
            "regex": r"opening|ending|g[eé]n[eé]rique|\bsample\b|preview|trailer|bande.?annonce"
        },
        "is_episode": {"all": ["is_keroro", {"not": "not_episode"}]},
        "teletoon": {"regex": "t[eé]l[eé]toon"},
        "idf1": {"regex": r"\bidf\s?1\b"},
        "vf": {"regex": r"\b(?:vf|vff|vfb)\b|version\s?francaise"},
        "source_marker": {"any": ["teletoon", "idf1", "vf"]},
        "segment_id": {
            "regex": (
                r"(?:n[°o]?\s*0*{absolute_number}|s0*{season}\s*e0*{seasonal_number}"
                r"|0*{season}\s*x\s*0*{seasonal_number})\s*{segment}"
            )
        },
        "segment_id_loose": {
            "regex": r"{mono_gate}(?:^|[^0-9])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9]|$)"
        },
        "title_hit": {"coverage": "title", "min": 0.6},
        "is_video": {"regex": r"\.(avi|mkv|mp4|mpg|mpeg|divx|m4v|ogm)$"},
        "is_archive": {"regex": r"\.(zip|7z|rar|r\d\d|z\d\d|part\d+\.rar)$"},
    },
    "rules": [
        {
            "name": "id_segment_exact",
            "tier": "download",
            "all": ["is_episode", "is_video", "segment_id"],
        },
        {
            "name": "title_confirmed",
            "tier": "download",
            "all": ["is_episode", "is_video", "title_hit", "source_marker"],
        },
        {
            "name": "numero_nu_confirmed",
            "tier": "download",
            "all": ["is_episode", "is_video", "segment_id_loose", "source_marker"],
        },
        {"name": "title_review", "tier": "notify", "all": ["is_episode", "is_video", "title_hit"]},
        {
            "name": "numero_nu",
            "tier": "notify",
            "all": ["is_episode", "is_video", "segment_id_loose"],
        },
        {
            "name": "archive_candidate",
            "tier": "notify",
            "all": [
                "is_episode",
                "is_archive",
                {"any": ["segment_id", "title_hit", "source_marker"]},
            ],
        },
        {"name": "keroro_large", "tier": "catalog", "all": ["is_keroro"]},
    ],
}

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
    assert decision.target_id == "S2E062A"


def test_evaluate_discards_non_keroro_file() -> None:
    decision = _canonical_engine().evaluate(FileCandidate(filename="Naruto épisode 062 VF.avi"))
    assert decision is None


def test_evaluate_highest_tier_comes_from_a_different_target() -> None:
    # "keroro N°062B.avi" : 62A -> catalog (keroro_large) ; 62B -> download (id_segment_exact).
    # Le palier le plus haut vient d'une AUTRE cible que le plus bas -> isole l'agrégation
    # inter-cibles (un bug ne regardant que la 1re cible renverrait catalog/S2E062A).
    decision = _canonical_engine().evaluate(FileCandidate(filename="keroro N°062B.avi"))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.rule_name == "id_segment_exact"
    assert decision.target_id == "S2E062B"


def test_evaluate_notify_tier_via_title_review() -> None:
    # Titre proche, PAS de marqueur de source -> title_review (notify), pas download.
    candidate = FileCandidate(filename="KERORO Les demoiselles cambrioleuses.avi")
    decision = _canonical_engine().evaluate(candidate)
    assert decision is not None
    assert decision.tier == "notify"
    assert decision.rule_name == "title_review"
    assert decision.target_id == "S2E062A"


def test_evaluate_tiebreak_same_tier_lowest_target_id_wins() -> None:
    # Fichier "Keroro" seul -> 62A et 62B donnent TOUS DEUX keroro_large (catalog, index 3).
    # Même palier ET même index -> départage par target_id : S2E062A < S2E062B.
    decision = _canonical_engine().evaluate(FileCandidate(filename="Keroro Gunso opening.mkv"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"
    assert decision.target_id == "S2E062A"


# --- Départage par INDEX de règle (isolé du target_id) ---
# Deux règles download ; la cible au target_id PLUS GRAND matche la règle d'index PLUS
# PETIT. Si seul target_id départageait, la mauvaise cible gagnerait ; l'index doit primer.
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
    # target_high : grand target_id (S2E099Z), matche by_segment (index 0).
    target_high = TargetSegment(
        season=2, seasonal_number=99, absolute_number=99, segment="z", title="zzz aucun rapport"
    )
    # target_low : petit target_id (S2E001A), matche by_title (index 1).
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
    # Index 0 (by_segment sur S2E099Z) prime sur index 1 (by_title sur S2E001A),
    # MALGRÉ S2E001A < S2E099Z : l'index départage AVANT le target_id.
    assert decision.rule_name == "by_segment"
    assert decision.target_id == "S2E099Z"


def test_evaluate_rejects_filename_over_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=16)
    # Un nom qui matcherait (download) mais dépasse 16 caractères -> écarté.
    assert engine.evaluate(FileCandidate(filename="Keroro N°062A.avi")) is None


def test_evaluate_accepts_filename_at_or_below_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=4096)
    decision = engine.evaluate(FileCandidate(filename="Keroro N°062A.avi"))
    assert decision is not None
    assert decision.tier == "download"


def test_engine_resolves_each_target_once_at_construction() -> None:
    # La pré-résolution arrive à la construction : evaluate ne reconstruit pas d'arbre.
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A, _TARGET_62B))
    assert len(engine._resolved) == 2
    assert {rt.target.target_id for rt in engine._resolved} == {"S2E062A", "S2E062B"}


def test_evaluate_explanation_lists_coverage_value_even_below_threshold() -> None:
    # "keroro gunso.mkv" gagne en catalog (keroro) sur S2E062A ; title_hit (titre 62A) a un
    # score 0.0 < 0.6 : il N'EST PAS dans tokens_matched mais SON SCORE figure dans
    # coverage_values (utile pour déboguer un seuil).
    decision = _canonical_engine().evaluate(FileCandidate(filename="keroro gunso.mkv"))
    assert decision is not None
    coverage_names = [name for name, _ in decision.explanation.coverage_values]
    assert "title_hit" in coverage_names
    assert "title_hit" not in decision.explanation.tokens_matched


def test_explanation_on_real_62a_lists_fired_rules_tokens_and_coverage() -> None:
    decision = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert decision is not None
    explanation = decision.explanation
    assert explanation.target_id == "S2E062A"
    # Le réel 62A fait feu sur 4 règles (vérifié empiriquement) -> plusieurs rules_fired.
    # segment_id_loose ne fait jamais feu (bi-segment -> {mono_gate} never-match) donc
    # numero_nu_confirmed/numero_nu sont absents ; archive_candidate absent (pas d'archive).
    assert explanation.rules_fired == (
        "id_segment_exact",
        "title_confirmed",
        "title_review",
        "keroro_large",
    )
    # Tokens nommés qui matchent (triés). title_hit est un coverage et matche (value 1.0).
    assert "title_hit" in explanation.tokens_matched
    assert "keroro" in explanation.tokens_matched
    assert "segment_id" in explanation.tokens_matched
    assert explanation.tokens_matched == tuple(sorted(explanation.tokens_matched))
    # coverage_values : title_hit présent avec sa value (branche isinstance VRAIE).
    assert explanation.coverage_values == (("title_hit", 1.0),)


def test_explanation_single_rule_fired_and_no_coverage_token() -> None:
    # Config SANS aucun token coverage -> coverage_values vide (branche isinstance FAUSSE).
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
    assert decision.explanation.rules_fired == ("only",)  # une seule règle
    assert decision.explanation.coverage_values == ()  # aucun coverage
    assert decision.explanation.tokens_matched == ("is_video", "seg")


# --- Routage mono B-safe : segment_id_loose / numero_nu / numero_nu_confirmed (Task 4) ---
# Une cible mono (sole_segment=True) avec un numéro NU ("Keroro 10.avi") remonte en
# notify (revue humaine), JAMAIS en download SAUF marqueur de source (numero_nu_confirmed).
# Pour une cible bi-segment, {mono_gate} neutralise segment_id_loose (never-match) : les
# règles à numéro nu ne la concernent jamais.

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
    # 2 cibles : target_mono (sole_segment=True) et la 62A bi-segment existante.
    config = parse_matcher_config(_MONO_ROUTING_RAW)
    return MatchingEngine(config, (_TARGET_MONO, _TARGET_62A))


def test_evaluate_bare_number_on_mono_target_is_notify_numero_nu_mono() -> None:
    # "Keroro 10.avi" : numéro nu sur cible mono -> notify/numero_nu, JAMAIS download.
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 10.avi"))
    assert decision is not None
    assert decision.tier == "notify"
    assert decision.rule_name == "numero_nu"
    assert decision.target_id == "S1E010A"


def test_evaluate_bare_number_on_bi_segment_target_never_fires_numero_nu_mono() -> None:
    # "Keroro 62.avi" : 62 est le numéro absolu de la cible BI-segment 62A -> {mono_gate}
    # neutralise segment_id_loose pour elle (never-match) -> numero_nu/numero_nu_confirmed
    # ne font jamais feu pour elle ; seul keroro_large (catalog) matche (départage
    # target_id -> S1E010A).
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 62.avi"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"


def test_evaluate_lettered_mono_number_stays_download_via_strict_segment_id() -> None:
    # Mono structuré AVEC lettre ("N°010A") reste éligible download via le segment_id strict
    # (inchangé, Task 2) : la lettre obligatoire fait foi, indépendamment de segment_id_loose.
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro N°010A.avi"))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.rule_name == "id_segment_exact"
    assert decision.target_id == "S1E010A"


def test_evaluate_bare_number_digit_boundary_guard_rejects_substring() -> None:
    # Garde de bord chiffre : "10" est un SOUS-STRING de "105", PAS un numéro nu isolé ->
    # segment_id_loose ne matche pas -> pas de numero_nu (seul keroro_large matche).
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 105.avi"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"


def test_evaluate_bare_number_on_mono_with_source_marker_is_download() -> None:
    # Numéro nu + marqueur de source (teletoon) sur cible mono -> numero_nu_confirmed (download).
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 10 teletoon.avi"))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.rule_name == "numero_nu_confirmed"
    assert decision.target_id == "S1E010A"


def test_evaluate_opening_with_bare_number_demoted_to_catalog_by_not_episode() -> None:
    # "Keroro 10 opening.avi" : numéro nu mono MAIS "opening" -> not_episode -> is_episode
    # faux -> ni numero_nu ni numero_nu_confirmed ; keroro_large (catalog) reste.
    decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 10 opening.avi"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"
