"""EC adapter error hierarchy (cf. EC-adapter spec §6; orchestration §7).

The adapter SIGNALS, it does not decide: no hidden retry, no silent crash. This
hierarchy lets the caller (plan C) tell "amuled is down" (EcConnectError) apart
from "my config is wrong" (EcAuthError), an unreadable frame (EcProtocolError) from an
application failure cleanly signalled by the daemon (EcFailureError).

The error CONTRACT consumed by the application lives in the PORT (``ports/mule_client.py``:
``MuleUnreachableError``/``MuleSearchFailedError``); the EC classes below INHERIT from it
(adapter→port dependency, allowed) so the application NEVER depends on this adapter
(dependency rule, orchestration spec §4). The mapping: dead stream (connection/timeout/
unreadable frame) → ``MuleUnreachableError``; ``EC_OP_FAILED`` → ``MuleSearchFailedError``;
AUTH failure stays outside the loop contract (config problem, fail-fast at startup).
"""

from emule_indexer.ports.mule_client import (
    MuleClientError,
    MuleSearchFailedError,
    MuleUnreachableError,
)


class EcError(MuleClientError):
    """Base of all EC adapter errors (under the port contract)."""


class EcConnectError(EcError, MuleUnreachableError):
    """TCP refused, connection lost, or operation with no established connection."""


class EcAuthError(EcError):
    """Authentication refused (password or protocol version) — not a loop case."""


class EcProtocolError(EcError, MuleUnreachableError):
    """Malformed frame or unexpected response (network input is untrusted) → dead stream."""


class EcTimeoutError(EcError, MuleUnreachableError):
    """Timeout (network read or connection establishment) → dead stream."""


class EcFailureError(EcError, MuleSearchFailedError):
    """Application failure signalled by the daemon (EC_OP_FAILED); carries its message."""
