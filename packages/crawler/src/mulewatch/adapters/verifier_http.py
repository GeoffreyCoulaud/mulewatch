"""``HttpContentVerifier`` adapter: HTTP RPC to the verifier service (verify spec §5/§8).

httpx ``AsyncClient`` on the verifier URL. ``verify`` ``POST /verify {hash, expected}``;
``health`` ``GET /health``. DEFENSIVE PARSING (DECISION DV6) — two failure families:
  - UNREACHABLE service (connection refused / timeout / network / 5xx) → TRANSIENT:
    ``VerifierUnavailableError`` (the ``fail_verification`` loop → retry via lease);
  - MALFORMED / off-schema / oversized 200 response → DETERMINISTIC: we return a
    ``VerificationResult(verdict="error")`` (recorded + ``complete`` — no infinite loop).
The transient-error contract lives in the PORT (``ports/verifier_errors``) — the adapter
inherits/raises it, the application catches it without importing this adapter (dependency rule §4).

``aclose`` closes the httpx client (called by composition at shutdown). The crawler DTO is
``ports.content_verifier.VerificationResult`` — defined independently of the verifier response
(package boundary); this module PROVES the wire contract via its test against the real app.
"""

import json
import logging
from collections.abc import Mapping

import httpx

from mulewatch.ports.content_verifier import VerificationResult
from mulewatch.ports.verifier_errors import VerifierUnavailableError

_logger = logging.getLogger("mulewatch.adapters.verifier_http")

# SANITY/schema cap on an ALREADY-received response: a NO-OP /verify returns a tiny body,
# so an oversized body is necessarily abnormal → ``verdict="error"`` (defensive parsing, §8).
# This is NOT a memory/DoS defense: ``_parse`` reads ``response.content``, which materializes the
# whole body in memory BEFORE the size check (httpx buffers the response on receipt). A real
# streaming bound (capped streaming read) belongs to deployment hardening (Plan F).
_DEFAULT_MAX_RESPONSE_BYTES = 65536

_ERROR_RESULT = VerificationResult(verdict="error", real_meta={}, checks=())


class HttpContentVerifier:
    """httpx implementation of the ``ContentVerifier`` port (STRUCTURAL satisfaction)."""

    def __init__(
        self, client: httpx.AsyncClient, *, max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES
    ) -> None:
        self._client = client
        self._max_response_bytes = max_response_bytes

    async def verify(self, ed2k_hash: str, expected: Mapping[str, object]) -> VerificationResult:
        """``POST /verify``; unreachable→``VerifierUnavailableError``; bad response→error."""
        try:
            response = await self._client.post(
                "/verify", json={"hash": ed2k_hash, "expected": dict(expected)}
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            # 4xx/5xx: a 5xx is transient; a 4xx (our payload rejected) is a contract bug —
            # in both cases we do not fabricate a verdict, we surface transient
            # (the 4xx will not resolve on retry but ends up in dead_letter, visible, §8).
            raise VerifierUnavailableError(
                f"verifier replied {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise VerifierUnavailableError(f"verifier unreachable ({error})") from error
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> VerificationResult:
        """Defensive parse of a 200: malformed/off-schema/oversized → ``error`` verdict."""
        body = response.content
        if len(body) > self._max_response_bytes:
            _logger.warning("verifier reply too large (%d B) — verdict error", len(body))
            return _ERROR_RESULT
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            _logger.warning("non-JSON verifier reply — verdict error")
            return _ERROR_RESULT
        if not isinstance(payload, dict):
            return _ERROR_RESULT
        verdict = payload.get("verdict")
        if not isinstance(verdict, str):
            return _ERROR_RESULT
        real_meta = payload.get("real_meta", {})
        checks = payload.get("checks", [])
        if not isinstance(real_meta, dict) or not isinstance(checks, list):
            return _ERROR_RESULT
        return VerificationResult(verdict=verdict, real_meta=real_meta, checks=tuple(checks))

    async def health(self) -> bool:
        """``GET /health``; ``True`` iff 2xx, ``False`` on any failure (full-mode gate, §7)."""
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    async def aclose(self) -> None:
        """Closes the httpx client (called by composition at shutdown)."""
        await self._client.aclose()
