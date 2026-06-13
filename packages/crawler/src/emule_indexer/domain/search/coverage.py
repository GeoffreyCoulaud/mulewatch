"""Couverture EFFECTIVE du réseau, dérivée des statuts (PUR, spec orchestration §7 ; MVP §13).

Domaine PUR : reçoit des faits BOOLÉENS déjà observés (« telle instance peut-elle faire
aboutir une recherche ? ») et rend un signal agrégé. « Le process vit » ≠ « on peut
trouver maintenant » (spec MVP §13) : ``effective_coverage`` répond à la seconde question.

Le domaine NE connaît PAS ``NetworkStatus`` (qui vit dans ``ports`` — règle de dépendance
``ports ← application → domain`` : le domaine n'importe jamais un port). C'est
l'APPLICATION (``run_search_cycle``) qui traduit chaque ``NetworkStatus`` en booléen
« search-capable » (HighID eD2k OU Kad CONNECTED) avant d'appeler cette fonction pure.
"""

from collections.abc import Sequence
from enum import StrEnum


class Coverage(StrEnum):
    """Signal agrégé de couverture (spec MVP §13). Enum fermé."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLIND = "blind"


def effective_coverage(search_capable: Sequence[bool]) -> Coverage:
    """Agrège la capacité de recherche par instance en un signal (spec MVP §13).

    Aucune instance (liste vide) OU aucune capable → ``BLIND`` (on ne peut rien trouver,
    loggé fort par l'appelant, spec §7). Toutes capables → ``HEALTHY``. Mélange →
    ``DEGRADED`` (certaines instances aveugles). ``any(())`` vaut ``False`` → la liste
    vide tombe bien sur ``BLIND``.
    """
    if not any(search_capable):
        return Coverage.BLIND
    if all(search_capable):
        return Coverage.HEALTHY
    return Coverage.DEGRADED
