"""Listen-port get/set integration against a REAL amuled (High-ID port-sync, design §2/§4.2).

Dedicated run: uv run pytest -m ec_integration --no-cov

EMPIRICALLY validates against the real daemon the points frozen by reading the upstream source:
  - **R3**: does the ``GET_PREFERENCES`` RESPONSE indeed carry the ``EC_OP_SET_PREFERENCES``
    opcode (0x40, NOT 0x3F)? If ``get_listen_port()`` returns a value, it is confirmed (the
    client expects 0x40; a wrong ``expected`` would raise ``EcProtocolError``). Otherwise, adjust
    the ``expected``.
  - **R4**: is ``EC_DETAIL_CMD`` (implicit, we emit NO detail tag) enough to retrieve
    ``EC_TAG_CONN_TCP_PORT``? If the read succeeds, yes.
  - the set→get ROUND-TRIP: ``set_listen_port(N)`` then ``get_listen_port()`` returns ``N`` (the
    pref is updated IN MEMORY; the actual re-bind requires a restart, not tested here).

NOTE: the real restart + the real High-ID are covered by the layer-B e2e suite (WT-e2e), outside
this file (no Docker proxy here).
"""

from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from emule_indexer.adapters.mule_ec.client import AmuleEcClient

pytestmark = pytest.mark.ec_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"  # DECISION 10: Docker Hub image ngosang/docker-amule


@pytest.fixture(scope="module")
def amuled() -> Iterator[tuple[str, int]]:
    ready = LogMessageWaitStrategy(r"listening on 0\.0\.0\.0:4712").with_startup_timeout(180)
    container = (
        DockerContainer(_IMAGE)
        .with_env("GUI_PWD", _EC_PASSWORD)
        .with_exposed_ports(4712)
        .waiting_for(ready)
    )
    try:
        container.start()
        yield container.get_container_host_ip(), int(container.get_exposed_port(4712))
    finally:
        container.stop()


@pytest.mark.asyncio
async def test_real_get_listen_port_reads_a_plausible_port(amuled: tuple[str, int]) -> None:
    # R3 + R4: if get_listen_port returns a value, the response opcode (0x40) AND the detail level
    # (implicit CMD) are CONFIRMED against the real daemon. The image's default port is 4662.
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        listen_port = await client.get_listen_port()
        assert 0 < listen_port < 65536  # a plausible listen port (image default: 4662)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_set_then_get_round_trips_the_port(amuled: tuple[str, int]) -> None:
    # set_listen_port(N) updates the pref IN MEMORY; a later get must return N (proof that
    # SET_PREFERENCES → Apply() did set EC_TAG_CONN_TCP_PORT). The actual re-bind (socket)
    # requires a restart — NOT tested here (covered by the layer-B e2e).
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        original = await client.get_listen_port()
        target = 51820 if original != 51820 else 51821
        await client.set_listen_port(target)
        assert await client.get_listen_port() == target
        # courtesy: we restore the original port (the pref is persisted by amuled).
        await client.set_listen_port(original)
    finally:
        await client.close()
