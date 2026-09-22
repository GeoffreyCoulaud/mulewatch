"""What the crawler expects from an eMule client, plus the error contract it decides on.

``MuleUnreachableError`` means the instance is down (reconnect), ``MuleSearchFailedError`` that
one channel failed (backoff). The adapter's errors inherit them, so the application never
imports an adapter. Protocol stubs stay on ONE line: a body on a second one is an uncovered
branch.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mulewatch.domain.observation import FileObservation


class MuleClientError(Exception):
    """Base of the eMule client's error contract (spec orchestration §7)."""


class MuleUnreachableError(MuleClientError):
    """The daemon is unreachable, at either hop → reconnection by the caller (§7)."""


class MuleSearchFailedError(MuleClientError):
    """Application failure of a search reported by the daemon → channel backoff (§7)."""


class SearchChannel(StrEnum):
    """eD2k servers or Kad. The values are the tokens ``POST /search`` takes verbatim."""

    GLOBAL = "global"
    KAD = "kad"


class KadStatus(StrEnum):
    """Kad state (closed enum), read from ``GET /status``'s ``kad`` object."""

    OFF = "off"
    RUNNING = "running"
    CONNECTED = "connected"
    FIREWALLED = "firewalled"


@dataclass(frozen=True)
class NetworkStatus:
    """``ed2k_id`` is ``None`` while not connected to a server, which is what keeps a false
    ``ed2k_high`` (LowID, id < 16777216) apart from "no id yet"."""

    ed2k_id: int | None
    ed2k_high: bool
    kad_status: KadStatus
    server_name: str | None = None
    server_addr: str | None = None


class MuleClient(Protocol):
    """UNIT actions only: no sleep, no retry, no loop. ``fetch_results`` returns the daemon's
    CUMULATIVE snapshot, and ``search_progress`` is ``None`` when it reports no percentage."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def start_search(self, keyword: str, channel: SearchChannel) -> None: ...

    async def fetch_results(self) -> tuple[FileObservation, ...]: ...

    async def stop_search(self) -> None: ...

    async def search_progress(self) -> int | None: ...

    async def network_status(self) -> NetworkStatus: ...
