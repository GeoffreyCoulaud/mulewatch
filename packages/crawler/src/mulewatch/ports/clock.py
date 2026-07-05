"""``Clock`` and ``Rng`` ports: time and randomness, injectable (spec orchestration §3).

TOTAL determinism (spec §3): the application never reads the system clock nor a global
``random`` directly — it goes through these ports, which tests replace with advanceable/
seeded fake implementations (zero flakiness, every cycle replayable).

``Clock`` carries an AWARE ``now()`` (UTC) AND an ASYNC ``sleep`` (the cycle sleeps between
two iterations): the two faces of time the orchestration needs. The ``sleep`` is on the
port so a fake can advance it WITHOUT a real wait.

``Rng`` is the deterministic shuffler consumed by ``domain/search/cycle.py``; it is
RE-EXPORTED here from the domain (the Protocol's canonical definition lives in the domain,
where it is consumed — dependency rule: the domain never imports a port). This re-export
gives the adapters/composition a single "the time ports" import point.
"""

from datetime import datetime
from typing import Protocol

from mulewatch.domain.search.cycle import Rng

__all__ = ["Clock", "Rng"]


class Clock(Protocol):
    """Time, injectable: aware ``now()`` (UTC) + async ``sleep`` (spec §3).

    Implemented on the adapter side by ``datetime.now(UTC)`` + ``asyncio.sleep``; replaced
    in tests by an advanceable fake clock (the ``sleep`` advances ``now`` without waiting).
    """

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...
