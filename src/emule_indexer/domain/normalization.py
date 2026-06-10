"""Normalisation des chaînes pour le matching (cf. spec §8.1)."""

import unicodedata


def normalize(value: str) -> str:
    """Replie une chaîne pour le matching.

    NFKD (décomposition de compatibilité) -> suppression des diacritiques
    combinants -> minuscules -> non-alphanumériques convertis en espaces ->
    espaces compactés -> trim.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_marks.lower()
    cleaned = "".join(ch if ch.isalnum() else " " for ch in lowered)
    return " ".join(cleaned.split())
