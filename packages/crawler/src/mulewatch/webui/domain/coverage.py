"""Pure derivation of a target's coverage status (webui spec §5). No I/O.

The tier order comes from ``catalog_matching.config.TIER_RANK`` (source of truth shared
with the matching engine). Without this sharing, the webui reinvented its own divergent
table — a 4th tier or a rename would have silently skewed the coverage.
"""

from collections.abc import Sequence

from catalog_matching.config import TIER_RANK
from mulewatch.webui.domain.views import CoverageStatus


def coverage_for(target_id: str, decisions: Sequence[tuple[str, str]]) -> CoverageStatus:
    """``decisions`` = ``(ed2k_hash, tier)`` of the latest verdicts for this target."""
    if not decisions:
        return CoverageStatus(status="none", best_tier=None, file_count=0)
    # TIER_RANK: increasing integer = stronger tier (download > notify > catalog); an unknown
    # tier falls back to ``-1`` (below the weakest) — neutral with respect to the choice.
    best = max(decisions, key=lambda d: TIER_RANK.get(d[1], -1))[1]
    status = "found" if best == "download" else "partial"
    return CoverageStatus(status=status, best_tier=best, file_count=len(decisions))
