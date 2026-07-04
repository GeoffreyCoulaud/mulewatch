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
    """Fake ``ProdChildRunner`` (signature conforming to the ``ChildRunner`` Protocol): returns a
    canned egress WITHOUT spawning a subprocess — keeps the analysis tests in the default gate."""

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
    # file present → analysis; canned egress (no real spawn in the default gate).
    monkeypatch.setattr(check_module, "ProdChildRunner", _FakeProdChildRunner)
    (quarantine / ("a" * 32)).write_bytes(b"\x00\x01")
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", json={"hash": "a" * 32, "expected": {"target_id": "062A"}}
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
    # body > bound (the verifier does not load an unbounded body into memory).
    huge = json.dumps({"hash": "c" * 32, "expected": {"pad": "x" * 200_000}})
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=huge.encode(), headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_deeply_nested_json(quarantine: Path) -> None:
    # body UNDER the byte cap but too deep: json.loads raises RecursionError.
    nested = ("[" * 20000) + "1" + ("]" * 20000)  # ~40 KiB < 64 KiB
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=nested.encode(), headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_non_canonical_hash(quarantine: Path) -> None:
    # a non-canonical hash (traversal/slash) must never escape the quarantine directory.
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "../etc/passwd", "expected": {}})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_defaults_expected_to_empty_mapping(
    quarantine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # expected omitted → default {} acceptable; the pipeline analyzes (canned egress, no spawn).
    monkeypatch.setattr(check_module, "ProdChildRunner", _FakeProdChildRunner)
    (quarantine / ("d" * 32)).write_bytes(b"x")
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "d" * 32})  # expected omitted
    assert response.status_code == 200
    assert response.json()["verdict"] == "suspicious"


@pytest.mark.asyncio
async def test_verify_rejects_non_object_json_payload(quarantine: Path) -> None:
    # valid JSON body but non-object (list) → 400.
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=b"[1, 2, 3]", headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_non_dict_expected(quarantine: Path) -> None:
    # expected present but non-object → 400.
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
    # sandbox-confinement#0 / concurrency-async#0: verify_file is synchronous and blocking (spawn +
    # communicate). It MUST run via run_in_threadpool, not on the event loop thread — otherwise
    # /health (Docker healthcheck) and /metrics are frozen for the whole analysis. Deterministic
    # proxy for non-blocking: verify_file runs on a DIFFERENT thread than the event loop.
    import download_verifier.app as app_module

    captured: dict[str, int] = {}

    def _capture_thread(
        path: Path, expected: Mapping[str, object], *, cfg: object
    ) -> tuple[str, dict[str, object], list[object], str | None]:
        captured["thread"] = threading.get_ident()
        return "clean", {}, [], "ok"

    monkeypatch.setattr(app_module, "verify_file", _capture_thread)
    (quarantine / ("a" * 32)).write_bytes(b"x")
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
    assert response.status_code == 200
    assert captured["thread"] != threading.get_ident()  # executed OFF the event loop thread


@pytest.mark.asyncio
async def test_verify_injects_boot_resolved_config(
    quarantine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # error-boundary#0: the config is resolved ONCE at boot (app.state.config) and INJECTED into
    # verify_file — no more per-request AnalysisConfig.from_env(os.environ), which turned an
    # invalid config into a "transient" 500 → dead-letter, instead of a fail-fast at startup.
    import download_verifier.app as app_module

    captured: dict[str, object] = {}

    def _capture(
        path: Path, expected: Mapping[str, object], *, cfg: object
    ) -> tuple[str, dict[str, object], list[object], str | None]:
        captured["cfg"] = cfg
        return "clean", {}, [], "ok"

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
    assert captured["cfg"] is config  # the SAME instance resolved at boot, not a re-resolution


@pytest.mark.asyncio
async def test_verify_increments_request_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import download_verifier.app as app_module

    monkeypatch.setattr(
        app_module, "verify_file", lambda path, expected, *, cfg: ("clean", {}, (), "ok")
    )
    (tmp_path / ("a" * 32)).write_bytes(b"x")
    async with _client(tmp_path) as client:
        verify = await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
        metrics = await client.get("/metrics")
    assert verify.status_code == 200
    assert 'emule_verifier_requests_total{verdict="clean"} 1.0' in metrics.text


@pytest.mark.asyncio
async def test_verify_increments_child_outcome_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # observability#2: a verify that completes with a ``timeout`` outcome must increment
    # ``emule_verifier_child_outcome{outcome="timeout"}``. The business verdict stays aggregated
    # in ``emule_verifier_requests`` (orthogonality).
    import download_verifier.app as app_module

    monkeypatch.setattr(
        app_module,
        "verify_file",
        lambda path, expected, *, cfg: ("suspicious", {}, (), "timeout"),
    )
    (tmp_path / ("a" * 32)).write_bytes(b"x")
    async with _client(tmp_path) as client:
        verify = await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
        metrics = await client.get("/metrics")
    assert verify.status_code == 200
    assert 'emule_verifier_child_outcome_total{outcome="timeout"} 1.0' in metrics.text


@pytest.mark.asyncio
async def test_verify_skips_child_outcome_when_no_child_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # observability#2 (boundary case): verify_file short-circuits (missing file / symlink /
    # non-regular type → ``error`` verdict, outcome=None). No child ran → we must NOT touch
    # ``child_outcome`` (otherwise the metric would be polluted with a fictitious category).
    import download_verifier.app as app_module

    monkeypatch.setattr(
        app_module, "verify_file", lambda path, expected, *, cfg: ("error", {}, [], None)
    )
    async with _client(tmp_path) as client:
        await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
        metrics = await client.get("/metrics")
    # the counter has no child_outcome series (never touched)
    assert "emule_verifier_child_outcome_total{" not in metrics.text


@pytest.mark.asyncio
async def test_verify_counts_200_responses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # observability#3: every /verify exit is counted by ``responses{status}``. Here 200.
    import download_verifier.app as app_module

    monkeypatch.setattr(
        app_module, "verify_file", lambda path, expected, *, cfg: ("clean", {}, (), "ok")
    )
    (tmp_path / ("a" * 32)).write_bytes(b"x")
    async with _client(tmp_path) as client:
        await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
        metrics = await client.get("/metrics")
    assert 'emule_verifier_responses_total{status="200"} 1.0' in metrics.text


@pytest.mark.asyncio
async def test_verify_counts_400_responses(quarantine: Path) -> None:
    # observability#3: a 400 (invalid JSON) must also increment ``responses{status="400"}``.
    # Before, only ``observe`` was called (200s only) → the 400s were invisible.
    async with _client(quarantine) as client:
        await client.post("/verify", content=b"{not json")
        metrics = await client.get("/metrics")
    assert 'emule_verifier_responses_total{status="400"} 1.0' in metrics.text


@pytest.mark.asyncio
async def test_verify_counts_500_responses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # observability#3: an unforeseen crash of ``verify_file`` (mkdtemp on a full FS, etc.) exits
    # 500 and MUST be counted. The net is a try/except Exception that re-raises after counting,
    # letting Starlette's standard ServerErrorMiddleware render the 500.
    import download_verifier.app as app_module

    def _boom(path: Path, expected: Mapping[str, object], *, cfg: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(app_module, "verify_file", _boom)
    (tmp_path / ("a" * 32)).write_bytes(b"x")
    # ``raise_app_exceptions=False``: we want httpx to convert the exception into an HTTP 500
    # response (like a real ASGI server), not re-raise it test-side.
    config = AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path)})
    app = build_app(config)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
        metrics = await client.get("/metrics")
    assert response.status_code == 500
    assert 'emule_verifier_responses_total{status="500"} 1.0' in metrics.text
