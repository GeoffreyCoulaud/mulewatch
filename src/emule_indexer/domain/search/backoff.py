"""Backoff exponentiel plafonné, math PURE (spec orchestration §3/§4 ; spec MVP §6/§14).

Domaine PUR : aucune I/O, aucun ``random`` global, aucune horloge. ``backoff_delay``
calcule le délai NOMINAL (exponentiel borné par ``cap``) ; le JITTER est appliqué par
l'appelant (il a besoin du port ``Rng``/d'un tirage) — séparer le calcul déterministe du
tirage garde ce module trivialement testable et le jitter rejouable. Utilisé par
``application/search_worker.py`` pour le backoff PAR (instance, canal) (spec §3).
"""


def backoff_delay(attempt: int, *, base: float, cap: float, factor: float) -> float:
    """Délai de backoff pour la ``attempt``-ième tentative consécutive en échec (≥ 1).

    ``attempt = 1`` → ``base`` ; chaque échec supplémentaire multiplie par ``factor`` ;
    le résultat est plafonné à ``cap`` (spec MVP §6 : « backoff exponentiel »). Un
    ``attempt`` à 0 ou négatif est traité comme la première tentative (``base``) — un
    appelant ne doit jamais demander un délai pour « zéro échec », mais on ne crashe pas
    sur une entrée hors-borne (résilience, spec §14).
    """
    if attempt <= 1:
        return min(base, cap)
    return min(base * factor ** (attempt - 1), cap)
