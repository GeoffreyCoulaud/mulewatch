"""Tests de l'adapter ``HttpContentVerifier`` (spec verify §5/§8 — DÉCISION DV6).

Deux familles :
- Tests de CONTRAT (vraie app Starlette via ``ASGITransport``) : prouvent le contrat de fil
  DTO↔réponse sans socket/Docker (DÉCISION DV4).
- Tests fabriqués (``MockTransport``) : couvrent le parsing défensif, les erreurs réseau, etc.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import httpx
import pytest

import download_verifier.check as check_module
from download_verifier.app import build_app
from download_verifier.config import AnalysisConfig
from emule_indexer.adapters.verifier_http import HttpContentVerifier
from emule_indexer.ports.content_verifier import VerificationResult
from emule_indexer.ports.verifier_errors import VerifierUnavailableError

_HASH = "a" * 32


class _FakeProdChildRunner:
    """Faux ``ProdChildRunner`` (signature conforme au Protocol ``ChildRunner``) : rend un égress
    canné SANS spawner de sous-process — garde le contract test dans le gate par défaut."""

    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]:
        return 0, b'{"verdict": "suspicious", "real_meta": {}, "checks": []}', False


def _verifier_against(app: object) -> HttpContentVerifier:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    return HttpContentVerifier(client)


# ----------------------------------------------------- test de CONTRAT (vraie app Starlette)


@pytest.mark.asyncio
async def test_contract_verify_against_real_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Contrat de fil end-to-end (égress JSON → réponse → DTO) ; égress canné, pas de spawn réel.
    monkeypatch.setattr(check_module, "ProdChildRunner", _FakeProdChildRunner)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"\x00")
    config = AnalysisConfig.from_env({"QUARANTINE_DIR": str(quarantine)})
    verifier = _verifier_against(build_app(config))
    try:
        result = await verifier.verify(_HASH, {"target_id": "S2E062A"})
    finally:
        await verifier.aclose()
    assert result.verdict == "suspicious"


@pytest.mark.asyncio
async def test_contract_health_against_real_app(tmp_path: Path) -> None:
    config = AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path)})
    verifier = _verifier_against(build_app(config))
    try:
        assert await verifier.health() is True
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_contract_missing_file_is_error_verdict(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    config = AnalysisConfig.from_env({"QUARANTINE_DIR": str(quarantine)})
    verifier = _verifier_against(build_app(config))
    try:
        result = await verifier.verify("b" * 32, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


# ----------------------------------------------------- réponses fabriquées (MockTransport)


def _verifier_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> HttpContentVerifier:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://verifier")
    return HttpContentVerifier(client, max_response_bytes=1024)


@pytest.mark.asyncio
async def test_well_formed_200_maps_to_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"verdict": "unverified", "real_meta": {"x": 1}, "checks": ["c"]}
        )

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result == VerificationResult(verdict="unverified", real_meta={"x": 1}, checks=("c",))


@pytest.mark.asyncio
async def test_malformed_200_missing_verdict_is_error_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"real_meta": {}, "checks": []})  # pas de verdict

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_non_json_200_is_error_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_oversized_200_body_is_error_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        big = {"verdict": "unverified", "real_meta": {"pad": "x" * 5000}, "checks": []}
        return httpx.Response(200, json=big)  # > max_response_bytes=1024

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_verdict_not_a_string_is_error_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"verdict": 5, "real_meta": {}, "checks": []})

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_non_dict_json_200_is_error_verdict() -> None:
    """JSON valide mais non-objet (ex: liste) → verdict error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_bad_real_meta_type_is_error_verdict() -> None:
    """``real_meta`` non-dict → verdict error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"verdict": "unverified", "real_meta": "bad", "checks": []})

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_5xx_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    verifier = _verifier_with_handler(handler)
    try:
        with pytest.raises(VerifierUnavailableError):
            await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_connect_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    verifier = _verifier_with_handler(handler)
    try:
        with pytest.raises(VerifierUnavailableError):
            await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_timeout_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    verifier = _verifier_with_handler(handler)
    try:
        with pytest.raises(VerifierUnavailableError):
            await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_health_returns_false_on_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    verifier = _verifier_with_handler(handler)
    try:
        assert await verifier.health() is False
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_health_returns_false_on_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    verifier = _verifier_with_handler(handler)
    try:
        assert await verifier.health() is False
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_client() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"verdict": "unverified"}))
    client = httpx.AsyncClient(transport=transport, base_url="http://verifier")
    verifier = HttpContentVerifier(client)
    await verifier.aclose()
    assert client.is_closed is True
