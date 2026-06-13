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
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import CatalogRepository
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.repository_errors import RepositoryError

_logger = logging.getLogger("emule_indexer.application.record_observations")


def record_observation(
    observation: FileObservation,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
) -> bool:
    """Traite UNE observation (spec §4). Rend ``True`` ssi un NOUVEAU verdict a été persisté.

    Le booléen sert au logging/aux compteurs de cycle (combien de verdicts ont changé).
    ``record_observation`` est toujours appelé d'abord (ordre d'écriture catalogue : la
    décision exige que l'observation existe, FK — handoff data-model §4). Une
    ``RepositoryError`` est absorbée (log + ``False``), le cycle continue (spec §7).
    """
    try:
        catalog.record_observation(observation)
        decision = engine.evaluate(observation.to_candidate())
        if decision is None:
            return False
        fresh = to_record(decision)
        if catalog.last_decision(observation.ed2k_hash) == fresh:
            # Verdict INCHANGÉ : ni ré-append (anti-redondance §3) ni nudge.
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
    signal.signal(observation.ed2k_hash)
    if decision.tier == "download":
        # Nudge le sujet conventionnel "download" (DÉCISION DV9) : la boucle de download s'y
        # abonne et rejoue le journal dès qu'un verdict download change. Best-effort (le poll
        # de repli reste le filet) — un nudge perdu est inoffensif (même contrat que le hash).
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)
    return True
