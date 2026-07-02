"""Events are frozen dataclasses with business fields — construction/freeze test."""

import dataclasses

import pytest

from emule_indexer.domain.observability.events import (
    ObservationRecorded,
    VerificationCompleted,
)


def test_observation_recorded_carries_network() -> None:
    event = ObservationRecorded(network="ed2k")
    assert event.network == "ed2k"


def test_event_is_frozen() -> None:
    event = VerificationCompleted(target_id="S2E062A", verdict="clean")
    # Pass the attribute via a variable to avoid ruff B010 while still
    # triggering FrozenInstanceError at runtime (frozen=True).
    attr = "verdict"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(event, attr, "malicious")
