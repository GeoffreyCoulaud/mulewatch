import datetime

import pytest

from emule_indexer.domain.matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
    CoverageDef,
    KeywordDef,
    NotDef,
    RegexDef,
    TokenRef,
)
from emule_indexer.domain.matching.validation import (
    ConfigError,
    CycleError,
    DepthExceededError,
    UnknownTokenError,
    parse_matcher_config,
    parse_targets,
    validate_config,
)


def test_parse_leaf_token_defs() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "teletoon": {"regex": "t[eé]l[eé]toon"},
                "video": {"regex": "\\.(avi|mkv)$", "flags": ""},
                "title_hit": {"coverage": "title", "min": 0.6},
                "small": {"attr_between": "size_mb", "min": 30, "max": 600},
            },
            "rules": [],
        }
    )
    assert config.tokens["keroro"] == KeywordDef(phrase="keroro")
    assert config.tokens["teletoon"] == RegexDef(pattern="t[eé]l[eé]toon", flags="i")
    assert config.tokens["video"] == RegexDef(pattern="\\.(avi|mkv)$", flags="")
    assert config.tokens["title_hit"] == CoverageDef(reference="title", min=0.6)
    assert config.tokens["small"] == AttrBetweenDef(attr="size_mb", min=30.0, max=600.0)


def test_parse_composite_token_def() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "titar": {"keyword": "titar"},
                "kt": {"any": ["keroro", "titar"]},
            },
            "rules": [],
        }
    )
    assert config.tokens["kt"] == AnyDef(operands=("keroro", "titar"))


def test_parse_not_token_def() -> None:
    config = parse_matcher_config(
        {"tokens": {"keroro": {"keyword": "keroro"}, "nk": {"not": "keroro"}}, "rules": []}
    )
    assert config.tokens["nk"] == NotDef(operand="keroro")


def test_parse_rule_with_inline_token_ref_and_condition() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "title_hit": {"coverage": "title", "min": 0.6},
                "seg": {"regex": "0*{number}"},
            },
            "rules": [
                {
                    "name": "numero_titre",
                    "tier": "notify",
                    "all": ["seg", {"token": "title_hit", "min": 0.5}],
                }
            ],
        }
    )
    rule = config.rules[0]
    assert rule.name == "numero_titre"
    assert rule.tier == "notify"
    assert rule.condition == AllDef(operands=("seg", TokenRef(name="title_hit", min=0.5)))


def test_parse_rule_with_nested_inline_condition() -> None:
    config = parse_matcher_config(
        {
            "tokens": {"keroro": {"keyword": "keroro"}, "titar": {"keyword": "titar"}},
            "rules": [{"name": "r", "tier": "catalog", "not": {"any": ["keroro", "titar"]}}],
        }
    )
    assert config.rules[0].condition == NotDef(operand=AnyDef(operands=("keroro", "titar")))


def test_unknown_tier_raises_and_names_it() -> None:
    with pytest.raises(ConfigError, match="bogus"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "bogus", "any": ["keroro"]}],
            }
        )


def test_attr_between_unknown_attr_raises_and_names_it() -> None:
    with pytest.raises(ConfigError, match="codec"):
        parse_matcher_config({"tokens": {"c": {"attr_between": "codec", "min": 1}}, "rules": []})


def test_unknown_token_definition_shape_raises() -> None:
    with pytest.raises(ConfigError, match="forme de token inconnue"):
        parse_matcher_config({"tokens": {"x": {"frobnicate": "y"}}, "rules": []})


def test_token_def_with_multiple_keys_raises() -> None:
    with pytest.raises(ConfigError, match="exactement une clé"):
        parse_matcher_config({"tokens": {"x": {"keyword": "a", "regex": "b"}}, "rules": []})


def test_override_on_non_coverage_token_raises() -> None:
    with pytest.raises(ConfigError, match="kw"):
        parse_matcher_config(
            {
                "tokens": {"kw": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": [{"token": "kw", "min": 0.5}]}],
            }
        )


def test_rule_without_condition_key_raises() -> None:
    with pytest.raises(ConfigError, match="condition"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog"}],
            }
        )


def test_rule_with_two_condition_keys_raises() -> None:
    with pytest.raises(ConfigError, match="une seule condition"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": ["keroro"], "any": ["keroro"]}],
            }
        )


def test_token_ref_missing_name_raises() -> None:
    with pytest.raises(ConfigError, match="token"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": [{"min": 0.5}]}],
            }
        )


def test_operand_wrong_type_raises() -> None:
    with pytest.raises(ConfigError, match="opérande"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": [123]}],
            }
        )


def test_parse_targets_builds_segments() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 2,
                    "number": 62,
                    "broadcast_date": datetime.date(2008, 9, 21),
                    "status": "partial",
                    "segments": [
                        {"letter": "A", "title": "Les demoiselles", "aliases": ["alt"]},
                        {"letter": "B", "title": "Le grand combat"},
                    ],
                }
            ]
        }
    )
    assert len(targets) == 2
    a, b = targets
    assert a.target_id == "S2E062A"
    assert a.broadcast_date == datetime.date(2008, 9, 21)
    assert a.status == "partial"
    assert a.aliases == ("alt",)
    assert b.target_id == "S2E062B"
    assert b.aliases == ()
    assert b.status == "partial"


def test_parse_targets_default_status_is_lost() -> None:
    targets = parse_targets(
        {"episodes": [{"season": 1, "number": 5, "segments": [{"letter": "a", "title": "x"}]}]}
    )
    assert targets[0].status == "lost"
    assert targets[0].broadcast_date is None


def test_parse_targets_missing_episodes_raises() -> None:
    with pytest.raises(ConfigError, match="episodes"):
        parse_targets({})


def test_parse_targets_missing_required_episode_field_raises() -> None:
    with pytest.raises(ConfigError, match="number"):
        parse_targets({"episodes": [{"season": 1, "segments": [{"letter": "a", "title": "x"}]}]})


def test_parse_targets_missing_required_segment_field_raises() -> None:
    with pytest.raises(ConfigError, match="title"):
        parse_targets({"episodes": [{"season": 1, "number": 5, "segments": [{"letter": "a"}]}]})


# --- Résidus de couverture de branches ---


def test_token_def_non_mapping_raises() -> None:
    """_require_mapping lève ConfigError si la def de token n'est pas un mapping."""
    with pytest.raises(ConfigError, match="mapping"):
        parse_matcher_config({"tokens": {"x": "not-a-dict"}, "rules": []})


def test_composite_token_def_multiple_condition_keys_raises() -> None:
    """_parse_condition lève si un token composite contient 2+ clés de condition."""
    with pytest.raises(ConfigError, match="une seule condition"):
        parse_matcher_config({"tokens": {"x": {"all": ["a"], "any": ["b"]}}, "rules": []})


def test_all_body_non_list_raises() -> None:
    """_parse_condition lève si le corps de 'all' n'est pas une liste."""
    with pytest.raises(ConfigError, match="liste"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": "keroro"}],
            }
        )


def test_coverage_token_missing_min_raises() -> None:
    """_parse_token_def lève si un coverage ne déclare pas 'min'."""
    with pytest.raises(ConfigError, match="min"):
        parse_matcher_config({"tokens": {"t": {"coverage": "title"}}, "rules": []})


def test_rule_without_name_raises() -> None:
    """_parse_rule lève si une règle n'a pas de 'name'."""
    with pytest.raises(ConfigError, match="name"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"tier": "catalog", "any": ["keroro"]}],
            }
        )


def test_rules_non_list_raises() -> None:
    """parse_matcher_config lève si 'rules' n'est pas une liste."""
    with pytest.raises(ConfigError, match="rules"):
        parse_matcher_config({"tokens": {}, "rules": "not-a-list"})


def test_token_ref_with_coverage_token_no_override_ok() -> None:
    """TokenRef sur un coverage sans override min/fuzz : valide, pas d'erreur."""
    config = parse_matcher_config(
        {
            "tokens": {"title_hit": {"coverage": "title", "min": 0.6}},
            "rules": [{"name": "r", "tier": "catalog", "all": [{"token": "title_hit"}]}],
        }
    )
    assert config.rules[0].name == "r"


def test_parse_targets_episode_without_segments() -> None:
    """Épisode sans clé 'segments' : boucle vide, aucun segment émis."""
    targets = parse_targets({"episodes": [{"season": 1, "number": 1}]})
    assert targets == ()


# --- Task 6 : graph validation (DAG, depth, RE2 compile-check) ---


def test_unknown_token_reference_raises_and_names_it() -> None:
    with pytest.raises(UnknownTokenError, match="ghost"):
        parse_matcher_config(
            {
                "tokens": {"kt": {"any": ["keroro", "ghost"]}, "keroro": {"keyword": "keroro"}},
                "rules": [],
            }
        )


def test_unknown_token_in_rule_raises() -> None:
    with pytest.raises(UnknownTokenError, match="ghost"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": ["keroro", "ghost"]}],
            }
        )


def test_direct_cycle_is_detected_and_named() -> None:
    with pytest.raises(CycleError) as excinfo:
        parse_matcher_config({"tokens": {"a": {"any": ["b"]}, "b": {"any": ["a"]}}, "rules": []})
    message = str(excinfo.value)
    assert "a -> b -> a" in message or "b -> a -> b" in message


def test_self_cycle_is_detected() -> None:
    with pytest.raises(CycleError, match="loop"):
        parse_matcher_config({"tokens": {"loop": {"any": ["loop"]}}, "rules": []})


def test_acyclic_composite_graph_validates() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "titar": {"keyword": "titar"},
                "kt": {"any": ["keroro", "titar"]},
                "deep": {"all": ["kt", "keroro"]},
            },
            "rules": [{"name": "r", "tier": "catalog", "any": ["deep"]}],
        }
    )
    assert "deep" in config.tokens


def test_regex_compile_check_rejects_bad_pattern() -> None:
    with pytest.raises(ConfigError, match="RE2"):
        parse_matcher_config({"tokens": {"bad": {"regex": "(unbalanced"}}, "rules": []})


def test_regex_unknown_placeholder_rejected_at_load() -> None:
    with pytest.raises(ConfigError, match="bogus"):
        parse_matcher_config({"tokens": {"bad": {"regex": "n {bogus}"}}, "rules": []})


def test_regex_with_known_placeholders_validates() -> None:
    config = parse_matcher_config(
        {"tokens": {"seg": {"regex": "n[°o]?\\s*0*{number}\\s*{segment}"}}, "rules": []}
    )
    assert "seg" in config.tokens


def test_regex_date_alt_placeholder_validates_via_probe() -> None:
    # {date_alt} exige un broadcast_date ; la sonde de validation en fournit un.
    config = parse_matcher_config({"tokens": {"air": {"regex": "{date_alt}"}}, "rules": []})
    assert "air" in config.tokens


def test_depth_within_bound_validates() -> None:
    # Chaîne a -> b -> c (profondeur 3) avec max_depth=3 : OK.
    config = parse_matcher_config(
        {
            "tokens": {
                "c": {"keyword": "x"},
                "b": {"any": ["c"]},
                "a": {"any": ["b"]},
            },
            "rules": [],
        }
    )
    validate_config(config, max_depth=3)


def test_depth_exceeded_raises() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "c": {"keyword": "x"},
                "b": {"any": ["c"]},
                "a": {"any": ["b"]},
            },
            "rules": [],
        }
    )
    with pytest.raises(DepthExceededError, match="profondeur"):
        validate_config(config, max_depth=2)


def test_default_max_depth_is_32() -> None:
    # Une chaîne de 33 tokens dépasse le défaut 32.
    tokens: dict[str, object] = {"t0": {"keyword": "x"}}
    for i in range(1, 34):
        tokens[f"t{i}"] = {"any": [f"t{i - 1}"]}
    with pytest.raises(DepthExceededError):
        parse_matcher_config({"tokens": tokens, "rules": []})


def test_deep_chain_root_first_caught_by_dfs_guard() -> None:
    # Ordre racine-d'abord : le DFS de _check_acyclic descend toute la chaîne, donc le
    # garde-fou len(stack)>=max_depth lève DepthExceededError avant tout RecursionError.
    tokens: dict[str, object] = {}
    for i in range(40, 0, -1):  # t40 (racine) défini en premier, t0 (feuille) en dernier
        tokens[f"t{i}"] = {"any": [f"t{i - 1}"]}
    tokens["t0"] = {"keyword": "x"}
    with pytest.raises(DepthExceededError):
        parse_matcher_config({"tokens": tokens, "rules": []})


def test_empty_config_validates() -> None:
    # Table de tokens vide : _max_resolution_depth doit renvoyer 0 (default), pas d'erreur.
    config = parse_matcher_config({"tokens": {}, "rules": []})
    assert config.tokens == {}


def test_coverage_override_forward_reference_in_composite_validates() -> None:
    # Régression : un token composite référençant {token: cov, min: …} où cov est
    # défini APRÈS ne doit PAS être rejeté (validation override différée au graphe).
    config = parse_matcher_config(
        {
            "tokens": {
                "combo": {"any": [{"token": "title_hit", "min": 0.4}, "keroro"]},
                "title_hit": {"coverage": "title", "min": 0.6},
                "keroro": {"keyword": "keroro"},
            },
            "rules": [],
        }
    )
    assert "combo" in config.tokens
