from datetime import UTC, datetime

import pytest

from emule_indexer.ports.clock import Clock, Rng


class _StubClock:
    """Satisfait Clock structurellement (sans l'importer)."""

    def now(self) -> datetime:
        return datetime(2026, 6, 12, tzinfo=UTC)

    async def sleep(self, seconds: float) -> None:
        return None


class _StubRng:
    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        return 0.0


def test_clock_protocol_is_satisfied_structurally() -> None:
    clock: Clock = _StubClock()
    assert clock.now() == datetime(2026, 6, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_clock_sleep_is_awaitable() -> None:
    clock: Clock = _StubClock()
    await clock.sleep(1.0)  # ne lève pas ; rend None (contrat)


def test_rng_is_reexported_from_the_domain() -> None:
    rng: Rng = _StubRng()
    assert rng.shuffled(("a", "b"), "seed") == ("a", "b")
    assert rng.jitter(5.0) == 0.0
