"""Contrat ``Matcher`` (Protocol) et combinateurs ``all``/``any``/``not`` (cf. spec §8.3).

Domaine pur : ces combinateurs wrappent d'autres ``Matcher`` (feuilles du Plan 2a ou
combinateurs) et exposent la même interface ``matches(candidate) -> bool``.
"""

from typing import Protocol

from emule_indexer.domain.matching.models import FileCandidate


class Matcher(Protocol):
    """Contrat structural commun aux feuilles (Plan 2a) et aux combinateurs.

    Les 4 matchers feuilles (`KeywordMatcher`, `RegexMatcher`, `CoverageMatcher`,
    `AttrBetweenMatcher`) le satisfont déjà sans modification (ils ont `matches`).
    `CoverageMatcher.value()` n'entre PAS dans le contrat : le Plan 2c y accédera
    spécifiquement pour l'explicabilité.
    """

    def matches(self, candidate: FileCandidate) -> bool: ...


class AllMatcher:
    """Conjonction : vrai si TOUS les enfants matchent (``all([]) == True``)."""

    def __init__(self, children: tuple[Matcher, ...]) -> None:
        self._children = children

    def matches(self, candidate: FileCandidate) -> bool:
        return all(child.matches(candidate) for child in self._children)


class AnyMatcher:
    """Disjonction : vrai si AU MOINS un enfant matche (``any([]) == False``)."""

    def __init__(self, children: tuple[Matcher, ...]) -> None:
        self._children = children

    def matches(self, candidate: FileCandidate) -> bool:
        return any(child.matches(candidate) for child in self._children)


class NotMatcher:
    """Négation : vrai si l'enfant unique NE matche PAS."""

    def __init__(self, child: Matcher) -> None:
        self._child = child

    def matches(self, candidate: FileCandidate) -> bool:
        return not self._child.matches(candidate)
