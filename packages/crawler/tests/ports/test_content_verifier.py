import dataclasses
from collections.abc import Mapping

import pytest

from emule_indexer.ports.content_verifier import ContentVerifier, VerificationResult
from emule_indexer.ports.verifier_errors import VerifierUnavailableError


class _StubVerifier:
    """Satisfies ContentVerifier structurally (without importing it)."""

    def __init__(self) -> None:
        self.verified: list[tuple[str, Mapping[str, object]]] = []

    async def verify(self, ed2k_hash: str, expected: Mapping[str, object]) -> VerificationResult:
        self.verified.append((ed2k_hash, expected))
        return VerificationResult(verdict="unverified", real_meta={}, checks=())

    async def health(self) -> bool:
        return True


def test_result_is_frozen() -> None:
    result = VerificationResult(verdict="unverified", real_meta={}, checks=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.verdict = "error"  # type: ignore[misc]


def test_result_carries_verdict_meta_checks() -> None:
    result = VerificationResult(verdict="error", real_meta={"k": 1}, checks=("type_sniff",))
    assert result.verdict == "error"
    assert result.real_meta == {"k": 1}
    assert result.checks == ("type_sniff",)


def test_unavailable_error_is_an_exception() -> None:
    assert issubclass(VerifierUnavailableError, Exception)


@pytest.mark.asyncio
async def test_protocol_is_satisfied_structurally() -> None:
    verifier: ContentVerifier = _StubVerifier()
    result = await verifier.verify("a" * 32, {"target_id": "062A"})
    assert await verifier.health() is True
    assert result.verdict == "unverified"
    assert isinstance(verifier, _StubVerifier)
    assert verifier.verified == [("a" * 32, {"target_id": "062A"})]
