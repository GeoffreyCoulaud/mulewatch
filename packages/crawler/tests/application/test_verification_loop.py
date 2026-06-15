import asyncio

import pytest

from emule_indexer.application.edge_state import EdgeState
from emule_indexer.application.run_verification_cycle import (
    VerifyLoopDeps,
    verification_loop,
)
from emule_indexer.ports.local_state_repository import ClaimedTask
from tests.application.fakes import RecordingTelemetry
from tests.application.test_run_verification_cycle import (
    FakeClock,
    FakeQueue,
    FakeTargets,
    FakeVerifier,
    FakeWriter,
)

_A = "a" * 32


def _loop_deps(
    *, queue: FakeQueue, shutdown: asyncio.Event, clock: FakeClock | None = None
) -> VerifyLoopDeps:
    return VerifyLoopDeps(
        queue=queue,
        verifier=FakeVerifier(),
        writer=FakeWriter(),
        targets=FakeTargets(),
        poll_interval_seconds=10.0,
        clock=clock or FakeClock(),
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
        shutdown=shutdown,
    )


@pytest.mark.asyncio
async def test_loop_stops_when_shutdown_is_set_before_start() -> None:
    shutdown = asyncio.Event()
    shutdown.set()
    queue = FakeQueue(claims=[None])
    deps = _loop_deps(queue=queue, shutdown=shutdown)
    await asyncio.wait_for(verification_loop(deps), timeout=1.0)
    assert queue.reclaimed == 0  # aucun cycle


@pytest.mark.asyncio
async def test_loop_runs_cycles_then_stops() -> None:
    # Le while continue (False branch de `if shutdown.is_set(): break`) au moins une fois
    # avant l'arrêt : on attend le 2e sleep pour garantir deux cycles complets.
    shutdown = asyncio.Event()
    clock = FakeClock()
    queue = FakeQueue(claims=[None, None, None])  # file vide → dort à chaque cycle
    deps = _loop_deps(queue=queue, shutdown=shutdown, clock=clock)

    async def stop_after_second_sleep() -> None:
        while len(clock.sleeps) < 2:
            await asyncio.sleep(0)
        shutdown.set()

    await asyncio.gather(
        asyncio.wait_for(verification_loop(deps), timeout=2.0), stop_after_second_sleep()
    )
    assert queue.reclaimed >= 2
    assert len(clock.sleeps) >= 2  # deux cycles complets → branche while-continue couverte


class _ShutdownDuringCycleQueue(FakeQueue):
    """Pose ``shutdown`` au 1er reclaim (PENDANT le cycle) → break sans sleep résiduel."""

    def __init__(self, shutdown: asyncio.Event) -> None:
        super().__init__(claims=[ClaimedTask(task_id=1, ed2k_hash=_A, attempts=1)])
        self._shutdown = shutdown

    def reclaim_expired(self) -> int:
        self._shutdown.set()
        return super().reclaim_expired()


@pytest.mark.asyncio
async def test_loop_breaks_when_shutdown_is_set_during_the_cycle() -> None:
    shutdown = asyncio.Event()
    clock = FakeClock()
    queue = _ShutdownDuringCycleQueue(shutdown)
    deps = _loop_deps(queue=queue, shutdown=shutdown, clock=clock)
    await asyncio.wait_for(verification_loop(deps), timeout=1.0)
    assert queue.reclaimed == 1  # un seul cycle, puis break (shutdown posé pendant)
