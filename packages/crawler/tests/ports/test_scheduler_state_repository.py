import dataclasses
from datetime import UTC, datetime

import pytest

from emule_indexer.ports.scheduler_state_repository import (
    ChannelBackoff,
    SchedulerStateRepository,
)


class _StubRepository:
    """Satisfies SchedulerStateRepository structurally (without importing it)."""

    def __init__(self) -> None:
        self.index = 0
        self.writes: list[tuple[int, datetime]] = []
        self.backoff: dict[str, ChannelBackoff] = {}

    def read_cycle_index(self) -> int:
        return self.index

    def write_cycle_state(self, cycle_index: int, last_full_cycle_at: datetime) -> None:
        self.writes.append((cycle_index, last_full_cycle_at))

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
        return dict(self.backoff)

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None:
        self.backoff = dict(backoff)


def test_channel_backoff_is_frozen_and_holds_fields() -> None:
    state = ChannelBackoff(attempts=2, retry_after="2026-06-12T10:05:00.000000+00:00")
    assert state.attempts == 2
    assert state.retry_after == "2026-06-12T10:05:00.000000+00:00"
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.attempts = 3  # type: ignore[misc]


def test_protocol_is_satisfied_structurally() -> None:
    repository: SchedulerStateRepository = _StubRepository()
    assert repository.read_cycle_index() == 0
    moment = datetime(2026, 6, 12, tzinfo=UTC)
    repository.write_cycle_state(3, moment)
    assert repository.load_channel_backoff() == {}
    state = {
        "amule-1:kad": ChannelBackoff(attempts=1, retry_after="2026-06-12T10:00:00.000000+00:00")
    }
    repository.save_channel_backoff(state)
    assert isinstance(repository, _StubRepository)
    assert repository.writes == [(3, moment)]
    assert repository.load_channel_backoff() == state
