"""Per-observation pipeline: record → evaluate → (if verdict changed) decide + nudge.

APPLICATION layer (orchestration spec §4): orchestrates PORTS (sync repos + pure engine
+ async nudge hub), does no I/O itself. For EACH observation (spec §4):

1. ``record_observation`` ALWAYS (periodic re-observation is the goal, spec §3/§6).
2. ``evaluate`` via the pure engine; ``None`` (file discarded) → we stop there.
3. Anti-redundancy (spec §3): we read the last known verdict (``last_decision``); we only
   ``record_decision`` (and ``signal`` the hub) IF the verdict CHANGES (new hash, or a
   different ``DecisionRecord``). Identical verdict → neither re-append nor nudge.

The repos are SYNCHRONOUS, called DIRECTLY (spec §3: sub-ms, no ``to_thread`` in the
MVP; accepted consequence: DB writes are de facto serialized on the event loop).
A ``RepositoryError`` (a PORT contract, never an adapter) on ONE observation is
LOGGED and ABSORBED here: the function returns ``False`` and the cycle continues (spec §7) — a
single corrupt/failed obs does not bring down the whole sweep, but the failure stays
VISIBLE (``error``-level log, so a persistent failure gets noticed).
"""

import logging

from catalog_matching.engine import MatchingEngine, to_record
from emule_indexer.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from emule_indexer.domain.observability.events import DecisionRecorded, ObservationRecorded
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import CatalogRepository
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.repository_errors import RepositoryError
from emule_indexer.ports.telemetry import Telemetry

_logger = logging.getLogger("emule_indexer.application.record_observations")


async def record_observation(
    observation: FileObservation,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
    telemetry: Telemetry,
    network: str,
) -> bool:
    """Process ONE observation (spec §4). Returns ``True`` iff a NEW verdict was persisted.

    Emits ``ObservationRecorded`` as soon as it is recorded (always), and ``DecisionRecorded`` on
    a verdict change. A ``RepositoryError`` is absorbed (log + ``False``), the cycle
    continues (spec §7)."""
    try:
        catalog.record_observation(observation)
        await telemetry.emit(ObservationRecorded(network=network))
        decision = engine.evaluate(observation.to_candidate())
        if decision is None:
            return False
        fresh = to_record(decision)
        if catalog.last_decision(observation.ed2k_hash) == fresh:
            return False
        catalog.record_decision(observation.ed2k_hash, decision)
    except RepositoryError as error:
        _logger.error(
            "persistence failed on hash=%s (%s) — observation skipped, cycle continues",
            observation.ed2k_hash,
            error,
        )
        return False
    _logger.info(
        "verdict changed hash=%s target=%s tier=%s rule=%s",
        observation.ed2k_hash,
        decision.target_id,
        decision.tier,
        decision.rule_name,
    )
    await telemetry.emit(DecisionRecorded(target_id=decision.target_id, tier=decision.tier))
    signal.signal(observation.ed2k_hash)
    if decision.tier == "download":
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)
    return True
