"""amuleapi adapter error hierarchy (spec amuleapi §4.1; orchestration §7).

The adapter SIGNALS, it does not decide, exactly as the EC adapter did: no hidden retry, no
silent crash. What changed is that "the daemon is unreachable" is now TWO states, because
amuleapi is a hop of its own: it can be up while its link to amuled is down (``503
ec_unavailable``). Both degrade the same way here, and ``GET /health`` is what tells them apart
for the operator.

The error CONTRACT consumed by the application lives in the PORT (``ports/mule_client.py``);
the classes below INHERIT from it (adapter->port dependency, allowed) so the application NEVER
depends on this adapter. The mapping: an operation the daemon refused -> ``MuleSearchFailed
Error`` (channel backoff); anything else, transport included -> ``MuleUnreachableError``
(instance down); a login refused stays outside the loop contract (config problem, fail-fast).
"""

import json

import httpx

from mulewatch.ports.mule_client import (
    MuleClientError,
    MuleSearchFailedError,
    MuleUnreachableError,
)

# Error codes that report a failed OPERATION rather than a failed daemon: the request is dead,
# the transport is not. `not_found` is the search evicted from amuled's 20-entry ring (§7.2).
_OPERATION_CODES = frozenset({"amuled_rejected", "not_found"})


class ApiError(MuleClientError):
    """Base of all amuleapi adapter errors (under the port contract)."""


class ApiUnreachableError(ApiError, MuleUnreachableError):
    """Transport dead, amuleapi down, or its EC link to amuled down -> instance down."""


class ApiAuthError(ApiError):
    """Login refused (wrong admin password) - not a loop case, a config one."""


class ApiRejectedError(ApiError, MuleSearchFailedError):
    """The daemon refused the operation and said so cleanly; carries its message."""


def error_from_response(response: httpx.Response) -> ApiError:
    """A non-2xx response -> the adapter error the port contract calls for."""
    code, message = _envelope(response)
    detail = (
        f"{response.request.method} {response.request.url.path}: "
        f"{response.status_code} {code}: {message}"
    )
    if code == "rate_limited":
        # The adapter never sleeps (the caller owns the backoff), so honouring Retry-After
        # means surfacing it rather than acting on it.
        detail += f" (retry after {response.headers.get('Retry-After', 'an unstated delay')}s)"
    if code in _OPERATION_CODES:
        return ApiRejectedError(detail)
    return ApiUnreachableError(detail)


def _envelope(response: httpx.Response) -> tuple[str, str]:
    """``{"error": {code, message}}`` -> the pair, or placeholders if the body is not one."""
    try:
        payload = json.loads(response.content)
    except (json.JSONDecodeError, ValueError):
        return "", response.reason_phrase
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "", response.reason_phrase
    code = error.get("code")
    message = error.get("message")
    return (
        code if isinstance(code, str) else "",
        message if isinstance(message, str) else response.reason_phrase,
    )
