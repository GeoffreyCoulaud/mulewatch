"""``describe`` is an exhaustive match: one case per event + each conditional branch
(known/unknown verdict, download/other tier, first_occurrence true/false)."""

from emule_indexer.domain.observability import events as ev
from emule_indexer.domain.observability.policy import (
    Audience,
    MetricInstruction,
    MetricName,
    Report,
    Severity,
    describe,
)

_COMMUNITY = frozenset({Audience.COMMUNITY})
_OPERATIONS = frozenset({Audience.OPERATIONS})
_BOTH = frozenset({Audience.COMMUNITY, Audience.OPERATIONS})


CASES: list[tuple[ev.Event, Report]] = [
    (
        ev.SearchCycleCompleted(cycle_index=3, duration_seconds=4.5),
        Report(
            Severity.INFO,
            "cycle 3 done (4.5s)",
            (
                MetricInstruction(MetricName.SEARCH_CYCLES, "inc"),
                MetricInstruction(MetricName.SEARCH_CYCLE_DURATION, "observe", value=4.5),
            ),
        ),
    ),
    (
        ev.SearchExecuted(network="ed2k", n_results=7),
        Report(
            Severity.DEBUG,
            "search ed2k: 7 result(s)",
            (MetricInstruction(MetricName.SEARCHES, "inc", (("network", "ed2k"),)),),
        ),
    ),
    (
        ev.InstanceUnreachable(instance="amule-1"),
        Report(
            Severity.WARNING,
            "instance amule-1 unreachable",
            (MetricInstruction(MetricName.MULE_UNREACHABLE, "inc", (("instance", "amule-1"),)),),
        ),
    ),
    (
        ev.SearchFailed(instance="amule-1", network="kad"),
        Report(
            Severity.WARNING,
            "search failed on kad (instance amule-1)",
            (MetricInstruction(MetricName.SEARCH_FAILURES, "inc", (("network", "kad"),)),),
        ),
    ),
    (
        ev.AllInstancesBlind(first_occurrence=True),
        Report(
            Severity.WARNING,
            "blind coverage: no search-capable instance",
            (MetricInstruction(MetricName.SEARCH_BLIND_CYCLES, "inc"),),
            _OPERATIONS,
        ),
    ),
    (
        ev.AllInstancesBlind(first_occurrence=False),
        Report(
            Severity.WARNING,
            "blind coverage: no search-capable instance",
            (MetricInstruction(MetricName.SEARCH_BLIND_CYCLES, "inc"),),
        ),
    ),
    (
        ev.ObservationRecorded(network="kad"),
        Report(
            Severity.DEBUG,
            "observation recorded (kad)",
            (MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", "kad"),)),),
        ),
    ),
    (
        ev.DecisionRecorded(target_id="062A", tier="download"),
        Report(
            Severity.INFO,
            "decision download for 062A",
            (MetricInstruction(MetricName.DECISIONS, "inc", (("tier", "download"),)),),
            _COMMUNITY,
        ),
    ),
    (
        ev.DecisionRecorded(target_id="062A", tier="candidate"),
        Report(
            Severity.INFO,
            "decision candidate for 062A",
            (MetricInstruction(MetricName.DECISIONS, "inc", (("tier", "candidate"),)),),
        ),
    ),
    (
        ev.DownloadQueued(target_id="062A"),
        Report(
            Severity.INFO,
            "download queued: 062A",
            (MetricInstruction(MetricName.DOWNLOADS_QUEUED, "inc"),),
        ),
    ),
    (
        ev.DownloadCompleted(target_id="062A", ed2k_hash="a" * 32),
        Report(
            Severity.INFO,
            "✅ download completed: 062A",
            (MetricInstruction(MetricName.DOWNLOADS_COMPLETED, "inc"),),
            _COMMUNITY,
        ),
    ),
    (
        ev.PromotionFailed(ed2k_hash="a" * 32),
        Report(
            Severity.WARNING,
            f"quarantine promotion failed: {'a' * 32}",
            (MetricInstruction(MetricName.PROMOTION_FAILURES, "inc"),),
        ),
    ),
    (
        ev.VerificationCompleted(target_id="062A", verdict="clean"),
        Report(
            Severity.INFO,
            "verification 062A: verdict=clean",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "clean"),)),),
            _COMMUNITY,
        ),
    ),
    (
        ev.VerificationCompleted(target_id="062A", verdict="suspicious"),
        Report(
            Severity.INFO,
            "verification 062A: verdict=suspicious",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "suspicious"),)),),
            _OPERATIONS,
        ),
    ),
    (
        ev.VerificationCompleted(target_id="062A", verdict="malicious"),
        Report(
            Severity.WARNING,
            "verification 062A: verdict=malicious",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "malicious"),)),),
            _OPERATIONS,
        ),
    ),
    (
        ev.VerificationCompleted(target_id="062A", verdict="error"),
        Report(
            Severity.WARNING,
            "verification 062A: verdict=error",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "error"),)),),
        ),
    ),
    (
        # UNKNOWN verdict → defensive
        ev.VerificationCompleted(target_id="062A", verdict="bogus"),
        Report(
            Severity.WARNING,
            "verification 062A: verdict=bogus",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "bogus"),)),),
        ),
    ),
    (
        ev.VerifierUnavailable(first_occurrence=True),
        Report(
            Severity.WARNING,
            "verifier unreachable",
            (MetricInstruction(MetricName.VERIFIER_UNAVAILABLE, "inc"),),
            _OPERATIONS,
        ),
    ),
    (
        ev.VerifierUnavailable(first_occurrence=False),
        Report(
            Severity.WARNING,
            "verifier unreachable",
            (MetricInstruction(MetricName.VERIFIER_UNAVAILABLE, "inc"),),
        ),
    ),
    (
        ev.ConnectedInstancesSampled(network="ed2k", count=2),
        Report(
            Severity.DEBUG,
            "connected instances (ed2k): 2",
            (
                MetricInstruction(
                    MetricName.CONNECTED_INSTANCES, "set", (("network", "ed2k"),), 2.0
                ),
            ),
        ),
    ),
    (
        ev.VerificationQueueDepthSampled(count=5),
        Report(
            Severity.DEBUG,
            "verification queue: 5 pending",
            (MetricInstruction(MetricName.VERIFICATION_QUEUE_DEPTH, "set", (), 5.0),),
        ),
    ),
    (
        ev.CrawlerStarted(mode="full"),
        Report(
            Severity.INFO,
            "🟢 instance online (mode full)",
            (MetricInstruction(MetricName.CRAWLER_UP, "set", (), 1.0),),
            _BOTH,
        ),
    ),
    (
        ev.PortSyncTriggered(old=4662, new=51820),
        Report(
            Severity.INFO,
            "port-sync: 4662 → 51820 (restart amuled)",
            (MetricInstruction(MetricName.PORT_SYNC_TRIGGERED, "inc"),),
        ),
    ),
    (
        ev.HighIdRecovered(port=51820),
        Report(
            Severity.INFO,
            "High-ID recovered on port 51820",
            (MetricInstruction(MetricName.HIGH_ID_RECOVERED, "inc"),),
            _COMMUNITY,
        ),
    ),
    (
        ev.PortMismatchUnresolved(first_occurrence=True, live=51820, configured=4662),
        Report(
            Severity.WARNING,
            "High-ID not restored (forwarded port 51820, amuled port 4662)",
            (MetricInstruction(MetricName.PORT_MISMATCH, "inc"),),
            _OPERATIONS,
        ),
    ),
    (
        ev.PortMismatchUnresolved(first_occurrence=False, live=51820, configured=4662),
        Report(
            Severity.WARNING,
            "High-ID not restored (forwarded port 51820, amuled port 4662)",
            (MetricInstruction(MetricName.PORT_MISMATCH, "inc"),),
        ),
    ),
]


def test_describe_maps_every_event() -> None:
    for event, expected in CASES:
        assert describe(event) == expected, f"wrong Report for {event!r}"
