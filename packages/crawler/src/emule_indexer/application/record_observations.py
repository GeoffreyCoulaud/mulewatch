"""Pipeline par observation : record → evaluate → (si verdict changé) decide + nudge.

Couche APPLICATION (spec orchestration §4) : orchestre des PORTS (sync repos + moteur pur
+ hub de nudge async), ne fait aucune I/O elle-même. Pour CHAQUE observation (spec §4) :

1. ``record_observation`` TOUJOURS (la re-observation périodique est le but, spec §3/§6).
2. ``evaluate`` via le moteur pur ; ``None`` (fichier écarté) → on s'arrête là.
3. Anti-redondance (spec §3) : on lit le dernier verdict connu (``last_decision``) ; on ne
   ``record_decision`` (et ne ``signal`` le hub) QUE si le verdict CHANGE (nouveau hash, ou
   ``DecisionRecord`` différent). Verdict identique → ni ré-append ni nudge.

Les repos sont SYNCHRONES, appelés DIRECTEMENT (spec §3 : sub-ms, pas de ``to_thread`` en
MVP ; conséquence assumée : les écritures DB sont sérialisées de facto sur l'event loop).
Une ``RepositoryError`` (contrat de PORT, jamais un adapter) sur UNE observation est
LOGGÉE et ABSORBÉE ici : la fonction rend ``False`` et le cycle continue (spec §7) — une
seule obs corrompue/en échec ne fait pas tomber tout le balayage, mais l'échec reste
VISIBLE (log niveau ``error``, pour qu'un échec persistant se remarque).
"""

import logging

from emule_indexer.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from emule_indexer.domain.matching.engine import MatchingEngine, to_record
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
    """Traite UNE observation (spec §4). Rend ``True`` ssi un NOUVEAU verdict a été persisté.

    Émet ``ObservationRecorded`` dès l'enregistrement (toujours), et ``DecisionRecorded`` au
    changement de verdict. Une ``RepositoryError`` est absorbée (log + ``False``), le cycle
    continue (spec §7)."""
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
            "persistance échouée sur hash=%s (%s) — observation ignorée, cycle continue",
            observation.ed2k_hash,
            error,
        )
        return False
    _logger.info(
        "verdict changé hash=%s target=%s tier=%s règle=%s",
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
