"""Validation au chargement (fail-fast) : schéma YAML parsé -> modèle de config.

Domaine PUR : reçoit des structures déjà parsées (``dict``/``list``), n'importe pas
``yaml``, ne touche pas le disque. Couvre la spec §8.4 (validation au chargement) côté
schéma + validations locales (tier fermé, enum ``attr_between``, override coverage-only).
La validation de graphe (DAG/profondeur) et le compile-check RE2 sont ajoutés en Task 6.
"""

import datetime
from typing import Any

from emule_indexer.domain.matching.config import (
    TIERS,
    AllDef,
    AnyDef,
    AttrBetweenDef,
    Condition,
    CoverageDef,
    KeywordDef,
    MatcherConfig,
    NotDef,
    Operand,
    RegexDef,
    Rule,
    TokenDef,
    TokenRef,
)
from emule_indexer.domain.matching.matchers import ATTR_NAMES
from emule_indexer.domain.matching.models import TargetSegment

_CONDITION_KEYS = ("all", "any", "not")


class ConfigError(Exception):
    """Erreur fatale de configuration au chargement (schéma, tier, enum, override)."""


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} : mapping attendu, obtenu {type(value).__name__}")
    return value


def _require_key(mapping: dict[str, Any], key: str, what: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    return mapping[key]


def _parse_operand(raw: Any, tokens: dict[str, TokenDef]) -> Operand:
    """Un opérande : nom de token nu (str), ``{token: …}`` (TokenRef), ou condition inline."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        if any(key in raw for key in _CONDITION_KEYS):
            return _parse_condition(raw, tokens)
        # Traité comme un token-ref (avec ou sans la clé «token») — _parse_token_ref
        # lèvera ConfigError si la clé «token» est absente ou invalide.
        return _parse_token_ref(raw, tokens)
    raise ConfigError(f"opérande de type invalide : {type(raw).__name__} ({raw!r})")


def _parse_token_ref(raw: dict[str, Any], tokens: dict[str, TokenDef]) -> TokenRef:
    name = raw.get("token")
    if not isinstance(name, str):
        raise ConfigError(f"opérande {{token: …}} sans nom de token valide : {raw!r}")
    min_value = raw.get("min")
    fuzz_value = raw.get("fuzz")
    ref = TokenRef(
        name=name,
        min=None if min_value is None else float(min_value),
        fuzz=None if fuzz_value is None else float(fuzz_value),
    )
    # Override min/fuzz n'a de sens que sur un token coverage (cf. EBNF §8.3).
    if (ref.min is not None or ref.fuzz is not None) and not isinstance(
        tokens.get(name), CoverageDef
    ):
        raise ConfigError(f"override min/fuzz interdit sur le token non-coverage {name!r}")
    return ref


def _parse_condition(raw: dict[str, Any], tokens: dict[str, TokenDef]) -> Condition:
    present = [key for key in _CONDITION_KEYS if key in raw]
    if len(present) != 1:
        raise ConfigError(f"une seule condition (all/any/not) attendue, obtenu {present!r}")
    key = present[0]
    body = raw[key]
    if key == "not":
        return NotDef(operand=_parse_operand(body, tokens))
    if not isinstance(body, list):
        raise ConfigError(f"'{key}:' attend une liste d'opérandes, obtenu {type(body).__name__}")
    operands = tuple(_parse_operand(item, tokens) for item in body)
    if key == "all":
        return AllDef(operands=operands)
    return AnyDef(operands=operands)


def _require_float(mapping: dict[str, Any], key: str) -> float | None:
    """Lit une borne flottante optionnelle (``None`` si absente)."""
    value = mapping.get(key)
    return None if value is None else float(value)


def _parse_token_def(raw: Any, tokens: dict[str, TokenDef]) -> TokenDef:
    """Dispatch d'une def de token : composite (all/any/not) ou feuille (4 types).

    Lit TOUTES les clés annexes de la def (``flags`` du regex, ``min``/``fuzz`` du
    coverage, ``min``/``max`` de l'attr_between), pas seulement la clé-type.
    """
    mapping = _require_mapping(raw, "définition de token")
    if any(key in mapping for key in _CONDITION_KEYS):
        return _parse_condition(mapping, tokens)
    leaf_keys = [k for k in ("keyword", "regex", "coverage", "attr_between") if k in mapping]
    if len(leaf_keys) > 1:
        raise ConfigError(f"un token feuille a exactement une clé-type, obtenu {sorted(leaf_keys)}")
    if "keyword" in mapping:
        return KeywordDef(phrase=str(mapping["keyword"]))
    if "regex" in mapping:
        flags = mapping.get("flags", "i")
        return RegexDef(pattern=str(mapping["regex"]), flags=str(flags))
    if "coverage" in mapping:
        min_value = _require_float(mapping, "min")
        if min_value is None:
            raise ConfigError("un token coverage doit déclarer 'min'")
        fuzz_value = _require_float(mapping, "fuzz")
        return CoverageDef(
            reference=str(mapping["coverage"]),
            min=min_value,
            fuzz=0.85 if fuzz_value is None else fuzz_value,
        )
    if "attr_between" in mapping:
        attr = str(mapping["attr_between"])
        if attr not in ATTR_NAMES:
            raise ConfigError(
                f"attr_between inconnu : {attr!r} (attendu l'un de {sorted(ATTR_NAMES)})"
            )
        return AttrBetweenDef(
            attr=attr,
            min=_require_float(mapping, "min"),
            max=_require_float(mapping, "max"),
        )
    raise ConfigError(f"forme de token inconnue : clés {sorted(mapping)}")


def _parse_rule(raw: Any, tokens: dict[str, TokenDef]) -> Rule:
    mapping = _require_mapping(raw, "règle")
    name = str(mapping.get("name", ""))
    if not name:
        raise ConfigError(f"règle sans 'name' : {raw!r}")
    tier = mapping.get("tier")
    if tier not in TIERS:
        raise ConfigError(
            f"tier inconnu pour la règle {name!r} : {tier!r} (attendu {sorted(TIERS)})"
        )
    present = [key for key in _CONDITION_KEYS if key in mapping]
    if not present:
        raise ConfigError(f"règle {name!r} sans condition (all/any/not)")
    if len(present) != 1:
        raise ConfigError(f"règle {name!r} : une seule condition attendue, obtenu {present!r}")
    return Rule(name=name, tier=str(tier), condition=_parse_condition(mapping, tokens))


def parse_matcher_config(raw: dict[str, Any]) -> MatcherConfig:
    """Construit un :class:`MatcherConfig` validé (schéma) depuis un dict YAML parsé."""
    tokens_raw = _require_mapping(raw.get("tokens", {}), "section 'tokens'")
    tokens: dict[str, TokenDef] = {}
    for token_name, token_raw in tokens_raw.items():
        tokens[str(token_name)] = _parse_token_def(token_raw, tokens)
    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ConfigError(f"section 'rules' : liste attendue, obtenu {type(rules_raw).__name__}")
    rules = tuple(_parse_rule(rule_raw, tokens) for rule_raw in rules_raw)
    return MatcherConfig(tokens=tokens, rules=rules)


def parse_targets(raw: dict[str, Any]) -> tuple[TargetSegment, ...]:
    """Construit les :class:`TargetSegment` depuis ``targets.yaml`` parsé (cf. spec §7)."""
    episodes = raw.get("episodes")
    if not isinstance(episodes, list):
        raise ConfigError("section 'episodes' : liste attendue")
    segments: list[TargetSegment] = []
    for episode in episodes:
        ep = _require_mapping(episode, "épisode")
        season = int(_require_key(ep, "season", "épisode"))
        number = int(_require_key(ep, "number", "épisode"))
        broadcast = ep.get("broadcast_date")
        broadcast_date = broadcast if isinstance(broadcast, datetime.date) else None
        status = str(ep.get("status", "lost"))
        for seg in ep.get("segments", []):
            seg_map = _require_mapping(seg, "segment")
            aliases = tuple(str(alias) for alias in seg_map.get("aliases", ()))
            segments.append(
                TargetSegment(
                    season=season,
                    number=number,
                    segment=str(_require_key(seg_map, "letter", "segment")),
                    title=str(_require_key(seg_map, "title", "segment")),
                    broadcast_date=broadcast_date,
                    status=status,
                    aliases=aliases,
                )
            )
    return tuple(segments)
