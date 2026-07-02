import dataclasses
from dataclasses import FrozenInstanceError

import pytest

from emule_indexer.ports.mule_client import KadStatus, NetworkStatus
from emule_indexer.ports.mule_download_client import (
    DownloadEntry,
    MuleDownloadClient,
    SharedFileEntry,
)


class _StubDownloadClient:
    """Satisfies MuleDownloadClient structurally (without importing it)."""

    def __init__(self) -> None:
        self.links: list[str] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def add_link(self, ed2k_link: str) -> None:
        self.links.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        return (DownloadEntry(ed2k_hash="a" * 32, size_done=5, size_full=10),)

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        return ()

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


def test_shared_file_entry_carries_hash_and_real_name() -> None:
    entry = SharedFileEntry(ed2k_hash="a" * 32, name="Keroro 62a.avi")
    assert entry.ed2k_hash == "a" * 32
    assert entry.name == "Keroro 62a.avi"


def test_shared_file_entry_is_frozen() -> None:
    entry = SharedFileEntry(ed2k_hash="a" * 32, name="x.avi")
    with pytest.raises(FrozenInstanceError):
        entry.name = "y.avi"  # type: ignore[misc]


def test_download_entry_is_frozen() -> None:
    entry = DownloadEntry(ed2k_hash="a" * 32, size_done=5, size_full=10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.size_done = 6  # type: ignore[misc]


def test_is_complete_when_done_reaches_full() -> None:
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=10, size_full=10).is_complete is True
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=11, size_full=10).is_complete is True


def test_is_not_complete_below_full() -> None:
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=9, size_full=10).is_complete is False


def test_zero_full_size_is_never_complete() -> None:
    # size_full == 0 (nascent entry) must NEVER count as complete (otherwise we would
    # promote an empty file). Explicit guard.
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=0, size_full=0).is_complete is False


@pytest.mark.asyncio
async def test_protocol_is_satisfied_structurally() -> None:
    client: MuleDownloadClient = _StubDownloadClient()
    await client.connect()
    await client.add_link("ed2k://|file|x|1|" + "a" * 32 + "|/")
    queue = await client.download_queue()
    status = await client.network_status()
    await client.close()
    assert isinstance(client, _StubDownloadClient)
    assert client.links == ["ed2k://|file|x|1|" + "a" * 32 + "|/"]
    assert queue[0].ed2k_hash == "a" * 32
    assert status.kad_status is KadStatus.CONNECTED
