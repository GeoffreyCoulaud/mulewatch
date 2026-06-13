"""Normalisation des chaînes pour le matching (cf. spec §8.1)."""

import unicodedata

# Ligatures françaises (œ, æ) non décomposées par NFKD et non repliées en ASCII
# par casefold. Table volontairement limitée au corpus FR/DE ciblé (§8.1) — ce
# n'est PAS la liste exhaustive des lettres Unicode dans ce cas (p. ex. ĳ, ĸ
# survivent aussi).
_LIGATURES = {"œ": "oe", "æ": "ae"}


def _common_fold(value: str) -> str:
    """Repli commun : NFKD -> retrait des diacritiques combinants -> casefold -> ligatures."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    for ligature, replacement in _LIGATURES.items():
        folded = folded.replace(ligature, replacement)
    return folded


def fold(value: str) -> str:
    """Repli commun seul : ponctuation et chiffres PRÉSERVÉS (cf. spec §8.1).

    Utilisé par les tokens ``regex`` (ainsi ``teletoon``/``fevrier`` matchent sans
    classes d'accents, et ``°`` reste pour ``n°062a``).
    """
    return _common_fold(value)


def normalize(value: str) -> str:
    """Replie une chaîne pour le matching keyword/coverage (cf. spec §8.1).

    ``fold`` -> non-alphanumériques convertis en espaces -> espaces compactés -> trim.
    """
    folded = fold(value)
    cleaned = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(cleaned.split())


def tokenize(value: str) -> list[str]:
    """Tokens significatifs d'une chaîne, après normalisation."""
    return normalize(value).split()
