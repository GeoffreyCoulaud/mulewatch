"""Shared decision helper: evaluate → compare → record (or retract) → emit → nudge (spec §4).

APPLICATION layer, PURE orchestration (no ``try/except`` here — a ``RepositoryError`` is a
port contract each CALLER absorbs on its own terms, cf. ``record_observation`` and the future
backfill use-case). Used by BOTH the per-observation pipeline (``record_observations.py``) and
the startup catalogue re-evaluation, so the anti-redundancy + retraction + nudge logic is
written exactly once.

Anti-redundancy (unchanged from the pre-refactor behaviour): a fresh verdict is persisted
(and nudged) only if it differs from the file's LATEST persisted :class:`DecisionRecord`.

Retraction (spec §5, the behavioural addition): when the engine now discards a file
(``evaluate`` returns ``None``) that previously had a REAL verdict, we append a sentinel
``RETRACTED_TIER`` decision instead of silently doing nothing — the append-only
``match_decisions`` table has no "un-match" primitive otherwise. A file that was already
retracted, or never matched at all, stays a no-op (no row, no emit).
"""

from catalog_matching.engine import MatchingEngine, to_record
from catalog_matching.models import FileCandidate
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.observability.events import DecisionRecorded
from mulewatch.domain.retraction import RETRACTED_TIER
from mulewatch.ports.catalog_repository import CatalogRepository
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.telemetry import Telemetry


async def record_decision_if_changed(
    ed2k_hash: str,
    candidate: FileCandidate,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
    telemetry: Telemetry,
) -> bool:
    """Evaluate ``candidate``; append a decision (or retraction) iff the verdict changed.

    Returns ``True`` iff a NEW row was written (a real decision OR a retraction). May
    propagate ``RepositoryError`` (pure orchestration; the caller absorbs it)."""
    decision = engine.evaluate(candidate)
    last = catalog.last_decision(ed2k_hash)
    if decision is None:
        if last is None or last.tier == RETRACTED_TIER:
            return False
        catalog.record_retraction(ed2k_hash)
        await telemetry.emit(DecisionRecorded(target_id="", tier=RETRACTED_TIER))
        return True
    fresh = to_record(decision)
    if last == fresh:
        return False
    catalog.record_decision(ed2k_hash, decision)
    await telemetry.emit(DecisionRecorded(target_id=decision.target_id, tier=decision.tier))
    signal.signal(ed2k_hash)
    if decision.tier == "download":
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)
    return True
