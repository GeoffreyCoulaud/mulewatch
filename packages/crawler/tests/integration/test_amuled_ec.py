"""Integration against a REAL amuled (ngosang/amule image, protocol ref. §8).

Dedicated run: uv run pytest -m ec_integration --no-cov
Validates: real auth (hash formula §4 against the real daemon), auth failure, network
status, and the full start/fetch/stop CYCLE. The results may be empty without eD2k
network access: it is the cycle that is validated (spec §7.3), the richness of the real
fields comes from the probe (the deliverable 5 report).
"""

import pytest

from mulewatch.adapters.mule_ec.client import AmuleEcClient
from mulewatch.adapters.mule_ec.errors import EcAuthError, EcFailureError
from mulewatch.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
from tests.integration.conftest import EcEndpoint

pytestmark = pytest.mark.ec_integration


@pytest.mark.asyncio
async def test_real_auth_succeeds(amuled: EcEndpoint) -> None:
    client = AmuleEcClient(amuled.host, amuled.port, amuled.password, timeout=30.0)
    await client.connect()  # hash formula §4 validated against the REAL daemon
    await client.close()


@pytest.mark.asyncio
async def test_real_auth_fails_with_wrong_password(amuled: EcEndpoint) -> None:
    client = AmuleEcClient(amuled.host, amuled.port, "wrong-password", timeout=30.0)
    with pytest.raises(EcAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_real_network_status(amuled: EcEndpoint) -> None:
    client = AmuleEcClient(amuled.host, amuled.port, amuled.password, timeout=30.0)
    await client.connect()
    try:
        status = await client.network_status()
        assert isinstance(status, NetworkStatus)
        assert status.kad_status in set(KadStatus)  # any real state, but DECODED
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_search_cycle(amuled: EcEndpoint) -> None:
    client = AmuleEcClient(amuled.host, amuled.port, amuled.password, timeout=30.0)
    await client.connect()
    try:
        try:
            await client.start_search("keroro", SearchChannel.GLOBAL)
        except EcFailureError as exc:
            # amuled responded EC_OP_FAILED cleanly (no eD2k server reachable
            # from the container): the application request/response cycle IS validated,
            # with the daemon's forwarded message (spec §6, DECISION 10).
            assert str(exc)
            return
        progress = await client.search_progress()
        assert progress is None or 0 <= progress <= 100
        results = await client.fetch_results()  # possibly empty: the CYCLE is what counts
        assert isinstance(results, tuple)
        await client.stop_search()
    finally:
        await client.close()
