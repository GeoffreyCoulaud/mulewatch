"""``SchedulerStateRepository`` port: the scheduler's durable state (spec §4/§7).

PORTS layer, SYNCHRONOUS Protocol (same principle as ``LocalStateRepository``: sub-ms, no
``to_thread`` in the MVP). Persists what crash recovery re-reads (spec §7): the cycle INDEX
(only advances at the END of a cycle → a kill mid-way replays the remaining keywords), the
timestamp of the last full cycle, AND the per-(instance, channel) BACKOFF (spec §3/§7: it
must survive a restart). Everything is stored as KV in the ``scheduler_state`` table of
``local.db`` (never merged, invariant §11).

The backoff is serialized as JSON under ONE key (``channel_backoff``): a map
``{ "amule-1:kad": {attempts, retry_after}, "amule-1": {...} }`` — the key is either
``instance:channel`` (a channel failure), or ``instance`` alone (reconnection). ``retry_after``
is a fixed-width ISO-8601 UTC (lexicographic comparison == chronological).
``read_cycle_index`` returns ``0`` if never written (first startup); ``load_channel_backoff``
returns an empty dict.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ChannelBackoff:
    """Backoff state of a key (instance, or instance:channel): counter + deadline.

    ``attempts`` = number of CONSECUTIVE failures (used for the exponential computation).
    ``retry_after`` = fixed-width ISO-8601 UTC: as long as ``now < retry_after``, the key is
    SKIPPED. Frozen and JSON-friendly (two scalar fields) → trivial serialization.
    """

    attempts: int
    retry_after: str


class SchedulerStateRepository(Protocol):
    """Sync scheduling-state contract (cycle index + last cycle + backoff).

    ``write_cycle_state`` receives an AWARE ``datetime`` (the application passes
    ``clock.now()``, which depends on no adapter); the ISO-8601 formatting is internal to the
    SQLite adapter. ``save_channel_backoff`` ENTIRELY replaces the persisted map (registry
    snapshot).
    """

    def read_cycle_index(self) -> int: ...

    def write_cycle_state(self, cycle_index: int, last_full_cycle_at: datetime) -> None: ...

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]: ...

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None: ...
