"""Shared decision helper: evaluate → set-diff → record / retract → emit → nudge (spec §7).

APPLICATION layer, PURE orchestration (no ``try/except`` here — a ``RepositoryError`` is a
port contract each CALLER absorbs on its own terms, cf. ``record_observation`` and the backfill
use-case). Used by BOTH the per-observation pipeline (``record_observations.py``) and the
startup catalogue re-evaluation, so the set-diff + retraction + nudge logic is written once.

Set diff keyed by ``(ed2k_hash, target_id)`` (spec §7). ``engine.evaluate`` returns a LIST of
:class:`MatchDecision` — one per segment target the file covers, empty = discarded. A fresh
decision is persisted (and nudged) only when it differs from the file's LATEST persisted
:class:`DecisionRecord` for THAT target; a target that dropped out of the fresh set (was
matched, now absent) is retracted — unless it is already retracted (no-op). Returns the number
of rows written (0..N; a decision OR a retraction each counts as one).
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
) -> int:
    """Evaluate ``candidate``; append new/changed decisions and retract dropped targets.

    Returns the number of rows written (0..N). May propagate ``RepositoryError`` (pure
    orchestration; the caller absorbs it)."""
    fresh = engine.evaluate(candidate)
    persisted = catalog.last_decisions(ed2k_hash)
    written = 0
    fresh_ids: set[str] = set()
    for decision in fresh:
        fresh_ids.add(decision.target_id)
        if persisted.get(decision.target_id) == to_record(decision):
            continue
        catalog.record_decision(ed2k_hash, decision)
        written += 1
        await telemetry.emit(DecisionRecorded(target_id=decision.target_id, tier=decision.tier))
        signal.signal(ed2k_hash)
        if decision.tier == "download":
            signal.signal(DOWNLOAD_NUDGE_SUBJECT)
    for target_id in sorted(persisted):
        if persisted[target_id].tier == RETRACTED_TIER or target_id in fresh_ids:
            continue
        catalog.record_retraction(ed2k_hash, target_id)
        written += 1
        await telemetry.emit(DecisionRecorded(target_id=target_id, tier=RETRACTED_TIER))
    return written
