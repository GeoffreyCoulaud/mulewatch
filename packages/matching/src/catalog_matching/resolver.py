"""Construction par cible des arbres de matchers (cf. spec §8.5, partie construction).

Domaine PUR. À partir d'une :class:`MatcherConfig` VALIDÉE (DAG/profondeur/RE2 garantis
par ``validation.validate_config``) et d'une :class:`TargetSegment`, bâtit l'arbre de
:class:`Matcher` de chaque token nommé et de chaque règle. Les regex sont interpolées et
compilées PAR CIBLE ; les coverage liés à ``target.title`` (avec overrides au point
d'usage) ; keyword/attr_between sont statiques.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import assert_never

from catalog_matching.combinators import (
    AllMatcher,
    AnyMatcher,
    Matcher,
    NotMatcher,
)
from catalog_matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
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
from catalog_matching.interpolation import interpolate
from catalog_matching.matchers import (
    AttrBetweenMatcher,
    CoverageMatcher,
    KeywordMatcher,
    RegexMatcher,
)
from catalog_matching.models import TargetSegment

# Mot-clé de config désignant le titre de la cible comme référence de coverage (§8.5).
_TITLE_KEYWORD = "title"


@dataclass(frozen=True)
class ResolvedTarget:
    """Arbres de matchers construits pour une cible : tokens nommés + règles, par nom."""

    target: TargetSegment
    tokens: Mapping[str, Matcher]
    rules: Mapping[str, Matcher]


class MatcherResolver:
    """Construit les arbres de :class:`Matcher` d'une config validée, par cible."""

    def __init__(self, config: MatcherConfig) -> None:
        self.config = config

    def resolve_token(
        self,
        name: str,
        target: TargetSegment,
        min_override: float | None = None,
        fuzz_override: float | None = None,
    ) -> Matcher:
        """Construit le matcher du token ``name`` pour ``target`` (overrides coverage)."""
        return self._build_def(self.config.tokens[name], target, min_override, fuzz_override)

    def resolve_rule(self, rule: Rule, target: TargetSegment) -> Matcher:
        """Construit le matcher de la condition d'une règle pour ``target``."""
        return self._build_def(rule.condition, target, None, None)

    def resolve_all(self, target: TargetSegment) -> ResolvedTarget:
        """Construit tous les arbres (tokens + règles) pour ``target``."""
        tokens = {name: self.resolve_token(name, target) for name in self.config.tokens}
        rules = {rule.name: self.resolve_rule(rule, target) for rule in self.config.rules}
        return ResolvedTarget(target=target, tokens=tokens, rules=rules)

    def _build_operand(
        self,
        operand: Operand,
        target: TargetSegment,
    ) -> Matcher:
        if isinstance(operand, str):
            return self.resolve_token(operand, target)
        if isinstance(operand, TokenRef):
            return self.resolve_token(operand.name, target, operand.min, operand.fuzz)
        return self._build_def(operand, target, None, None)

    def _build_def(
        self,
        token_def: TokenDef,
        target: TargetSegment,
        min_override: float | None,
        fuzz_override: float | None,
    ) -> Matcher:
        match token_def:
            case KeywordDef(phrase=phrase):
                return KeywordMatcher(phrase)
            case RegexDef(pattern=pattern, flags=flags):
                return RegexMatcher(interpolate(pattern, target), flags=flags)
            case CoverageDef(reference=reference, min=min_value, fuzz=fuzz_value):
                text = target.title if reference == _TITLE_KEYWORD else reference
                return CoverageMatcher(
                    reference=text,
                    min=min_value if min_override is None else min_override,
                    fuzz=fuzz_value if fuzz_override is None else fuzz_override,
                )
            case AttrBetweenDef(attr=attr, min=min_value, max=max_value):
                return AttrBetweenMatcher(attr, min=min_value, max=max_value)
            case AllDef(operands=operands):
                return AllMatcher(tuple(self._build_operand(op, target) for op in operands))
            case AnyDef(operands=operands):
                return AnyMatcher(tuple(self._build_operand(op, target) for op in operands))
            case NotDef(operand=operand):
                return NotMatcher(self._build_operand(operand, target))
            case _:  # pragma: no cover - exhaustif (mypy le prouve via assert_never)
                assert_never(token_def)
