"""Verifier error contract (spec verify §5/§8 — DECISION DV6).

PORTS layer: the transient error CONTRACT the verification loop catches lives at the port
level, NEVER at an adapter (dependency rule §4, ``MuleUnreachableError`` pattern). The http
adapter (``HttpContentVerifier``) RAISES ``VerifierUnavailableError`` when the service is
unreachable (connection refused / timeout / 5xx) — a TRANSIENT failure: the loop
``fail_verification`` (lease → retry), invents NO verdict. A merely malformed 200 response is
NOT transient → the adapter returns a ``VerificationResult(verdict="error")``.
"""


class VerifierUnavailableError(Exception):
    """The verifier service is unreachable (transient) → retry by the loop (spec §8)."""
