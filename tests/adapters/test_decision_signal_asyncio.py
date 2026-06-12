import asyncio

import pytest

from emule_indexer.adapters.decision_signal_asyncio import AsyncioDecisionSignal


@pytest.mark.asyncio
async def test_signal_wakes_a_waiter() -> None:
    hub = AsyncioDecisionSignal()
    waiter = asyncio.create_task(hub.wait("S2E062A"))
    await asyncio.sleep(0)
    assert not waiter.done()
    hub.signal("S2E062A")
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()


@pytest.mark.asyncio
async def test_signal_before_wait_is_not_lost() -> None:
    # Un nudge émis SANS waiter laisse l'événement armé : le wait suivant repart aussitôt.
    hub = AsyncioDecisionSignal()
    hub.signal("S2E062A")
    await asyncio.wait_for(hub.wait("S2E062A"), timeout=1.0)  # ne bloque pas


@pytest.mark.asyncio
async def test_wait_rearms_for_the_next_signal() -> None:
    hub = AsyncioDecisionSignal()
    hub.signal("h")
    await asyncio.wait_for(hub.wait("h"), timeout=1.0)
    # Re-dort : plus de signal en attente → le wait suivant ne se résout pas tout seul.
    second = asyncio.create_task(hub.wait("h"))
    await asyncio.sleep(0)
    assert not second.done()
    hub.signal("h")
    await asyncio.wait_for(second, timeout=1.0)


@pytest.mark.asyncio
async def test_subjects_are_independent() -> None:
    hub = AsyncioDecisionSignal()
    waiter_a = asyncio.create_task(hub.wait("a"))
    await asyncio.sleep(0)
    hub.signal("b")  # autre sujet : ne réveille pas a
    await asyncio.sleep(0)
    assert not waiter_a.done()
    hub.signal("a")
    await asyncio.wait_for(waiter_a, timeout=1.0)
