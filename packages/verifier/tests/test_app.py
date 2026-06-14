import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
import pytest

import download_verifier.check as check_module
from download_verifier.app import build_app


class _FakeProdChildRunner:
    """Faux ``ProdChildRunner`` (signature conforme au Protocol ``ChildRunner``) : rend un égress
    canné SANS spawner de sous-process — garde les tests d'analyse dans le gate par défaut."""

    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]:
        return 0, b'{"verdict": "suspicious", "real_meta": {}, "checks": []}', False


@pytest.fixture
def quarantine(tmp_path: Path) -> Path:
    directory = tmp_path / "quarantine"
    directory.mkdir()
    return directory


def _client(quarantine: Path) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=build_app(quarantine))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_health_returns_200(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_verify_existing_file_returns_suspicious(
    quarantine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # fichier présent → analyse ; égress canné (pas de spawn réel dans le gate par défaut).
    monkeypatch.setattr(check_module, "ProdChildRunner", _FakeProdChildRunner)
    (quarantine / ("a" * 32)).write_bytes(b"\x00\x01")
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", json={"hash": "a" * 32, "expected": {"target_id": "S2E062A"}}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "suspicious"


@pytest.mark.asyncio
async def test_verify_missing_file_returns_error_verdict(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "b" * 32, "expected": {}})
    assert response.status_code == 200
    assert response.json()["verdict"] == "error"


@pytest.mark.asyncio
async def test_verify_rejects_invalid_json(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=b"{not json", headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_missing_hash_field(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"expected": {}})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_non_string_hash(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": 123, "expected": {}})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_oversized_body(quarantine: Path) -> None:
    # corps > borne (le verifier ne charge pas un corps illimité en mémoire).
    huge = json.dumps({"hash": "c" * 32, "expected": {"pad": "x" * 200_000}})
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=huge.encode(), headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_deeply_nested_json(quarantine: Path) -> None:
    # corps SOUS le cap d'octets mais trop profond : json.loads lève RecursionError.
    nested = ("[" * 20000) + "1" + ("]" * 20000)  # ~40 Kio < 64 Kio
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=nested.encode(), headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_non_canonical_hash(quarantine: Path) -> None:
    # un hash hors-canon (traversal/slash) ne doit jamais sortir du dossier de quarantaine.
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "../etc/passwd", "expected": {}})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_defaults_expected_to_empty_mapping(
    quarantine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # expected omis → défaut {} acceptable ; le pipeline analyse (égress canné, pas de spawn réel).
    monkeypatch.setattr(check_module, "ProdChildRunner", _FakeProdChildRunner)
    (quarantine / ("d" * 32)).write_bytes(b"x")
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "d" * 32})  # expected omis
    assert response.status_code == 200
    assert response.json()["verdict"] == "suspicious"


@pytest.mark.asyncio
async def test_verify_rejects_non_object_json_payload(quarantine: Path) -> None:
    # corps JSON valide mais non-objet (liste) → 400.
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=b"[1, 2, 3]", headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_non_dict_expected(quarantine: Path) -> None:
    # expected présent mais non-objet → 400.
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "e" * 32, "expected": "string"})
    assert response.status_code == 400
