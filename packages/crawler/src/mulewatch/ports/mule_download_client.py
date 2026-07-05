"""``MuleDownloadClient`` port: the DOWNLOAD operations expected from an eMule client.

SEPARATE from ``MuleClient`` (ISP, spec download §2.4/§4 — DECISION D3): search does not
depend on the download methods and vice versa. The SAME adapter class (``AmuleEcClient``) can
implement both Protocols STRUCTURALLY; in production, the download connection is a SEPARATE
instance (its own EC connection, spec §2.2). The port imports ONLY the domain and the shared
network DTO ``NetworkStatus`` (already in ``ports/mule_client.py`` — reused, not duplicated:
HighID required to download in full mode).

``DownloadEntry`` is the port DTO (frozen): the crawler NEVER READS the bytes (spec §4);
``download_queue`` only returns EC METADATA. Completion is inferred from
``size_done``/``size_full`` (DECISION D2: EC exposes no portable staging path, so the DTO
carries none — the location for quarantine is derived from a staging configured by the
caller). The ERROR contract is Plan C's: a dead stream raises ``MuleUnreachableError``
(``ports/mule_client.py``) — the application tolerates it (spec §9).
"""

from dataclasses import dataclass
from typing import Protocol

from mulewatch.ports.mule_client import NetworkStatus


@dataclass(frozen=True)
class DownloadEntry:
    """An entry of amuled's download queue (EC metadata ONLY, spec §4).

    ``ed2k_hash`` = content key (lowercase hex 32). ``size_done``/``size_full`` = bytes
    transferred / total size. ``is_complete`` is true ONLY if the total size is known (> 0)
    AND reached — a ``size_full == 0`` (nascent entry) is never complete.
    """

    ed2k_hash: str
    size_done: int
    size_full: int

    @property
    def is_complete(self) -> bool:
        """``True`` if the file is fully transferred on amuled's side (spec §5)."""
        return self.size_full > 0 and self.size_done >= self.size_full


@dataclass(frozen=True)
class SharedFileEntry:
    """An entry of amuled's SHARED files list (``EC_OP_SHARED_FILES`` response).

    A downloaded file is auto-shared by amuled on completion (POSITIVE completion signal,
    cf. design 2026-06-17). ``name`` is the REAL on-disk name (``GetFileName`` on amuled's
    side, post-cleanup AND post-dedup ``name(0).ext``); ``ed2k_hash`` (lowercase hex 32) is
    used to match a tracked download. NO byte is read (EC metadata only, spec §4).
    """

    ed2k_hash: str
    name: str


class MuleDownloadClient(Protocol):
    """Async contract of the download operations (spec §4). UNIT actions: no sleep/retry.

    ``add_link`` adds an ed2k link to amuled's download queue. ``download_queue`` returns a
    snapshot of the queue (hash + progress). ``network_status`` is reused (HighID required to
    download in full mode).
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def add_link(self, ed2k_link: str) -> None: ...

    async def download_queue(self) -> tuple[DownloadEntry, ...]: ...

    async def shared_files(self) -> tuple[SharedFileEntry, ...]: ...

    async def network_status(self) -> NetworkStatus: ...
