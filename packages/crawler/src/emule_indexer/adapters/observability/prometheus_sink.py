"""Sink Prometheus : applique une ``MetricInstruction`` sur un ``CollectorRegistry`` DÉDIÉ (E-D9).

Couche ADAPTER (implémente ``MetricsSink``). Catalogue déclaré sur le registre INJECTÉ (jamais
le registre global) → testable sur un registre jetable, sans état partagé. Trois maps HOMOGÈNES
(counters/gauges/histogrammes) indexées par ``MetricName`` → ``apply`` route sur ``kind`` en 3
branches. GOTCHA : les counters sont nommés SANS ``_total`` (ajouté par la lib à l'exposition)."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from emule_indexer.domain.observability.policy import MetricInstruction, MetricName

# (nom, doc, labels) des counters.
_COUNTERS: tuple[tuple[MetricName, str, tuple[str, ...]], ...] = (
    (MetricName.SEARCH_CYCLES, "Cycles de recherche terminés", ()),
    (MetricName.SEARCHES, "Recherches exécutées", ("network",)),
    (MetricName.OBSERVATIONS, "Observations enregistrées", ("network",)),
    (MetricName.SEARCH_FAILURES, "Recherches en échec", ("network",)),
    (MetricName.MULE_UNREACHABLE, "Instances injoignables", ("instance",)),
    (MetricName.SEARCH_BLIND_CYCLES, "Cycles à couverture aveugle", ()),
    (MetricName.DECISIONS, "Décisions de match enregistrées", ("tier",)),
    (MetricName.DOWNLOADS_QUEUED, "Téléchargements mis en file", ()),
    (MetricName.DOWNLOADS_COMPLETED, "Téléchargements terminés", ()),
    (MetricName.PROMOTION_FAILURES, "Mises en quarantaine échouées", ()),
    (MetricName.VERIFICATIONS, "Vérifications terminées", ("verdict",)),
    (MetricName.VERIFIER_UNAVAILABLE, "Verifier injoignable (occurrences)", ()),
    (MetricName.PORT_SYNC_TRIGGERED, "Synchronisations de port déclenchées", ()),
    (MetricName.HIGH_ID_RECOVERED, "High-ID retrouvés", ()),
    (MetricName.PORT_MISMATCH, "High-ID non rétabli (occurrences)", ()),
)
_GAUGES: tuple[tuple[MetricName, str, tuple[str, ...]], ...] = (
    (MetricName.CONNECTED_INSTANCES, "Instances search-capable", ("network",)),
    (MetricName.VERIFICATION_QUEUE_DEPTH, "Tâches de vérification en attente", ()),
    (MetricName.CRAWLER_UP, "Crawler en marche (1)", ()),
)
_HISTOGRAMS: tuple[tuple[MetricName, str], ...] = (
    (MetricName.SEARCH_CYCLE_DURATION, "Durée d'un cycle de recherche (s)"),
)


class PrometheusSink:
    """Adapter ``MetricsSink`` sur un registre dédié injecté."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self._counters = {
            name: Counter(name.value, doc, labels, registry=registry)
            for name, doc, labels in _COUNTERS
        }
        self._gauges = {
            name: Gauge(name.value, doc, labels, registry=registry) for name, doc, labels in _GAUGES
        }
        self._histograms = {
            name: Histogram(name.value, doc, registry=registry) for name, doc in _HISTOGRAMS
        }

    def apply(self, instruction: MetricInstruction) -> None:
        labels = dict(instruction.labels)
        if instruction.kind == "inc":
            counter = self._counters[instruction.name]
            (counter.labels(**labels) if labels else counter).inc(instruction.value)
        elif instruction.kind == "set":
            gauge = self._gauges[instruction.name]
            (gauge.labels(**labels) if labels else gauge).set(instruction.value)
        else:
            self._histograms[instruction.name].observe(instruction.value)
