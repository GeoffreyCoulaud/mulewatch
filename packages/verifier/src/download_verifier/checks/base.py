"""Check model & worst-status aggregation (analysis spec §5).

Each check returns a ``CheckOutcome(name, status, meta)`` with ``status`` in
``clean < suspicious < malicious``. The file verdict = worst-status over the list of statuses.
``error`` is NOT a check status (it is a service-level result, §6) — it never appears here.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

Status = Literal["clean", "suspicious", "malicious"]

# Severity order: a more severe check overrides a less severe one (worst-status).
STATUS_RANK: dict[Status, int] = {"clean": 0, "suspicious": 1, "malicious": 2}
_RANK_TO_STATUS: dict[int, Status] = {rank: status for status, rank in STATUS_RANK.items()}


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """A check's result: its name, its severity verdict, and its contribution to ``real_meta``."""

    name: str
    status: Status
    meta: Mapping[str, object]


def worst_status(statuses: Iterable[Status]) -> Status:
    """Most severe status in ``statuses``; empty list → ``clean`` (nothing dangerous seen)."""
    return _RANK_TO_STATUS[max((STATUS_RANK[status] for status in statuses), default=0)]
