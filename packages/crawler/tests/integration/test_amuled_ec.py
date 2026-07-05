"""Integration against a REAL amuled (ngosang/amule image, protocol ref. §8).

Dedicated run: uv run pytest -m ec_integration --no-cov
Validates: real auth (hash formula §4 against the real daemon), auth failure, network
status, and the full start/fetch/stop CYCLE — the results may be empty without eD2k
network access: it is the cycle that is validated (spec §7.3), the richness of the real
fields comes from the probe (the deliverable 5 report).
"""

from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from mulewatch.adapters.mule_ec.client import AmuleEcClient
from mulewatch.adapters.mule_ec.errors import EcAuthError, EcFailureError
from mulewatch.ports.mule_client import KadStatus, NetworkStatus, SearchChannel

pytestmark = pytest.mark.ec_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"  # DECISION 10: Docker Hub image from ngosang/docker-amule


@pytest.fixture(scope="module")
def amuled() -> Iterator[tuple[str, int]]:
    # Readiness (ref. §8): "*** TCP socket (ECServer) listening on 0.0.0.0:4712"
    # (ExternalConn.cpp:333). Regex pattern WITHOUT the literal parentheses. The strategy
    # also detects a container that died before the expected line (immediate RuntimeError).
    ready = LogMessageWaitStrategy(r"listening on 0\.0\.0\.0:4712").with_startup_timeout(180)
    container = (
        DockerContainer(_IMAGE)
        .with_env("GUI_PWD", _EC_PASSWORD)
        .with_exposed_ports(4712)
        .waiting_for(ready)
    )
    try:
        container.start()  # readiness is awaited DURING start() (strategy above)
        yield container.get_container_host_ip(), int(container.get_exposed_port(4712))
    finally:
        container.stop()  # safe no-op if the container was never created


@pytest.mark.asyncio
async def test_real_auth_succeeds(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()  # hash formula §4 validated against the REAL daemon
    await client.close()


@pytest.mark.asyncio
async def test_real_auth_fails_with_wrong_password(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, "mauvais-mot-de-passe", timeout=30.0)
    with pytest.raises(EcAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_real_network_status(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        status = await client.network_status()
        assert isinstance(status, NetworkStatus)
        assert status.kad_status in set(KadStatus)  # any real state, but DECODED
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_search_cycle(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
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
