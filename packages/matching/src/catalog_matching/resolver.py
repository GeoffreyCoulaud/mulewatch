"""Per-target construction of matcher trees (cf. spec §8.5, construction part).

PURE domain. From a VALIDATED :class:`MatcherConfig` (DAG/depth/RE2 guaranteed by
``validation.validate_config``) and a :class:`TargetSegment`, builds the :class:`Matcher`
tree of each named token and each rule. Regexes are interpolated and compiled PER TARGET;
coverages bound to ``target.title`` (with overrides at point of use); keyword/attr_between
are static.
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

# Config keyword designating the target's title as the coverage reference (§8.5).
_TITLE_KEYWORD = "title"


@dataclass(frozen=True)
class ResolvedTarget:
    """Matcher trees built for a target: named tokens + rules, by name."""

    target: TargetSegment
    tokens: Mapping[str, Matcher]
    rules: Mapping[str, Matcher]


class MatcherResolver:
    """Builds the :class:`Matcher` trees of a validated config, per target."""

    def __init__(self, config: MatcherConfig) -> None:
        self.config = config

    def resolve_token(
        self,
        name: str,
        target: TargetSegment,
        min_override: float | None = None,
        fuzz_override: float | None = None,
    ) -> Matcher:
        """Builds the matcher of token ``name`` for ``target`` (coverage overrides)."""
        return self._build_def(self.config.tokens[name], target, min_override, fuzz_override)

    def resolve_rule(self, rule: Rule, target: TargetSegment) -> Matcher:
        """Builds the matcher of a rule's condition for ``target``."""
        return self._build_def(rule.condition, target, None, None)

    def resolve_all(self, target: TargetSegment) -> ResolvedTarget:
        """Builds all the trees (tokens + rules) for ``target``."""
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
            case _:  # pragma: no cover - exhaustive (mypy proves it via assert_never)
                assert_never(token_def)
