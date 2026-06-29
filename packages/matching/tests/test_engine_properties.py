import datetime
import random

from catalog_matching.engine import MatchingEngine
from catalog_matching.models import FileCandidate, TargetSegment
from catalog_matching.validation import parse_matcher_config

# Seed CONSTANT : chaque run est identique (zéro flakiness, cf. spec §16).
_SEED = 20260610

_CANONICAL_RAW: dict[str, object] = {
    "tokens": {
        "keroro": {"keyword": "keroro"},
        "titar": {"keyword": "titar"},
        "keroro_titar": {"any": ["keroro", "titar"]},
        "teletoon": {"regex": "t[eé]l[eé]toon"},
        "segment_id": {"regex": r"n[°o]?\s*0*{number}\s*{segment}"},
        "foreign_lang": {
            "regex": (
                r"\b(ITA|KOR|Korean|Italiano|Coreano|VOSTFR|VOSTA|Subs?FR|"
                r"Espa[nñ]ol|English\s?Dub|ENG)\b"
            ),
        },
        "french_safe": {"not": "foreign_lang"},
        "title_hit": {"coverage": "title", "min": 0.6},
        "is_video": {"regex": r"\.(avi|mkv|mp4|mpg|ogm)$"},
    },
    "rules": [
        {
            "name": "id_segment_exact",
            "tier": "download",
            "all": ["french_safe", "is_video", "segment_id", "keroro"],
        },
        {
            "name": "teletoon_titre",
            "tier": "download",
            "all": ["french_safe", "teletoon", {"token": "title_hit", "min": 0.6}],
        },
        {
            "name": "numero_titre",
            "tier": "notify",
            "all": ["french_safe", "segment_id", {"token": "title_hit", "min": 0.5}],
        },
        {"name": "keroro_large", "tier": "catalog", "all": ["french_safe", "keroro_titar"]},
    ],
}


def _targets() -> list[TargetSegment]:
    date = datetime.date(2008, 9, 21)
    return [
        TargetSegment(
            season=2,
            number=62,
            segment="a",
            title="Les demoiselles cambrioleuses",
            broadcast_date=date,
            status="partial",
        ),
        TargetSegment(
            season=2,
            number=62,
            segment="b",
            title="Le grand combat sous-marin",
            broadcast_date=date,
            status="lost",
        ),
        TargetSegment(
            season=1,
            number=5,
            segment="a",
            title="Un titre quelconque",
            broadcast_date=date,
            status="lost",
        ),
    ]


_FILENAMES = [
    "[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses »"
    " [21 septembre 2008 TELETOON].avi",
    "KERORO N°062A Les demoiselles cambrioleuses.txt",
    "Keroro Gunso opening.mkv",
    "Naruto épisode 062 VF.avi",
    "keroro mission titar 062b grand combat.avi",
]


def test_property_decision_invariant_under_target_reordering() -> None:
    # P1 : réordonner les cibles ne change JAMAIS la décision (§8.5 déterminisme).
    config = parse_matcher_config(_CANONICAL_RAW)
    rng = random.Random(_SEED)
    base_targets = _targets()
    reference_engine = MatchingEngine(config, base_targets)
    for filename in _FILENAMES:
        candidate = FileCandidate(filename=filename)
        expected = reference_engine.evaluate(candidate)
        for _ in range(20):  # 20 permutations seedées par fichier (déterministe)
            shuffled = base_targets[:]
            rng.shuffle(shuffled)
            got = MatchingEngine(config, shuffled).evaluate(candidate)
            assert got == expected, f"décision dépend de l'ordre des cibles pour {filename!r}"


def test_property_higher_priority_rule_never_lowers_tier() -> None:
    # P2 : ajouter une règle PLUS PRIORITAIRE (index 0) de palier >= ne baisse jamais le
    # palier résultant (§16). On compare la config canonique à une variante où l'on
    # PRÉPEND une règle download large ; pour tout fichier déjà décidé, le palier ne baisse pas.
    from catalog_matching.engine import _TIER_RANK

    config_base = parse_matcher_config(_CANONICAL_RAW)
    raw_boosted = {
        "tokens": dict(_CANONICAL_RAW["tokens"]),  # type: ignore[call-overload]
        "rules": [
            # Règle download PRÉPENDUE (index 0) : tout fichier "keroro" -> download.
            {"name": "boost_keroro_download", "tier": "download", "any": ["keroro_titar"]},
            *_CANONICAL_RAW["rules"],  # type: ignore[misc]
        ],
    }
    config_boosted = parse_matcher_config(raw_boosted)
    targets = _targets()
    engine_base = MatchingEngine(config_base, targets)
    engine_boosted = MatchingEngine(config_boosted, targets)
    for filename in _FILENAMES:
        candidate = FileCandidate(filename=filename)
        base = engine_base.evaluate(candidate)
        boosted = engine_boosted.evaluate(candidate)
        if base is None:
            continue  # rien à comparer : un fichier écarté peut le rester
        assert boosted is not None, f"{filename!r}: décidé sans boost, écarté avec ?!"
        assert _TIER_RANK[boosted.tier] >= _TIER_RANK[base.tier], (
            f"{filename!r}: une règle plus prioritaire a BAISSÉ le palier"
        )
