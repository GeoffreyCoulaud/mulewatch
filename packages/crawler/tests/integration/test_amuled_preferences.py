"""Listen-port get/set integration against a REAL amuleapi (High-ID port-sync, design §4.2).

Dedicated run: uv run pytest -m api_integration --no-cov

EMPIRICALLY validates against the real daemon:
  - that ``GET /preferences`` really carries ``connection.tcp_port`` on this build;
  - the set -> get ROUND-TRIP: ``set_listen_port(N)`` then ``get_listen_port()`` returns ``N``
    (the preference is updated; the actual re-bind requires a restart, not tested here).

NOTE: the real restart + the real High-ID are covered by the layer-B e2e suite, outside this
file.
"""

import pytest

from mulewatch.adapters.mule_api.client import AmuleApiClient
from tests.integration.conftest import ApiEndpoint

pytestmark = pytest.mark.api_integration


@pytest.mark.asyncio
async def test_real_get_listen_port_reads_a_plausible_port(amuled: ApiEndpoint) -> None:
    client = AmuleApiClient(amuled.host, amuled.port, amuled.password, timeout=30.0)
    await client.connect()
    try:
        listen_port = await client.get_listen_port()
        assert 0 < listen_port < 65536  # a plausible listen port (aMule's default: 4662)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_set_then_get_round_trips_the_port(amuled: ApiEndpoint) -> None:
    # set_listen_port(N) writes the preference; a later get must return N. The actual re-bind
    # (socket) requires a restart: NOT tested here (covered by the layer-B e2e).
    client = AmuleApiClient(amuled.host, amuled.port, amuled.password, timeout=30.0)
    await client.connect()
    try:
        original = await client.get_listen_port()
        target = 51820 if original != 51820 else 51821
        await client.set_listen_port(target)
        assert await client.get_listen_port() == target
        # courtesy: we restore the original port (the preference is persisted by amuled).
        await client.set_listen_port(original)
    finally:
        await client.close()
