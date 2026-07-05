"""EFFECTIVE network coverage, derived from statuses (PURE, spec orchestration §7; MVP §13).

PURE domain: receives already-observed BOOLEAN facts ("can this instance make a search
succeed?") and returns an aggregated signal. "The process is alive" ≠ "we can find right
now" (spec MVP §13): ``effective_coverage`` answers the second question.

The domain does NOT know ``NetworkStatus`` (which lives in ``ports`` — dependency rule
``ports ← application → domain``: the domain never imports a port). It is the APPLICATION
(``run_search_cycle``) that translates each ``NetworkStatus`` into a "search-capable"
boolean (HighID eD2k OR Kad CONNECTED) before calling this pure function.
"""

from collections.abc import Sequence
from enum import StrEnum


class Coverage(StrEnum):
    """Aggregated coverage signal (spec MVP §13). Closed enum."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLIND = "blind"


def effective_coverage(search_capable: Sequence[bool]) -> Coverage:
    """Aggregate per-instance search capability into one signal (spec MVP §13).

    No instance (empty list) OR none capable → ``BLIND`` (we can find nothing, logged
    loudly by the caller, spec §7). All capable → ``HEALTHY``. A mix → ``DEGRADED`` (some
    instances blind). ``any(())`` is ``False`` → the empty list correctly lands on
    ``BLIND``.
    """
    if not any(search_capable):
        return Coverage.BLIND
    if all(search_capable):
        return Coverage.HEALTHY
    return Coverage.DEGRADED
