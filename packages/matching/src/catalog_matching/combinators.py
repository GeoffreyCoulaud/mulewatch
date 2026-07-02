"""``Matcher`` contract (Protocol) and ``all``/``any``/``not`` combinators (cf. spec §8.3).

Pure domain: these combinators wrap other ``Matcher``s (Plan 2a leaves or combinators) and
expose the same ``matches(candidate) -> bool`` interface.
"""

from typing import Protocol

from catalog_matching.models import FileCandidate


class Matcher(Protocol):
    """Structural contract shared by leaves (Plan 2a) and combinators.

    The 4 leaf matchers (`KeywordMatcher`, `RegexMatcher`, `CoverageMatcher`,
    `AttrBetweenMatcher`) already satisfy it unchanged (they have `matches`).
    `CoverageMatcher.value()` is NOT part of the contract: Plan 2c will access it
    specifically for explainability.
    """

    def matches(self, candidate: FileCandidate) -> bool: ...


class AllMatcher:
    """Conjunction: true if ALL children match (``all([]) == True``)."""

    def __init__(self, children: tuple[Matcher, ...]) -> None:
        self._children = children

    def matches(self, candidate: FileCandidate) -> bool:
        return all(child.matches(candidate) for child in self._children)


class AnyMatcher:
    """Disjunction: true if AT LEAST one child matches (``any([]) == False``)."""

    def __init__(self, children: tuple[Matcher, ...]) -> None:
        self._children = children

    def matches(self, candidate: FileCandidate) -> bool:
        return any(child.matches(candidate) for child in self._children)


class NotMatcher:
    """Negation: true if the single child does NOT match."""

    def __init__(self, child: Matcher) -> None:
        self._child = child

    def matches(self, candidate: FileCandidate) -> bool:
        return not self._child.matches(candidate)
