"""Validation au chargement (fail-fast) : schéma YAML parsé -> modèle de config.

Domaine PUR : reçoit des structures déjà parsées (``dict``/``list``), n'importe pas
``yaml``, ne touche pas le disque. Couvre la spec §8.4 (validation au chargement) côté
schéma + validations locales (tier fermé, enum ``attr_between``, override coverage-only).
La validation de graphe (DAG/profondeur) et le compile-check RE2 sont ajoutés en Task 6.
"""

import datetime
from typing import Any

import re2

from catalog_matching.config import (
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
from catalog_matching.interpolation import InterpolationError, interpolate
from catalog_matching.matchers import ATTR_NAMES
from catalog_matching.models import TargetSegment

_CONDITION_KEYS = ("all", "any", "not")


class ConfigError(Exception):
    """Erreur fatale de configuration au chargement (schéma, tier, enum, override)."""


class UnknownTokenError(ConfigError):
    """Une référence pointe vers un token absent de la table."""


class CycleError(ConfigError):
    """Le graphe de références token->token contient un cycle (le message le nomme)."""


class DepthExceededError(ConfigError):
    """La profondeur de résolution dépasse la borne (défaut 32)."""


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} : mapping attendu, obtenu {type(value).__name__}")
    return value


def _require_key(mapping: dict[str, Any], key: str, what: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    return mapping[key]


def _parse_operand(raw: Any) -> Operand:
    """Un opérande : nom de token nu (str), ``{token: …}`` (TokenRef), ou condition inline."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        if any(key in raw for key in _CONDITION_KEYS):
            return _parse_condition(raw)
        # Traité comme un token-ref (avec ou sans la clé «token») — _parse_token_ref
        # lèvera ConfigError si la clé «token» est absente ou invalide.
        return _parse_token_ref(raw)
    raise ConfigError(f"opérande de type invalide : {type(raw).__name__} ({raw!r})")


def _require_unit_fraction(value: float, what: str) -> float:
    """Valide qu'une fraction logique (seuil coverage/fuzz) est dans [0, 1] (fail-fast §8.4).

    Hors de [0, 1], la règle est silencieusement inerte (seuil jamais atteint) : c'est une
    erreur de config (typiquement un pourcentage saisi pour une fraction), rejetée au chargement.
    """
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{what} doit être dans [0, 1], obtenu {value}")
    return value


def _parse_token_ref(raw: dict[str, Any]) -> TokenRef:
    name = raw.get("token")
    if not isinstance(name, str):
        raise ConfigError(f"opérande {{token: …}} sans nom de token valide : {raw!r}")
    min_value = raw.get("min")
    fuzz_value = raw.get("fuzz")
    # La licéité d'un override min/fuzz (coverage-only, cf. EBNF §8.3) est vérifiée au
    # niveau du graphe (validate_config), pas ici : le parsing reste purement structurel
    # et indépendant de l'ordre de définition des tokens (réf. en avant autorisée). Les BORNES
    # [0, 1] sont en revanche structurelles → validées ici.
    return TokenRef(
        name=name,
        min=(
            None
            if min_value is None
            else _require_unit_fraction(float(min_value), f"override 'min' de {name!r}")
        ),
        fuzz=(
            None
            if fuzz_value is None
            else _require_unit_fraction(float(fuzz_value), f"override 'fuzz' de {name!r}")
        ),
    )


def _parse_condition(raw: dict[str, Any]) -> Condition:
    present = [key for key in _CONDITION_KEYS if key in raw]
    if len(present) != 1:
        raise ConfigError(f"une seule condition (all/any/not) attendue, obtenu {present!r}")
    key = present[0]
    body = raw[key]
    if key == "not":
        return NotDef(operand=_parse_operand(body))
    if not isinstance(body, list):
        raise ConfigError(f"'{key}:' attend une liste d'opérandes, obtenu {type(body).__name__}")
    if not body:
        # EBNF §8.3 : operand (',' operand)* = au moins un opérande. Une liste vide donnerait
        # AllMatcher([]).matches()==all([])==True (matche TOUT) ou AnyMatcher([])==False (muet) :
        # config dégénérée, rejetée au chargement (fail-fast §8.4). all([])/any([]) reste la
        # sémantique interne légitime des combinateurs — c'est la CONFIG vide qu'on interdit.
        raise ConfigError(f"'{key}:' exige au moins un opérande (liste vide reçue)")
    operands = tuple(_parse_operand(item) for item in body)
    if key == "all":
        return AllDef(operands=operands)
    return AnyDef(operands=operands)


def _require_float(mapping: dict[str, Any], key: str) -> float | None:
    """Lit une borne flottante optionnelle (``None`` si absente)."""
    value = mapping.get(key)
    return None if value is None else float(value)


def _parse_token_def(raw: Any) -> TokenDef:
    """Dispatch d'une def de token : composite (all/any/not) ou feuille (4 types).

    Lit TOUTES les clés annexes de la def (``flags`` du regex, ``min``/``fuzz`` du
    coverage, ``min``/``max`` de l'attr_between), pas seulement la clé-type.
    """
    mapping = _require_mapping(raw, "définition de token")
    if any(key in mapping for key in _CONDITION_KEYS):
        return _parse_condition(mapping)
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
        fuzz = 0.85 if fuzz_value is None else _require_unit_fraction(fuzz_value, "coverage 'fuzz'")
        return CoverageDef(
            reference=str(mapping["coverage"]),
            min=_require_unit_fraction(min_value, "coverage 'min'"),
            fuzz=fuzz,
        )
    if "attr_between" in mapping:
        attr = str(mapping["attr_between"])
        if attr not in ATTR_NAMES:
            raise ConfigError(
                f"attr_between inconnu : {attr!r} (attendu l'un de {sorted(ATTR_NAMES)})"
            )
        min_bound = _require_float(mapping, "min")
        max_bound = _require_float(mapping, "max")
        # Bornes ouvertes (min seul / max seul / aucune) légitimes ; seule une plage explicite
        # inversée min > max est une plage VIDE (règle muette pour toujours) → fail-fast §8.4.
        if min_bound is not None and max_bound is not None and min_bound > max_bound:
            raise ConfigError(
                f"attr_between {attr!r} : min ({min_bound}) > max ({max_bound}) — plage vide"
            )
        return AttrBetweenDef(attr=attr, min=min_bound, max=max_bound)
    raise ConfigError(f"forme de token inconnue : clés {sorted(mapping)}")


def _parse_rule(raw: Any) -> Rule:
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
    return Rule(name=name, tier=str(tier), condition=_parse_condition(mapping))


def parse_matcher_config(raw: dict[str, Any]) -> MatcherConfig:
    """Construit un :class:`MatcherConfig` validé (schéma) depuis un dict YAML parsé."""
    tokens_raw = _require_mapping(raw.get("tokens", {}), "section 'tokens'")
    tokens: dict[str, TokenDef] = {}
    for token_name, token_raw in tokens_raw.items():
        tokens[str(token_name)] = _parse_token_def(token_raw)
    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ConfigError(f"section 'rules' : liste attendue, obtenu {type(rules_raw).__name__}")
    rules = tuple(_parse_rule(rule_raw) for rule_raw in rules_raw)
    config = MatcherConfig(tokens=tokens, rules=rules)
    validate_config(config)
    return config


_DEFAULT_MAX_DEPTH = 32

# Cible-sonde pour le compile-check : fournit number/segment/title/date_alt afin que
# l'interpolation de toute RegexDef soit testable au chargement (cf. spec §8.4/§8.5).
_PROBE_TARGET = TargetSegment(
    season=2,
    number=62,
    segment="a",
    title="sonde",
    broadcast_date=datetime.date(2008, 9, 21),
)


def _operand_refs(operand: Operand) -> tuple[str, ...]:
    """Noms de tokens directement référencés par un opérande (str, TokenRef ou inline)."""
    if isinstance(operand, str):
        return (operand,)
    if isinstance(operand, TokenRef):
        return (operand.name,)
    if isinstance(operand, NotDef):
        return _operand_refs(operand.operand)
    # AllDef | AnyDef
    refs: list[str] = []
    for child in operand.operands:
        refs.extend(_operand_refs(child))
    return tuple(refs)


def _def_refs(token_def: TokenDef) -> tuple[str, ...]:
    """Noms de tokens directement référencés par une def (vide pour une feuille)."""
    if isinstance(token_def, AllDef | AnyDef):
        refs: list[str] = []
        for child in token_def.operands:
            refs.extend(_operand_refs(child))
        return tuple(refs)
    if isinstance(token_def, NotDef):
        return _operand_refs(token_def.operand)
    return ()


def _operand_token_refs(operand: Operand) -> tuple[TokenRef, ...]:
    """Tous les TokenRef (avec leurs overrides) atteignables depuis un opérande."""
    if isinstance(operand, TokenRef):
        return (operand,)
    if isinstance(operand, NotDef):
        return _operand_token_refs(operand.operand)
    if isinstance(operand, AllDef | AnyDef):
        refs: list[TokenRef] = []
        for child in operand.operands:
            refs.extend(_operand_token_refs(child))
        return tuple(refs)
    return ()  # str (nom nu) -> aucun TokenRef


def _def_token_refs(token_def: TokenDef) -> tuple[TokenRef, ...]:
    """Tous les TokenRef d'une def composite (vide pour une feuille)."""
    if isinstance(token_def, AllDef | AnyDef):
        refs: list[TokenRef] = []
        for child in token_def.operands:
            refs.extend(_operand_token_refs(child))
        return tuple(refs)
    if isinstance(token_def, NotDef):
        return _operand_token_refs(token_def.operand)
    return ()


def _check_overrides_target_coverage(config: MatcherConfig) -> None:
    """Un override min/fuzz n'est licite que sur un token coverage (cf. EBNF §8.3).

    Vérifié ICI (pas au parsing) pour ne pas dépendre de l'ordre de définition :
    une référence en avant vers un token coverage doit être acceptée.
    """
    refs: list[TokenRef] = []
    for token_def in config.tokens.values():
        refs.extend(_def_token_refs(token_def))
    for rule in config.rules:
        refs.extend(_operand_token_refs(rule.condition))
    for ref in refs:
        if (ref.min is not None or ref.fuzz is not None) and not isinstance(
            config.tokens.get(ref.name), CoverageDef
        ):
            raise ConfigError(f"override min/fuzz interdit sur le token non-coverage {ref.name!r}")


def _check_references_exist(config: MatcherConfig) -> None:
    """Toute référence (dans un token composite OU une règle) doit exister."""
    known = set(config.tokens)
    for token_def in config.tokens.values():
        for ref in _def_refs(token_def):
            if ref not in known:
                raise UnknownTokenError(f"référence vers un token inconnu : {ref!r}")
    for rule in config.rules:
        for ref in _operand_refs(rule.condition):
            if ref not in known:
                raise UnknownTokenError(
                    f"règle {rule.name!r} : référence vers un token inconnu : {ref!r}"
                )


def _check_acyclic(config: MatcherConfig, max_depth: int) -> None:
    """Détecte un cycle dans le graphe token->token et le NOMME (cf. spec §8.4).

    Le garde-fou ``len(stack) >= max_depth`` borne aussi la récursion : un chemin
    plus profond que ``max_depth`` est déjà une violation de profondeur, levée
    proprement ici (évite un ``RecursionError`` Python sur une chaîne pathologique).
    """
    graph = {name: _def_refs(token_def) for name, token_def in config.tokens.items()}
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []

    def walk(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = stack[stack.index(name) :] + [name]
            raise CycleError(f"cycle de références : {' -> '.join(cycle)}")
        if len(stack) >= max_depth:
            tail = " -> ".join([*stack[-3:], name])
            raise DepthExceededError(
                f"profondeur de résolution > {max_depth} (chaîne : … -> {tail})"
            )
        visiting.add(name)
        stack.append(name)
        for ref in graph.get(name, ()):  # ref existe (vérifié par _check_references_exist)
            walk(ref)
        stack.pop()
        visiting.discard(name)
        done.add(name)

    for token_name in graph:
        walk(token_name)


def _max_resolution_depth(config: MatcherConfig) -> int:
    """Profondeur maximale d'un token (feuille = 1). Suppose le graphe acyclique."""
    graph = {name: _def_refs(token_def) for name, token_def in config.tokens.items()}
    memo: dict[str, int] = {}

    def depth(name: str) -> int:
        if name in memo:
            return memo[name]
        refs = graph.get(name, ())
        result = 1 if not refs else 1 + max(depth(ref) for ref in refs)
        memo[name] = result
        return result

    return max((depth(name) for name in graph), default=0)


def _check_regexes_compile(config: MatcherConfig) -> None:
    """Chaque RegexDef s'interpole (placeholders connus) et compile sous RE2 (cf. §8.4)."""
    for name, token_def in config.tokens.items():
        if not isinstance(token_def, RegexDef):
            continue
        try:
            pattern = interpolate(token_def.pattern, _PROBE_TARGET)
        except InterpolationError as exc:
            raise ConfigError(f"token {name!r} : interpolation invalide : {exc}") from exc
        if "i" in token_def.flags:
            pattern = "(?i)" + pattern
        try:
            re2.compile(pattern)
        except re2.error as exc:
            raise ConfigError(f"token {name!r} : regex non compilable sous RE2 : {exc}") from exc


def validate_config(config: MatcherConfig, *, max_depth: int = _DEFAULT_MAX_DEPTH) -> None:
    """Valide le graphe (références, DAG, profondeur) et les regex (cf. spec §8.4).

    Lève :class:`UnknownTokenError`, :class:`CycleError`, :class:`DepthExceededError`
    ou :class:`ConfigError` (regex/interpolation). À appeler après le parsing schéma.
    """
    _check_references_exist(config)
    _check_overrides_target_coverage(config)
    _check_acyclic(config, max_depth)
    depth = _max_resolution_depth(config)
    if depth > max_depth:
        raise DepthExceededError(
            f"profondeur de résolution {depth} > max {max_depth} (défaut {_DEFAULT_MAX_DEPTH})"
        )
    _check_regexes_compile(config)


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
    result = tuple(segments)
    seen: set[str] = set()
    for segment in result:
        if segment.target_id in seen:
            raise ConfigError(
                f"target_id en double : {segment.target_id!r} — les segments cibles doivent "
                f"être uniques (note : la lettre de segment est mise en majuscule par target_id, "
                f"donc 'a' et 'A' collisionnent). Le moteur d'évaluation en dépend (départage "
                f"déterministe)."
            )
        seen.add(segment.target_id)
    return result
