"""``MuleRestarter`` port: restart the amuled container (High-ID port-sync, design §4.1/§5).

PORTS layer. amuled does NOT re-bind its listen port at runtime (socket created once at boot):
after an EC ``set_listen_port``, the container must be RESTARTED so it re-binds the new port.
The restart goes through a minimal-surface docker-socket-proxy (the crawler NEVER sees the
Docker socket). ``RestarterError`` (the proxy refuses/fails) is ABSORBED by the loop (never
fatal → edge-triggered alert + backoff). Stub on ONE line (the ``def`` counts as covered).
"""

from typing import Protocol


class RestarterError(Exception):
    """Restart of the amuled container failed (proxy unreachable / ≠2xx) → absorbed by the loop.

    The loop catches it without importing this adapter (dependency rule §4): alert + backoff.
    """


class MuleRestarter(Protocol):
    async def restart(self) -> None: ...
