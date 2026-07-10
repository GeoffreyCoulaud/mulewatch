"""Observability events: PURE business facts (spec Plan E §3-4).

DOMAIN layer (pure). One FROZEN dataclass per salient observable fact; tagged union
``Event``. Business fields ONLY — no notion of log/metric/notif (that is ``policy.describe``'s
job). Recurring failure facts carry ``first_occurrence`` (computed by the application via
``EdgeState``) for notification anti-spam (E-D8).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchCycleCompleted:
    cycle_index: int
    duration_seconds: float


@dataclass(frozen=True)
class SearchExecuted:
    network: str
    n_results: int


@dataclass(frozen=True)
class InstanceUnreachable:
    instance: str


@dataclass(frozen=True)
class SearchFailed:
    instance: str
    network: str


@dataclass(frozen=True)
class SearchTaskDropped:
    # All instances in backoff refused this task during the cycle (spec §14):
    # no worker can process it → we drop it and trace it for visibility.
    keyword: str
    network: str


@dataclass(frozen=True)
class AllInstancesBlind:
    first_occurrence: bool


@dataclass(frozen=True)
class ObservationRecorded:
    network: str


@dataclass(frozen=True)
class DecisionRecorded:
    target_id: str
    tier: str


@dataclass(frozen=True)
class DownloadQueued:
    target_id: str


@dataclass(frozen=True)
class DownloadCompleted:
    target_id: str
    ed2k_hash: str


@dataclass(frozen=True)
class PromotionFailed:
    ed2k_hash: str


@dataclass(frozen=True)
class VerificationCompleted:
    target_id: str
    verdict: str


@dataclass(frozen=True)
class VerifierUnavailable:
    first_occurrence: bool


@dataclass(frozen=True)
class ConnectedInstancesSampled:
    network: str
    count: int


@dataclass(frozen=True)
class SearchCapabilitySampled:
    # Current-state sample of "can we search RIGHT NOW?" (at least one instance capable),
    # sampled every cycle → binary gauge. Complements the AllInstancesBlind counter (cumulative,
    # edge-triggered): this one carries the live 0/1 signal Grafana alerts on.
    capable: bool


@dataclass(frozen=True)
class VerificationQueueDepthSampled:
    count: int


@dataclass(frozen=True)
class CrawlerStarted:
    mode: str


@dataclass(frozen=True)
class PortSyncTriggered:
    old: int  # listen port configured before
    new: int  # targeted forwarded port (the one we align amuled to)


@dataclass(frozen=True)
class HighIdRecovered:
    port: int  # High-ID port confirmed after restart


@dataclass(frozen=True)
class PortMismatchUnresolved:
    first_occurrence: bool  # edge-triggered (E-D8) — computed via EdgeState
    live: int  # live forwarded port (gluetun)
    configured: int  # amuled's listen port (stayed wrong)


type Event = (
    SearchCycleCompleted
    | SearchExecuted
    | InstanceUnreachable
    | SearchFailed
    | SearchTaskDropped
    | AllInstancesBlind
    | ObservationRecorded
    | DecisionRecorded
    | DownloadQueued
    | DownloadCompleted
    | PromotionFailed
    | VerificationCompleted
    | VerifierUnavailable
    | ConnectedInstancesSampled
    | SearchCapabilitySampled
    | VerificationQueueDepthSampled
    | CrawlerStarted
    | PortSyncTriggered
    | HighIdRecovered
    | PortMismatchUnresolved
)
