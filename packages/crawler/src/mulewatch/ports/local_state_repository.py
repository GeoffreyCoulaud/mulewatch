"""``LocalStateRepository`` port: node identity + task queue (spec data-model §4/§6).

SYNCHRONOUS Protocol (same principle as ``CatalogRepository``). ``ClaimedTask`` is the frozen
DTO of the claim (spec §4): ``attempts`` is counted AT CLAIM (spec §6) — the consumer (plan D)
will see it at 1 from the first take. ``local.db`` is NEVER merged: this port does not cross
the node boundary (invariant MVP §11).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ClaimedTask:
    """A verification task claimed by a worker (lease set, attempts incremented)."""

    task_id: int
    ed2k_hash: str
    attempts: int


class LocalStateRepository(Protocol):
    """Sync local-state contract: stable identity + idempotent FIFO queue (§12 MVP)."""

    def node_id(self) -> str: ...

    def enqueue_verification(self, ed2k_hash: str) -> bool: ...

    def claim_verification(self) -> ClaimedTask | None: ...

    def complete_verification(self, task_id: int) -> None: ...

    def fail_verification(self, task_id: int) -> None: ...

    def reclaim_expired(self) -> int: ...

    def count_pending_verifications(self) -> int: ...

    def last_backfill_policy(self) -> str | None: ...

    def set_last_backfill_policy(self, sha256: str) -> None: ...
