"""Matchers feuilles du moteur de matching (cf. spec §8.2)."""

import re2
from rapidfuzz import fuzz

from emule_indexer.domain.matching.models import FileCandidate
from emule_indexer.domain.normalization import fold, tokenize


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


class RegexMatcher:
    """Match RE2 sur ``fold(filename)``. Si ``"i"`` dans ``flags``, préfixe ``(?i)``.

    On préfixe explicitement ``(?i)`` au pattern plutôt que de s'appuyer sur des
    constantes de flag RE2 (portabilité de l'API ``re2``).

    ``flags`` est une chaîne courte façon ``re`` : ``"i"`` active l'insensibilité
    à la casse, ``""`` la laisse sensible. Valeurs attendues depuis la config
    YAML (Plan 2b) : ``"i"`` ou ``""``. La détection est ``"i" in flags`` — ne
    pas passer de noms verbeux (``"ignore_case"``…), qui activeraient ``(?i)``
    par accident. Un pattern invalide lève ``re2.error`` à la construction
    (validation de config déléguée au Plan 2b).
    """

    def __init__(self, pattern: str, flags: str = "i") -> None:
        if "i" in flags:
            pattern = "(?i)" + pattern
        self._re = re2.compile(pattern)

    def matches(self, candidate: FileCandidate) -> bool:
        return self._re.search(fold(candidate.filename)) is not None


# Mots-vides français (déjà repliés par tokenize) exclus des tokens significatifs
# de la référence d'un CoverageMatcher (cf. spec §8.2 : R = tokens(title) \ stopwords).
# Ensemble volontairement minimal : sous-filtrer favorise le rappel (mieux vaut
# garder un mot limite que retirer un token significatif).
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
    """Fraction fuzzy des tokens significatifs de ``reference`` couverts (cf. spec §8.2)."""

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


# Enum fermé des attributs numériques de FileCandidate utilisables par attr_between
# (cf. spec §8.2). Tout autre nom -> erreur.
ATTR_NAMES: frozenset[str] = frozenset({"size_mb", "duration_sec", "bitrate_kbps"})


class AttrBetweenMatcher:
    """Vrai si l'attribut numérique est PRÉSENT et dans ``[min, max]`` (cf. spec §8.2).

    Bornes ouvertes quand ``min``/``max`` valent ``None``. Attribut absent -> faux.
    """

    def __init__(
        self,
        attr: str,
        min: float | None = None,
        max: float | None = None,
    ) -> None:
        if attr not in ATTR_NAMES:
            raise ValueError(f"attribut inconnu : {attr!r} (attendu l'un de {sorted(ATTR_NAMES)})")
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
