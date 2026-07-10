"""Observability policy: ``describe(event) → Report`` (spec Plan E §3, E-D3).

DOMAIN layer (pure). The ONLY place that decides — for each event — severity, message,
metric(s), audiences. ``describe`` is an EXHAUSTIVE match (``assert_never`` → 100% branch).
The domain knows nothing of ``logging``, Prometheus or apprise: ``Severity``/``Audience``/
``MetricName`` are DOMAIN enums, translated by the adapters (E-D3).

Prometheus GOTCHA: COUNTER names do NOT include ``_total`` here — ``prometheus_client``
adds it at exposition (including it would produce ``…_total_total``). Gauges/histogram: name
as-is.
"""

from dataclasses import dataclass
from enum import Enum, StrEnum, auto
from typing import Literal, assert_never

from mulewatch.domain.observability.events import (
    AllInstancesBlind,
    ConnectedInstancesSampled,
    CrawlerStarted,
    DecisionRecorded,
    DownloadCompleted,
    DownloadQueued,
    Event,
    HighIdRecovered,
    InstanceUnreachable,
    ObservationRecorded,
    PortMismatchUnresolved,
    PortSyncTriggered,
    PromotionFailed,
    SearchCapabilitySampled,
    SearchCycleCompleted,
    SearchExecuted,
    SearchFailed,
    SearchTaskDropped,
    VerificationCompleted,
    VerificationQueueDepthSampled,
    VerifierUnavailable,
)


class Severity(Enum):
    """DOMAIN severity of a fact (translated to a ``logging`` level by the adapter)."""

    DEBUG = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()


class Audience(Enum):
    """Consumer of a notification (E-D7) — the VALUE is the apprise tag."""

    COMMUNITY = "community"
    OPERATIONS = "operations"


class MetricName(StrEnum):
    """Metric names. Counters WITHOUT ``_total`` (added by prometheus_client at exposition)."""

    SEARCH_CYCLES = "emule_search_cycles"
    SEARCH_CYCLE_DURATION = "emule_search_cycle_duration_seconds"
    SEARCHES = "emule_searches"
    OBSERVATIONS = "emule_observations"
    SEARCH_FAILURES = "emule_search_failures"
    SEARCH_TASKS_DROPPED = "emule_search_tasks_dropped"
    MULE_UNREACHABLE = "emule_mule_unreachable"
    SEARCH_BLIND_CYCLES = "emule_search_blind_cycles"
    SEARCH_CAPABLE = "emule_search_capable"
    DECISIONS = "emule_decisions"
    DOWNLOADS_QUEUED = "emule_downloads_queued"
    DOWNLOADS_COMPLETED = "emule_downloads_completed"
    PROMOTION_FAILURES = "emule_promotion_failures"
    VERIFICATIONS = "emule_verifications"
    VERIFIER_UNAVAILABLE = "emule_verifier_unavailable"
    CONNECTED_INSTANCES = "emule_connected_instances"
    VERIFICATION_QUEUE_DEPTH = "emule_verification_queue_depth"
    CRAWLER_UP = "emule_crawler_up"
    PORT_SYNC_TRIGGERED = "emule_port_sync_triggered"
    HIGH_ID_RECOVERED = "emule_high_id_recovered"
    PORT_MISMATCH = "emule_port_mismatch"


MetricKind = Literal["inc", "set", "observe"]


@dataclass(frozen=True)
class MetricInstruction:
    """A metric operation: counter ``inc`` / gauge ``set`` / histogram ``observe``.

    ``labels`` = tuple of ordered (key, value) pairs (hashable → usable in a ``Report``
    equality test). ``value`` = quantity (default 1.0 for ``inc``).
    """

    name: MetricName
    kind: MetricKind
    labels: tuple[tuple[str, str], ...] = ()
    value: float = 1.0


@dataclass(frozen=True)
class Report:
    """How to report an event: severity + message + metric(s) + notif audiences.

    ``metrics`` is a TUPLE (one event can feed several metrics —
    ``SearchCycleCompleted`` = counter + histogram). Empty ``audiences`` = no notif.
    """

    severity: Severity
    message: str
    metrics: tuple[MetricInstruction, ...] = ()
    audiences: frozenset[Audience] = frozenset()


_VERDICT_SEVERITY: dict[str, Severity] = {
    "clean": Severity.INFO,
    "suspicious": Severity.INFO,
    "malicious": Severity.WARNING,
    "error": Severity.WARNING,
}
_VERDICT_AUDIENCES: dict[str, frozenset[Audience]] = {
    "clean": frozenset({Audience.COMMUNITY}),
    "suspicious": frozenset({Audience.OPERATIONS}),
    "malicious": frozenset({Audience.OPERATIONS}),
    "error": frozenset(),
}


def _verification(event: VerificationCompleted) -> Report:
    # unknown verdict (verifier contract not honored) → treated as ``error`` (defensive, E-D13).
    severity = _VERDICT_SEVERITY.get(event.verdict, Severity.WARNING)
    audiences = _VERDICT_AUDIENCES.get(event.verdict, frozenset())
    return Report(
        severity,
        f"verification {event.target_id}: verdict={event.verdict}",
        (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", event.verdict),)),),
        audiences,
    )


def describe(event: Event) -> Report:
    """Map an event to its ``Report`` (EXHAUSTIVE match → 100% branch)."""
    match event:
        case SearchCycleCompleted():
            return Report(
                Severity.INFO,
                f"cycle {event.cycle_index} done ({event.duration_seconds:.1f}s)",
                (
                    MetricInstruction(MetricName.SEARCH_CYCLES, "inc"),
                    MetricInstruction(
                        MetricName.SEARCH_CYCLE_DURATION, "observe", value=event.duration_seconds
                    ),
                ),
            )
        case SearchExecuted():
            return Report(
                Severity.DEBUG,
                f"search {event.network}: {event.n_results} result(s)",
                (MetricInstruction(MetricName.SEARCHES, "inc", (("network", event.network),)),),
            )
        case InstanceUnreachable():
            return Report(
                Severity.WARNING,
                f"instance {event.instance} unreachable",
                (
                    MetricInstruction(
                        MetricName.MULE_UNREACHABLE, "inc", (("instance", event.instance),)
                    ),
                ),
            )
        case SearchFailed():
            return Report(
                Severity.WARNING,
                f"search failed on {event.network} (instance {event.instance})",
                (
                    MetricInstruction(
                        MetricName.SEARCH_FAILURES, "inc", (("network", event.network),)
                    ),
                ),
            )
        case SearchTaskDropped():
            return Report(
                Severity.WARNING,
                f"task '{event.keyword}'/{event.network} dropped (all instances in backoff)",
                (
                    MetricInstruction(
                        MetricName.SEARCH_TASKS_DROPPED, "inc", (("network", event.network),)
                    ),
                ),
            )
        case AllInstancesBlind():
            return Report(
                Severity.WARNING,
                "blind coverage: no search-capable instance",
                (MetricInstruction(MetricName.SEARCH_BLIND_CYCLES, "inc"),),
                frozenset({Audience.OPERATIONS}) if event.first_occurrence else frozenset(),
            )
        case ObservationRecorded():
            return Report(
                Severity.DEBUG,
                f"observation recorded ({event.network})",
                (MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", event.network),)),),
            )
        case DecisionRecorded():
            audiences: frozenset[Audience]
            if event.tier == "download":
                audiences = frozenset({Audience.COMMUNITY})
            elif event.tier == "notify":
                audiences = frozenset({Audience.OPERATIONS})
            else:
                audiences = frozenset()
            return Report(
                Severity.INFO,
                f"decision {event.tier} for {event.target_id}",
                (MetricInstruction(MetricName.DECISIONS, "inc", (("tier", event.tier),)),),
                audiences,
            )
        case DownloadQueued():
            return Report(
                Severity.INFO,
                f"download queued: {event.target_id}",
                (MetricInstruction(MetricName.DOWNLOADS_QUEUED, "inc"),),
            )
        case DownloadCompleted():
            return Report(
                Severity.INFO,
                f"✅ download completed: {event.target_id}",
                (MetricInstruction(MetricName.DOWNLOADS_COMPLETED, "inc"),),
                frozenset({Audience.COMMUNITY}),
            )
        case PromotionFailed():
            return Report(
                Severity.WARNING,
                f"quarantine promotion failed: {event.ed2k_hash}",
                (MetricInstruction(MetricName.PROMOTION_FAILURES, "inc"),),
            )
        case VerificationCompleted():
            return _verification(event)
        case VerifierUnavailable():
            return Report(
                Severity.WARNING,
                "verifier unreachable",
                (MetricInstruction(MetricName.VERIFIER_UNAVAILABLE, "inc"),),
                frozenset({Audience.OPERATIONS}) if event.first_occurrence else frozenset(),
            )
        case ConnectedInstancesSampled():
            return Report(
                Severity.DEBUG,
                f"connected instances ({event.network}): {event.count}",
                (
                    MetricInstruction(
                        MetricName.CONNECTED_INSTANCES,
                        "set",
                        (("network", event.network),),
                        float(event.count),
                    ),
                ),
            )
        case SearchCapabilitySampled():
            # Binary current-state gauge: 1 when at least one instance can search now, else 0.
            # Sampled every cycle (not edge-triggered) so Grafana can alert on "capable == 0
            # for N minutes" without rate() on the SEARCH_BLIND_CYCLES counter.
            return Report(
                Severity.DEBUG,
                f"search-capable: {'yes' if event.capable else 'no'}",
                (MetricInstruction(MetricName.SEARCH_CAPABLE, "set", (), float(event.capable)),),
            )
        case VerificationQueueDepthSampled():
            return Report(
                Severity.DEBUG,
                f"verification queue: {event.count} pending",
                (
                    MetricInstruction(
                        MetricName.VERIFICATION_QUEUE_DEPTH, "set", (), float(event.count)
                    ),
                ),
            )
        case CrawlerStarted():
            return Report(
                Severity.INFO,
                f"🟢 instance online (mode {event.mode})",
                (MetricInstruction(MetricName.CRAWLER_UP, "set", (), 1.0),),
                frozenset({Audience.COMMUNITY, Audience.OPERATIONS}),
            )
        case PortSyncTriggered():
            return Report(
                Severity.INFO,
                f"port-sync: {event.old} → {event.new} (restart amuled)",
                (MetricInstruction(MetricName.PORT_SYNC_TRIGGERED, "inc"),),
            )
        case HighIdRecovered():
            return Report(
                Severity.INFO,
                f"High-ID recovered on port {event.port}",
                (MetricInstruction(MetricName.HIGH_ID_RECOVERED, "inc"),),
                frozenset({Audience.COMMUNITY}),
            )
        case PortMismatchUnresolved():
            # Fallback alert (DECISION 5): OPERATIONS, edge-triggered (notif on the 1st occurrence
            # only); the metric increments on EVERY occurrence (Prometheus wants the raw state).
            # Wording valid whether or not the port was applied: if `configured` == `live`, the
            # SetPort+restart took but the High-ID has not (yet) come back; otherwise the port
            # could not be applied (restart impossible). No misleading "X ≠ X".
            return Report(
                Severity.WARNING,
                f"High-ID not restored "
                f"(forwarded port {event.live}, amuled port {event.configured})",
                (MetricInstruction(MetricName.PORT_MISMATCH, "inc"),),
                frozenset({Audience.OPERATIONS}) if event.first_occurrence else frozenset(),
            )
        case _:  # pragma: no cover
            assert_never(event)
