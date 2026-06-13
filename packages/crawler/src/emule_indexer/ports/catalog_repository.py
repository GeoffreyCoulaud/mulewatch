"""Port ``CatalogRepository`` : la mémoire durable du catalogue (spec data-model §4).

Protocol SYNCHRONE (spec §3 : une écriture locale est sub-milliseconde ; si le plan C
veut s'isoler, il enveloppera dans ``asyncio.to_thread`` sans toucher cette couche).
Le port n'importe QUE le domaine. Les stubs tiennent sur UNE ligne (le ``def`` s'exécute
à la création de la classe : couvert). L'adapter stamppe ``observed_at``/``decided_at``/
``node_id`` — c'est pour ça que ``record_decision`` reçoit le hash À CÔTÉ de la décision
(``MatchDecision`` ne porte pas la clé contenu, par principe : domaine sans colonnes de
persistance).
"""

from dataclasses import dataclass
from typing import Protocol

from emule_indexer.domain.matching.engine import DecisionRecord, DownloadCandidate, MatchDecision
from emule_indexer.domain.observation import FileObservation


@dataclass(frozen=True)
class ObservedFile:
    """Forme de LECTURE minimale d'une observation : nom + taille (pour bâtir un lien ed2k).

    La boucle de download (spec §5) lit la DERNIÈRE observation d'un hash pour reconstruire
    son lien ed2k (``build_ed2k_link(filename, size_bytes, hash)``). On ne rend que les deux
    champs nécessaires — pas tout ``FileObservation`` (le reste est inutile au download).
    """

    filename: str
    size_bytes: int


class CatalogRepository(Protocol):
    """Contrat sync d'écriture du catalogue (append-only ; l'adapter signale, il ne décide pas).

    ``last_decision`` (anti-redondance, spec orchestration §3) rend un :class:`DecisionRecord`.
    ``download_decisions`` (spec download §5) rend les :class:`DownloadCandidate` dont le
    DERNIER verdict est tier=download (à rejouer par la boucle de download). ``last_observation``
    rend l':class:`ObservedFile` la plus récente d'un hash (nom+taille pour le lien ed2k), ou
    ``None``. Ces trois lectures sont inoffensives (aucune écriture).
    """

    def record_observation(self, observation: FileObservation) -> None: ...

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None: ...

    def last_decision(self, ed2k_hash: str) -> DecisionRecord | None: ...

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None: ...
