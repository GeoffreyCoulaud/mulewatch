"""Traversal order of a cycle, seeded per node (PURE, spec orchestration §3/§4).

PURE domain: no I/O, no clock, no GLOBAL ``random``. The shuffle is delegated to an
injected ``Rng`` port (``ports/clock.py``) to stay deterministic and testable; this module
ONLY builds the SEED (``node_id`` + cycle index) and applies the shuffle. Desired property
(spec MVP §6): two DIFFERENT nodes diverge (temporal blind spots removed), while the same
node at the same cycle REPLAYS the same order.
"""

from collections.abc import Sequence
from typing import Protocol


class Rng(Protocol):
    """Injectable randomness port. ``shuffled`` returns a PERMUTATION of ``items`` derived
    ONLY from the ``seed`` (same seed → same order). ``jitter`` returns a float in
    ``[0, span)`` (backoff anti-thundering-herd, spec §3 "+ jitter"). Implemented on the
    adapter side by ``random.Random`` (``adapters/clock_asyncio.py``); replaced in tests by
    a DETERMINISTIC fake (zero flakiness)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]: ...

    def jitter(self, span: float) -> float: ...


def cycle_seed(node_id: str, cycle_index: int) -> str:
    """Cycle seed: ``node_id`` + index, separated by ``:`` (spec §3).

    The ``node_id`` makes nodes diverge; the ``cycle_index`` varies the order from one
    cycle to the next ON the same node (otherwise the order would be frozen forever).
    """
    return f"{node_id}:{cycle_index}"


def shuffle_for_cycle(
    items: Sequence[str], rng: Rng, node_id: str, cycle_index: int
) -> tuple[str, ...]:
    """Deterministic permutation of ``items`` for this ``(node_id, cycle_index)`` (spec §4).

    The input order is irrelevant to the result (the seed determines it entirely); we go
    through a tuple to NEVER mutate the caller's sequence.
    """
    return rng.shuffled(tuple(items), cycle_seed(node_id, cycle_index))
