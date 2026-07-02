"""``MuleClient`` port: what the crawler expects from an eMule client (cf. spec EC-adapter §4).

The port imports ONLY the domain. The Protocol stubs fit on ONE line (the ``def`` runs at
class creation: covered). The ``search_and_wait`` convenience (poll + timeout) lives in the
probe tool, NOT here: polling belongs to the caller (spec §3).

The port also declares the client's ERROR CONTRACT (spec orchestration §7, "the client
reports, plan C decides"): ``MuleUnreachableError`` (dead stream → reconnection by the
caller) vs ``MuleSearchFailedError`` (application failure of a channel → backoff). The EC
adapter makes its ``EcError`` inherit from these classes (adapter→port dependency, allowed),
so that the APPLICATION NEVER depends on an adapter (dependency rule §4).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from emule_indexer.domain.observation import FileObservation


class MuleClientError(Exception):
    """Base of the eMule client's error contract (spec orchestration §7)."""


class MuleUnreachableError(MuleClientError):
    """The daemon is unreachable or the stream is dead → reconnection by the caller (§7)."""


class MuleSearchFailedError(MuleClientError):
    """Application failure of a search reported by the daemon → channel backoff (§7)."""


class SearchChannel(StrEnum):
    """Search channel (closed enum, spec §4): eD2k servers or Kad."""

    GLOBAL = "global"
    KAD = "kad"


class KadStatus(StrEnum):
    """Kad state (closed enum), decoded from the CONNSTATE bitfield (protocol ref. §6)."""

    OFF = "off"
    RUNNING = "running"
    CONNECTED = "connected"
    FIREWALLED = "firewalled"


@dataclass(frozen=True)
class NetworkStatus:
    """Network status (spec §4) — exactly what the metrics (§13 MVP) will consume.

    ``ed2k_id`` is ``None`` when the client is not connected to an eD2k server.
    ``ed2k_high``: ``True`` = HighID (reachable), ``False`` = LowID,
    i.e. ID < 16777216 (HIGHEST_LOWID_ED2K_KAD, ref. §6).
    """

    ed2k_id: int | None
    ed2k_high: bool
    kad_status: KadStatus
    server_name: str | None = None
    server_addr: str | None = None


class MuleClient(Protocol):
    """Async contract of the eMule client. UNIT actions: no sleep/retry/loop here.

    ``fetch_results`` returns the CUMULATIVE snapshot accumulated by the daemon (ref. §5);
    ``search_progress`` returns a percentage if EC exposes it, otherwise ``None``.
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def start_search(self, keyword: str, channel: SearchChannel) -> None: ...

    async def fetch_results(self) -> tuple[FileObservation, ...]: ...

    async def stop_search(self) -> None: ...

    async def search_progress(self) -> int | None: ...

    async def network_status(self) -> NetworkStatus: ...
