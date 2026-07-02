"""The dispatcher: log + metrics always; notification by audience, failure/timeout absorbed."""

import asyncio
import logging

import pytest

from emule_indexer.adapters.observability.dispatcher import ObservabilityDispatcher
from emule_indexer.domain.observability import events as ev
from emule_indexer.domain.observability.policy import Audience, MetricInstruction, Severity
from emule_indexer.ports.telemetry import MetricsSink, Notifier


class _RecordingSink:
    def __init__(self) -> None:
        self.applied: list[MetricInstruction] = []

    def apply(self, instruction: MetricInstruction) -> None:
        self.applied.append(instruction)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[Audience, str, Severity]] = []

    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        self.calls.append((audience, body, severity))


class _RaisingNotifier:
    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        raise RuntimeError("canal mort")


class _HangingNotifier:
    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        await asyncio.sleep(10)  # exceeds the test's short timeout


def _dispatcher(
    sink: MetricsSink, notifier: Notifier, timeout: float = 5.0
) -> ObservabilityDispatcher:
    # _RecordingSink/_RecordingNotifier/… structurally satisfy MetricsSink/Notifier.
    return ObservabilityDispatcher(metrics=sink, notifier=notifier, notify_timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_logs_and_applies_metrics_no_audience() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(ev.ObservationRecorded(network="ed2k"))
    assert [m.name.value for m in sink.applied] == ["emule_observations"]
    assert notifier.calls == []  # ObservationRecorded has no audience


@pytest.mark.asyncio
async def test_two_metrics_one_event() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(
        ev.SearchCycleCompleted(cycle_index=1, duration_seconds=2.0)
    )
    assert [m.name.value for m in sink.applied] == [
        "emule_search_cycles",
        "emule_search_cycle_duration_seconds",
    ]


@pytest.mark.asyncio
async def test_notifies_both_audiences() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(ev.CrawlerStarted(mode="full"))
    assert {a for a, _, _ in notifier.calls} == {Audience.COMMUNITY, Audience.OPERATIONS}


@pytest.mark.asyncio
async def test_log_level_matches_severity(caplog: pytest.LogCaptureFixture) -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    with caplog.at_level(logging.DEBUG, logger="emule_indexer.observability"):
        await _dispatcher(sink, notifier).emit(ev.InstanceUnreachable(instance="amule-1"))
    assert caplog.records[-1].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_notification_failure_is_absorbed(caplog: pytest.LogCaptureFixture) -> None:
    sink = _RecordingSink()
    with caplog.at_level(logging.WARNING, logger="emule_indexer.observability"):
        await _dispatcher(sink, _RaisingNotifier()).emit(ev.DownloadCompleted("S2E062A", "a" * 32))
    assert sink.applied  # the metric went through despite the notification failure
    assert any("failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_notification_timeout_is_absorbed() -> None:
    sink = _RecordingSink()
    # short timeout + hanging notifier → wait_for raises TimeoutError, absorbed.
    await _dispatcher(sink, _HangingNotifier(), timeout=0.01).emit(
        ev.DownloadCompleted("S2E062A", "a" * 32)
    )
    assert sink.applied
