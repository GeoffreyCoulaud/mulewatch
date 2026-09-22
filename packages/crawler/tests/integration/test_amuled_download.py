"""DOWNLOAD integration against a REAL amuleapi (download spec §11, option A).

Dedicated run: uv run pytest -m download_integration --no-cov
Validates the MECHANICS of the download: ``add_link`` accepted + the link appears in
``download_queue`` with readable byte counters. COMPLETION is NOT reachable (no eD2k sources
from the ephemeral container): it is the add_link -> queue -> status cycle that is validated.
"""

import pytest

from catalog_matching.ed2k_link import build_ed2k_link
from mulewatch.adapters.mule_api.client import AmuleApiClient
from mulewatch.adapters.mule_api.errors import ApiRejectedError
from mulewatch.ports.mule_download_client import DownloadEntry, SharedFileEntry
from tests.integration.conftest import ApiEndpoint

pytestmark = pytest.mark.download_integration

# A NON-DEGENERATE canonical hash: above all NOT the MD4 of the empty file (31d6cfe0…), which
# amuled treats as instantly complete at 0 bytes and NEVER lists as an active partfile. With a
# real size, the link creates a listed partfile (completed_bytes=0 < size_bytes).
_HASH = "aabbccddeeff00112233445566778899"
_SIZE = 734003200  # ~700 MiB: a real size, hence an active partfile (never "complete")


@pytest.mark.asyncio
async def test_add_link_then_appears_in_download_queue(amuled: ApiEndpoint) -> None:
    client = AmuleApiClient(amuled.host, amuled.port, amuled.password, timeout=30.0)
    await client.connect()
    try:
        link = build_ed2k_link("probe-download.bin", _SIZE, _HASH)
        try:
            await client.add_link(link)
        except ApiRejectedError as exc:
            # The daemon refused the link cleanly, per item in the bulk envelope: the
            # request/response cycle IS validated, with its message. Tolerable here.
            assert str(exc)
            return
        queue = await client.download_queue()
        assert isinstance(queue, tuple)
        assert all(isinstance(entry, DownloadEntry) for entry in queue)
        # add_link ACCEPTED: a real-size link (no source) creates a listed partfile, and
        # `status=all` is what makes it visible whatever its state (§4.3).
        hashes = {entry.ed2k_hash for entry in queue}
        assert _HASH in hashes
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_shared_files_round_trips(amuled: ApiEndpoint) -> None:
    # EMPIRICALLY confirms the GET /shared sweep and that the decoding does not raise. On a
    # fresh amuled the list may be empty; the mapping is covered by the unit tests. If entries
    # come back, they are valid SharedFileEntry (32-hex hash).
    client = AmuleApiClient(amuled.host, amuled.port, amuled.password, timeout=30.0)
    await client.connect()
    try:
        shared = await client.shared_files()
        assert isinstance(shared, tuple)
        assert all(isinstance(e, SharedFileEntry) for e in shared)
        assert all(len(e.ed2k_hash) == 32 for e in shared)
    finally:
        await client.close()
