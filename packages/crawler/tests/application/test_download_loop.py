import asyncio
from datetime import UTC, datetime

import pytest

from emule_indexer.application.run_download_cycle import (
    DOWNLOAD_NUDGE_SUBJECT,
    DownloadLoopDeps,
    download_loop,
)
from emule_indexer.domain.matching.engine import DownloadCandidate
from emule_indexer.ports.catalog_repository import ObservedFile

# Réutilise les fakes de test_run_download_cycle (importés explicitement).
from tests.application.test_run_download_cycle import (
    _TARGETS,
    FakeCatalogReads,
    FakeClock,
    FakeDownloadClient,
    FakeDownloadRepo,
    FakeLocalRepo,
    FakeQuarantine,
)


class RecordingSignal:
    """Hub de nudge enregistrant + réveillant (le test EST le producteur)."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self.waited: list[str] = []

    def signal(self, subject: str) -> None:
        self._events.setdefault(subject, asyncio.Event()).set()

    async def wait(self, subject: str) -> None:
        self.waited.append(subject)
        event = self._events.setdefault(subject, asyncio.Event())
        await event.wait()
        event.clear()


def _loop_deps(
    *, signal: RecordingSignal, shutdown: asyncio.Event, poll_interval: float = 30.0
) -> DownloadLoopDeps:
    from pathlib import Path

    return DownloadLoopDeps(
        client=FakeDownloadClient(),
        quarantine=FakeQuarantine(),
        downloads=FakeDownloadRepo(),
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
        targets=_TARGETS,
        disk_cap_bytes=1_000_000,
        staging_path_for=lambda entry: Path("/staging") / entry.ed2k_hash,
        clock=FakeClock(),
        signal=signal,
        poll_interval_seconds=poll_interval,
        shutdown=shutdown,
    )


@pytest.mark.asyncio
async def test_loop_stops_when_shutdown_is_set_before_start() -> None:
    shutdown = asyncio.Event()
    shutdown.set()
    deps = _loop_deps(signal=RecordingSignal(), shutdown=shutdown)
    await asyncio.wait_for(download_loop(deps), timeout=1.0)  # ne tourne aucun cycle


@pytest.mark.asyncio
async def test_loop_runs_a_cycle_then_sleeps_then_stops() -> None:
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown)

    async def stop_after_first_sleep() -> None:
        # laisse un cycle + l'entrée en attente, puis demande l'arrêt et réveille la boucle.
        while not deps.clock.sleeps:  # type: ignore[attr-defined]
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)

    await asyncio.gather(
        asyncio.wait_for(download_loop(deps), timeout=2.0), stop_after_first_sleep()
    )
    assert deps.clock.sleeps  # type: ignore[attr-defined]  # au moins un sleep de poll


@pytest.mark.asyncio
async def test_nudge_wakes_the_loop_before_poll_expires() -> None:
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown, poll_interval=999.0)

    async def nudge_then_stop() -> None:
        while DOWNLOAD_NUDGE_SUBJECT not in signal.waited:
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)  # réveille l'attente avant les 999 s de poll

    await asyncio.gather(asyncio.wait_for(download_loop(deps), timeout=2.0), nudge_then_stop())
    assert DOWNLOAD_NUDGE_SUBJECT in signal.waited


class _ShutdownDuringCycleCatalog:
    """CatalogReader qui pose ``shutdown`` au 1er ``download_decisions`` (PENDANT le cycle).

    Satisfait STRUCTURELLEMENT ``CatalogReader`` (download_decisions + last_observation).
    """

    def __init__(self, shutdown: asyncio.Event) -> None:
        self._shutdown = shutdown

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        self._shutdown.set()
        return ()

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return None


@pytest.mark.asyncio
async def test_loop_breaks_when_shutdown_is_set_during_the_cycle() -> None:
    # le `if deps.shutdown.is_set(): break` APRÈS le cycle : shutdown posé PENDANT le cycle
    # (par le catalog) → break sans appeler _sleep_or_nudge (aucun sleep enregistré).
    shutdown = asyncio.Event()
    clock = FakeClock()
    deps = _loop_deps(signal=RecordingSignal(), shutdown=shutdown)
    deps.clock = clock
    deps.catalog = _ShutdownDuringCycleCatalog(shutdown)
    await asyncio.wait_for(download_loop(deps), timeout=1.0)
    assert clock.sleeps == []  # break avant tout sleep/nudge


class _BlockingClock:
    """Clock dont ``sleep`` BLOQUE pour de bon (le nudge doit gagner et annuler le sleep).

    Satisfait STRUCTURELLEMENT ``Clock`` : ``now`` aware (non utilisé par la boucle) + ``sleep``
    qui ne se résout jamais → le nudge gagne et le sleep_task pendant est annulé.
    """

    def now(self) -> datetime:
        return datetime(2026, 6, 13, tzinfo=UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.Event().wait()  # ne se résout JAMAIS


@pytest.mark.asyncio
async def test_nudge_wins_and_cancels_the_pending_sleep() -> None:
    # _sleep_or_nudge : nudge PRÉ-armé → la branche `if not task.done(): task.cancel()` est
    # exercée sur le sleep_task encore en cours (le _BlockingClock ne le résout jamais).
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown)
    deps.clock = _BlockingClock()
    signal.signal(DOWNLOAD_NUDGE_SUBJECT)  # nudge déjà armé → wait() repart aussitôt

    async def stop_when_waited() -> None:
        while DOWNLOAD_NUDGE_SUBJECT not in signal.waited:
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)

    await asyncio.gather(asyncio.wait_for(download_loop(deps), timeout=2.0), stop_when_waited())
    assert DOWNLOAD_NUDGE_SUBJECT in signal.waited


@pytest.mark.asyncio
async def test_loop_cancelled_mid_wait_propagates_cleanly() -> None:
    # Le vrai déclencheur d'arrêt en prod : le TaskGroup de CrawlerApp annule la tâche de
    # boucle PENDANT qu'elle dort dans _sleep_or_nudge. _BlockingClock + aucun nudge → la
    # boucle se gare sur asyncio.wait ; l'annulation y atterrit, le finally annule les enfants,
    # et le CancelledError se propage proprement (la boucle s'arrête, sans tâche en fuite).
    shutdown = asyncio.Event()  # JAMAIS posé : seule l'annulation arrête la boucle
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown)
    deps.clock = _BlockingClock()
    task = asyncio.ensure_future(download_loop(deps))
    # attendre que la boucle soit garée dans _sleep_or_nudge (nudge_task a appelé wait())
    while DOWNLOAD_NUDGE_SUBJECT not in signal.waited:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
