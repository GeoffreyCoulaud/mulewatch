"""``MuleClient`` port: what the crawler expects from an eMule client (cf. spec adapter §4).

The port imports ONLY the domain. The Protocol stubs fit on ONE line (the ``def`` runs at
class creation: covered). Polling belongs to the caller (spec §3), so no ``search_and_wait``
convenience lives here.

The port also declares the client's ERROR CONTRACT (spec orchestration §7, "the client
reports, plan C decides"): ``MuleUnreachableError`` (the daemon is out of reach → reconnection
by the caller) vs ``MuleSearchFailedError`` (application failure of a channel → backoff). The
adapter makes its ``ApiError`` inherit from these classes (adapter→port dependency, allowed),
so that the APPLICATION NEVER depends on an adapter (dependency rule §4).
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
    """Search channel (closed enum, spec §4): eD2k servers or Kad.

    The values are the tokens ``POST /search`` takes for its ``type`` field.
    """

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
    """Network status (spec §4) — exactly what the metrics (§13 MVP) will consume.

    ``ed2k_id`` is ``None`` when the client is not connected to an eD2k server.
    ``ed2k_high``: ``True`` = HighID (reachable), ``False`` = LowID,
    i.e. ID < 16777216 (HIGHEST_LOWID_ED2K_KAD).
    """

    ed2k_id: int | None
    ed2k_high: bool
    kad_status: KadStatus
    server_name: str | None = None
    server_addr: str | None = None


class MuleClient(Protocol):
    """Async contract of the eMule client. UNIT actions: no sleep/retry/loop here.

    ``fetch_results`` returns the CUMULATIVE snapshot accumulated by the daemon;
    ``search_progress`` returns a percentage if the daemon reports one, otherwise ``None``.
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def start_search(self, keyword: str, channel: SearchChannel) -> None: ...

    async def fetch_results(self) -> tuple[FileObservation, ...]: ...

    async def stop_search(self) -> None: ...

    async def search_progress(self) -> int | None: ...

    async def network_status(self) -> NetworkStatus: ...
