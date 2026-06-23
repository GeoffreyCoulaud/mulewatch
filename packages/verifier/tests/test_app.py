import json
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
import pytest

import download_verifier.check as check_module
from download_verifier.app import build_app
from download_verifier.config import AnalysisConfig


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
    config = AnalysisConfig.from_env({"QUARANTINE_DIR": str(quarantine)})
    transport = httpx.ASGITransport(app=build_app(config))
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


@pytest.mark.asyncio
async def test_metrics_endpoint_responds(tmp_path: Path) -> None:
    async with _client(tmp_path) as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "emule_verifier_requests" in response.text


@pytest.mark.asyncio
async def test_verify_runs_off_the_event_loop_thread(
    quarantine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # sandbox-confinement#0 / concurrency-async#0 : verify_file est synchrone et bloquant (spawn +
    # communicate). Il DOIT tourner via run_in_threadpool, pas sur le thread de l'event loop —
    # sinon /health (healthcheck Docker) et /metrics sont gelés pendant toute l'analyse. Proxy
    # déterministe du non-blocage : verify_file s'exécute sur un thread DIFFÉRENT de l'event loop.
    import download_verifier.app as app_module

    captured: dict[str, int] = {}

    def _capture_thread(
        path: Path, expected: Mapping[str, object], *, cfg: object
    ) -> tuple[str, dict[str, object], list[object]]:
        captured["thread"] = threading.get_ident()
        return "clean", {}, []

    monkeypatch.setattr(app_module, "verify_file", _capture_thread)
    (quarantine / ("a" * 32)).write_bytes(b"x")
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
    assert response.status_code == 200
    assert captured["thread"] != threading.get_ident()  # exécuté HORS du thread de l'event loop


@pytest.mark.asyncio
async def test_verify_injects_boot_resolved_config(
    quarantine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # error-boundary#0 : la config est résolue UNE fois au boot (app.state.config) et INJECTÉE à
    # verify_file — fini le AnalysisConfig.from_env(os.environ) par requête, qui transformait une
    # config invalide en 500 « transitoire » → dead-letter, au lieu d'un fail-fast au démarrage.
    import download_verifier.app as app_module

    captured: dict[str, object] = {}

    def _capture(
        path: Path, expected: Mapping[str, object], *, cfg: object
    ) -> tuple[str, dict[str, object], list[object]]:
        captured["cfg"] = cfg
        return "clean", {}, []

    monkeypatch.setattr(app_module, "verify_file", _capture)
    config = AnalysisConfig.from_env(
        {"QUARANTINE_DIR": str(quarantine), "ENABLED_CHECKS": "type_sniff"}
    )
    app = build_app(config)
    (quarantine / ("a" * 32)).write_bytes(b"x")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
    assert response.status_code == 200
    assert captured["cfg"] is config  # la MÊME instance résolue au boot, pas une re-résolution


@pytest.mark.asyncio
async def test_verify_increments_request_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import download_verifier.app as app_module

    monkeypatch.setattr(app_module, "verify_file", lambda path, expected, *, cfg: ("clean", {}, ()))
    (tmp_path / ("a" * 32)).write_bytes(b"x")
    async with _client(tmp_path) as client:
        verify = await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
        metrics = await client.get("/metrics")
    assert verify.status_code == 200
    assert 'emule_verifier_requests_total{verdict="clean"} 1.0' in metrics.text
