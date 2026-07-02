"""Leaf matchers of the matching engine (cf. spec §8.2)."""

import re2
from rapidfuzz import fuzz

from catalog_matching.models import FileCandidate
from catalog_matching.normalization import fold, tokenize


class KeywordMatcher:
    """True if the (tokenized) phrase is a CONTIGUOUS subsequence of the filename tokens."""

    def __init__(self, phrase: str) -> None:
        self._tokens = tokenize(phrase)

    def matches(self, candidate: FileCandidate) -> bool:
        needle = self._tokens
        haystack = tokenize(candidate.filename)
        if not needle:
            return True
        # Sliding window of width len(needle): CONTIGUOUS subsequence.
        last_start = len(haystack) - len(needle)
        return any(
            haystack[start : start + len(needle)] == needle for start in range(last_start + 1)
        )


class RegexMatcher:
    """RE2 match on ``fold(filename)``. If ``"i"`` in ``flags``, prefixes ``(?i)``.

    We explicitly prefix ``(?i)`` to the pattern rather than relying on RE2 flag
    constants (portability of the ``re2`` API).

    ``flags`` is a short ``re``-style string: ``"i"`` enables case-insensitivity,
    ``""`` leaves it case-sensitive. Expected values from the YAML config (Plan 2b):
    ``"i"`` or ``""``. Detection is ``"i" in flags`` — do not pass verbose names
    (``"ignore_case"``…), which would enable ``(?i)`` by accident. An invalid pattern
    raises ``re2.error`` at construction (config validation delegated to Plan 2b).
    """

    def __init__(self, pattern: str, flags: str = "i") -> None:
        if "i" in flags:
            pattern = "(?i)" + pattern
        self._re = re2.compile(pattern)

    def matches(self, candidate: FileCandidate) -> bool:
        return self._re.search(fold(candidate.filename)) is not None


# French stopwords (already folded by tokenize) excluded from the significant tokens
# of a CoverageMatcher's reference (cf. spec §8.2: R = tokens(title) \ stopwords).
# Deliberately minimal set: under-filtering favors recall (better to keep a borderline
# word than to drop a significant token).
STOPWORDS_FR: frozenset[str] = frozenset(
    {
        "le",
        "la",
        "les",
        "l",
        "de",
        "des",
        "du",
        "d",
        "un",
        "une",
        "et",
        "a",
        "au",
        "aux",
        "en",
    }
)


class CoverageMatcher:
    """Fuzzy fraction of ``reference``'s significant tokens that are covered (cf. spec §8.2)."""

    def __init__(self, reference: str, min: float, fuzz: float = 0.85) -> None:
        self._reference_tokens = [t for t in tokenize(reference) if t not in STOPWORDS_FR]
        self._min = min
        self._fuzz = fuzz

    def value(self, candidate: FileCandidate) -> float:
        reference_tokens = self._reference_tokens
        if not reference_tokens:
            return 0.0
        candidate_tokens = tokenize(candidate.filename)
        hits = sum(
            1
            for r in reference_tokens
            if any(fuzz.ratio(r, f) / 100 >= self._fuzz for f in candidate_tokens)
        )
        return hits / len(reference_tokens)

    def matches(self, candidate: FileCandidate) -> bool:
        return self.value(candidate) >= self._min


# Closed enum of FileCandidate numeric attributes usable by attr_between
# (cf. spec §8.2). Any other name -> error.
ATTR_NAMES: frozenset[str] = frozenset({"size_mb", "duration_sec", "bitrate_kbps"})


class AttrBetweenMatcher:
    """True if the numeric attribute is PRESENT and within ``[min, max]`` (cf. spec §8.2).

    Open bounds when ``min``/``max`` are ``None``. Missing attribute -> false.
    """

    def __init__(
        self,
        attr: str,
        min: float | None = None,
        max: float | None = None,
    ) -> None:
        if attr not in ATTR_NAMES:
            raise ValueError(f"unknown attribute: {attr!r} (expected one of {sorted(ATTR_NAMES)})")
        self._attr = attr
        self._min = min
        self._max = max

    def matches(self, candidate: FileCandidate) -> bool:
        value: float | None = getattr(candidate, self._attr)
        return (
            value is not None
            and (self._min is None or value >= self._min)
            and (self._max is None or value <= self._max)
        )
