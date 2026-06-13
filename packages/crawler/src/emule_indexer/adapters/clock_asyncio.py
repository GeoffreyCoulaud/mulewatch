"""Adapters réels du temps et du hasard (spec orchestration §4).

``AsyncioClock`` : ``now()`` = ``datetime.now(UTC)`` (aware), ``sleep`` = ``asyncio.sleep``
(le vrai sommeil de l'event loop). ``SeededRng`` : mélange déterministe via
``random.Random(seed)`` (un seed → un ordre, vérifié) — c'est l'implémentation du port
``Rng`` consommé par ``domain/search/cycle.py``. Ces deux adapters sont remplacés en test
par des faux avançables/scriptés (déterminisme total, spec §3).
"""

import asyncio
import random
from datetime import UTC, datetime


class AsyncioClock:
    """``Clock`` réel (satisfaction STRUCTURELLE du port)."""

    def now(self) -> datetime:
        """Instant courant, AWARE en UTC (contrat de ``Clock``)."""
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        """Sommeil réel de l'event loop (annulable au point d'``await``, spec §6)."""
        await asyncio.sleep(seconds)


class SeededRng:
    """``Rng`` réel (satisfaction STRUCTURELLE du port).

    ``shuffled`` : permutation déterministe par ``random.Random(seed)`` (une instance neuve
    par appel, seedée par ``seed`` → deux appels de même seed rendent le même ordre).
    ``jitter`` : tirage RÉEL dans ``[0, span)`` via une instance ``random.Random`` propre,
    seedée à la construction (``jitter_seed``, défaut entropie système) — le jitter du
    backoff casse le thundering-herd entre nœuds/canaux."""

    def __init__(self, *, jitter_seed: int | str | None = None) -> None:
        self._jitter = random.Random(jitter_seed)

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        ordered = list(items)
        random.Random(seed).shuffle(ordered)
        return tuple(ordered)

    def jitter(self, span: float) -> float:
        """Flottant dans ``[0, span)`` (``[0.0]`` si ``span <= 0``)."""
        if span <= 0:
            return 0.0
        return self._jitter.uniform(0.0, span)
