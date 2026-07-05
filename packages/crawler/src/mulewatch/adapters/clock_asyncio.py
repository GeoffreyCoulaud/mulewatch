"""Real adapters for time and randomness (orchestration spec §4).

``AsyncioClock``: ``now()`` = ``datetime.now(UTC)`` (aware), ``sleep`` = ``asyncio.sleep``
(the real event-loop sleep). ``SeededRng``: deterministic shuffle via
``random.Random(seed)`` (one seed → one ordering, verified) — this is the implementation of the
``Rng`` port consumed by ``domain/search/cycle.py``. Both adapters are replaced in tests
by advanceable/scripted fakes (fully deterministic, spec §3).
"""

import asyncio
import random
from datetime import UTC, datetime


class AsyncioClock:
    """Real ``Clock`` (STRUCTURAL port satisfaction)."""

    def now(self) -> datetime:
        """Current instant, AWARE in UTC (``Clock`` contract)."""
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        """Real event-loop sleep (cancellable at the ``await`` point, spec §6)."""
        await asyncio.sleep(seconds)


class SeededRng:
    """Real ``Rng`` (STRUCTURAL port satisfaction).

    ``shuffled``: deterministic permutation via ``random.Random(seed)`` (a fresh instance
    per call, seeded by ``seed`` → two calls with the same seed yield the same ordering).
    ``jitter``: REAL draw in ``[0, span)`` via a dedicated ``random.Random`` instance,
    seeded at construction (``jitter_seed``, defaults to system entropy) — the backoff
    jitter breaks the thundering-herd between nodes/channels."""

    def __init__(self, *, jitter_seed: int | str | None = None) -> None:
        self._jitter = random.Random(jitter_seed)

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        ordered = list(items)
        random.Random(seed).shuffle(ordered)
        return tuple(ordered)

    def jitter(self, span: float) -> float:
        """Float in ``[0, span)`` (``[0.0]`` if ``span <= 0``)."""
        if span <= 0:
            return 0.0
        return self._jitter.uniform(0.0, span)
