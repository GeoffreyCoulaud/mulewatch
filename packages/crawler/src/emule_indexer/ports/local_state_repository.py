"""Port ``LocalStateRepository`` : identité du nœud + file de tâches (spec data-model §4/§6).

Protocol SYNCHRONE (même principe que ``CatalogRepository``). ``ClaimedTask`` est le DTO
gelé du claim (spec §4) : ``attempts`` est compté AU CLAIM (spec §6) — le consommateur
(plan D) le verra à 1 dès la première prise. ``local.db`` n'est JAMAIS fusionné : ce port
ne traverse pas la frontière du nœud (invariant MVP §11).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ClaimedTask:
    """Une tâche de vérification prise par un worker (lease posée, attempts incrémenté)."""

    task_id: int
    ed2k_hash: str
    attempts: int


class LocalStateRepository(Protocol):
    """Contrat sync de l'état local : identité stable + file FIFO idempotente (§12 MVP)."""

    def node_id(self) -> str: ...

    def enqueue_verification(self, ed2k_hash: str) -> bool: ...

    def claim_verification(self) -> ClaimedTask | None: ...

    def complete_verification(self, task_id: int) -> None: ...

    def fail_verification(self, task_id: int) -> None: ...

    def reclaim_expired(self) -> int: ...
