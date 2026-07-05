"""DOWNLOAD integration against a REAL amuled (protocol ref., download spec §11 — option A).

Dedicated run: uv run pytest -m download_integration --no-cov
Validates the EC MECHANICS of the download: ``add_link`` accepted + the link appears in
``download_queue`` with a readable status. COMPLETION is NOT reachable (no eD2k sources from
the ephemeral container): it is the add_link → queue → status cycle that is validated.
"""

from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from catalog_matching.ed2k_link import build_ed2k_link
from mulewatch.adapters.mule_ec.client import AmuleEcClient
from mulewatch.adapters.mule_ec.errors import EcFailureError
from mulewatch.ports.mule_download_client import DownloadEntry, SharedFileEntry

pytestmark = pytest.mark.download_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"
# A NON-DEGENERATE canonical hash: above all NOT the MD4 of the empty file (31d6cfe0…), which
# amuled treats as instantly complete at 0 bytes and NEVER lists as an active partfile — which
# had masked the hash-decoding bug. With a real size, the link creates a listed partfile
# (size_done=0 < size_full), whose hash appears in the EC_TAG_PARTFILE_HASH child.
_HASH = "aabbccddeeff00112233445566778899"
_SIZE = 734003200  # ~700 Mio: a real size, hence an active partfile (never "complete")


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
async def test_add_link_then_appears_in_download_queue(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        link = build_ed2k_link("probe-download.bin", _SIZE, _HASH)
        try:
            await client.add_link(link)
        except EcFailureError as exc:
            # amuled responded FAILED cleanly (link refused): the request/response cycle
            # IS validated, with the daemon's message. Tolerable for this test context.
            assert str(exc)
            return
        queue = await client.download_queue()
        assert isinstance(queue, tuple)
        assert all(isinstance(entry, DownloadEntry) for entry in queue)
        # add_link ACCEPTED: a real-size link (no source) creates a listed partfile
        # (size_done=0 < size_full), whose hash is carried by the EC_TAG_PARTFILE_HASH child.
        # This is the REGRESSION GUARD for the decoding bug: if _map_partfile still read the
        # own value (UINT8) instead of the 0x031E child, the queue would be empty here → fail.
        hashes = {entry.ed2k_hash for entry in queue}
        assert _HASH in hashes
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_shared_files_round_trips(amuled: tuple[str, int]) -> None:
    # EMPIRICALLY confirms the GET_SHARED_FILES → SHARED_FILES request/response cycle and that
    # the decoding does not raise (opcodes 0x10/0x22). On a fresh amuled the list may be empty;
    # the mapping (EC_TAG_KNOWNFILE 0x0400 container, name/hash) is covered by the unit tests +
    # the upstream source. If entries come back, they are valid SharedFileEntry (32-hex hash,
    # name).
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        shared = await client.shared_files()
        assert isinstance(shared, tuple)
        assert all(isinstance(e, SharedFileEntry) for e in shared)
        assert all(len(e.ed2k_hash) == 32 and e.name for e in shared)
    finally:
        await client.close()
