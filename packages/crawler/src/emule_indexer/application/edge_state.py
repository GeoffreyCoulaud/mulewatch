"""État edge-trigger : calcule ``first_occurrence`` pour l'anti-spam des notifications (E-D8).

Couche APPLICATION (état mutable inter-itérations, comme ``BackoffRegistry``). Détenu par
``CrawlerApp``, consulté par les boucles (E.2) : ``enter(condition)`` rend ``True`` SEULEMENT à
la transition vers actif (première occurrence d'une panne) ; ``leave(condition)`` réarme au
rétablissement. La métrique, elle, s'incrémente à CHAQUE occurrence (Prometheus veut l'état
brut) — l'edge-trigger ne gouverne que la notification. Mono-thread sur l'event loop → aucun
verrou. L'état est in-process (non persisté) : après un redémarrage, une panne en cours
re-notifie une fois (acceptable, E-D8)."""


class EdgeState:
    """Ensemble des conditions actuellement actives (en alerte)."""

    def __init__(self) -> None:
        self._active: set[str] = set()

    def enter(self, condition: str) -> bool:
        """Marque ``condition`` active. Rend ``True`` ssi c'est une TRANSITION (1re occurrence)."""
        if condition in self._active:
            return False
        self._active.add(condition)
        return True

    def leave(self, condition: str) -> bool:
        """Marque ``condition`` inactive. Rend ``True`` ssi elle était active (réarmement)."""
        if condition not in self._active:
            return False
        self._active.discard(condition)
        return True
