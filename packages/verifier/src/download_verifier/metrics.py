"""Métriques techniques du verifier (E-D10). Pas d'événements/notifications (crawler-only) :
un simple compteur de requêtes ``/verify`` par verdict + un histogramme de durée d'analyse, sur
un ``CollectorRegistry`` DÉDIÉ (exposé tel quel par ``/metrics``). Counter SANS ``_total`` (ajouté
par prometheus_client à l'exposition)."""

from prometheus_client import CollectorRegistry, Counter, Histogram


class VerifierMetrics:
    """Registre + compteur ``/verify`` par verdict + histogramme de durée."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._requests = Counter(
            "emule_verifier_requests",
            "Requêtes /verify traitées",
            ["verdict"],
            registry=self.registry,
        )
        self._duration = Histogram(
            "emule_verifier_analysis_duration_seconds",
            "Durée d'analyse d'un fichier (s)",
            registry=self.registry,
        )

    def observe(self, verdict: str, seconds: float) -> None:
        """Compte une requête (par verdict) et observe sa durée d'analyse."""
        self._requests.labels(verdict=verdict).inc()
        self._duration.observe(seconds)
