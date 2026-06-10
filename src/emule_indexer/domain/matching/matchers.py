"""Matchers feuilles du moteur de matching (cf. spec §8.2)."""

from emule_indexer.domain.matching.models import FileCandidate
from emule_indexer.domain.normalization import tokenize


class KeywordMatcher:
    """Vrai si la phrase (tokenisée) est une sous-suite CONTIGUË des tokens du nom."""

    def __init__(self, phrase: str) -> None:
        self._tokens = tokenize(phrase)

    def matches(self, candidate: FileCandidate) -> bool:
        needle = self._tokens
        haystack = tokenize(candidate.filename)
        if not needle:
            return True
        # Fenêtre glissante de largeur len(needle) : sous-suite CONTIGUË.
        last_start = len(haystack) - len(needle)
        return any(
            haystack[start : start + len(needle)] == needle for start in range(last_start + 1)
        )
