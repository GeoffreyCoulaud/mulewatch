"""``ContentVerifier`` port: verification of a quarantined file (spec verify §5).

ASYNC Protocol (the adapter makes an HTTP RPC). ``verify`` returns a ``VerificationResult``
(frozen DTO); ``health`` a boolean (liveness, for the full-mode startup gate, §7). The port
imports NOTHING from the verifier (package boundary, DECISION DV4): the ``VerificationResult``
DTO is defined HERE, independently of the service's result shape; the JSON wire contract keeps
them in sync (contract test + e2e). The ``health`` stub fits on ONE line; ``verify`` is
WRAPPED (signature > 100 cols on one line → ruff E501) but KEEPS the final ``: ...`` on the
``->`` line (coverage idiom: the ``def`` runs at class creation).

``verify`` does NOT RAISE for a deterministic bad response (→ ``VerificationResult(verdict=
"error")``, recorded); it RAISES ``VerifierUnavailableError`` (``ports/verifier_errors``)
only when the service is unreachable (transient → retry), DECISION DV6.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VerificationResult:
    """Result of a verification (port DTO, spec §5).

    ``verdict``: string (in NO-OP: ``unverified``/``error``; D-analysis will add ``clean``/
    ``suspicious``/``malicious``). ``real_meta``: extracted media metadata (empty in NO-OP).
    ``checks``: trace of the checks run (empty in NO-OP). Frozen → value comparison in tests.
    These three fields are EXACTLY the columns ``file_verifications`` persists (verdict/
    real_meta/checks) — ``verified_at``/``node_id`` are stamped by the adapter (not the domain).
    """

    verdict: str
    real_meta: Mapping[str, object]
    checks: tuple[object, ...]


class ContentVerifier(Protocol):
    """Async verification contract (spec §5). ``verify`` RPC; ``health`` liveness (gate §7)."""

    async def verify(
        self, ed2k_hash: str, expected: Mapping[str, object]
    ) -> VerificationResult: ...

    async def health(self) -> bool: ...
