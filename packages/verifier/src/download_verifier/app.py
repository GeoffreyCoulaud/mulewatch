"""Verifier Starlette app (verify spec §4 — DECISION DV1/DV2).

``POST /verify {hash, expected}`` → ``{verdict, real_meta, checks}``: STRICT and BOUNDED
validation (body read as bytes, size capped BEFORE parse → 400; canonical hash required so we
never escape the quarantine directory — no traversal); delegates to ``check.verify_file``.
``GET /health`` → 200 (the crawler fails fast at startup if this health-check fails, §7).

Stateless / no-DB / no-domain / no-Internet (spec §4): only reads ``quarantine/<hash>`` RO.
The quarantine directory comes from the service config (``QUARANTINE_DIR`` env, default
``/quarantine``). ``build_app(quarantine_dir)`` is the testable factory; ``app`` (module-level)
is the instance ``uvicorn`` loads by import path (``download_verifier.app:app``).
"""

import json
import logging
import os
import re
import time
from pathlib import Path

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from download_verifier.check import verify_file
from download_verifier.config import AnalysisConfig
from download_verifier.metrics import VerifierMetrics

_logger = logging.getLogger("download_verifier.app")

# Canonical eD2k hash (32 lowercase hex): the ONLY accepted form → never a traversal out of the
# quarantine directory (a "../" or a "/" does not match and yields 400).
_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")

# Bounded body: a legitimate /verify is tiny ({hash, expected}). 64 KiB is generous and protects
# against an unbounded body loaded into memory (defensive parsing service-side too, §8).
_MAX_BODY_BYTES = 65536


def _bad_request(metrics: VerifierMetrics, detail: str) -> JSONResponse:
    metrics.observe_response(400)
    return JSONResponse({"error": detail}, status_code=400)


async def verify_endpoint(request: Request) -> JSONResponse:
    """``POST /verify``: validate (strict + bounded), analyze (confined child, DA6), return result.

    The NO-OP is gone: analysis spawns a confined child (``check.verify_file`` → DA6).

    Instrumentation (observability#2/#3): ``responses{status}`` is incremented for EVERY
    exit (200/400/500) — the historical ``observe`` only saw the 200s. ``child_outcome``
    captures the technical CAUSE of the child's outcome (timeout, exit ≠ 0, overflow, broken
    JSON, OK) — orthogonal to the business verdict.
    """
    metrics: VerifierMetrics = request.app.state.metrics
    try:
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            return _bad_request(metrics, "body too large")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError, RecursionError):
            # RecursionError: body under the byte cap but nested too deeply
            # (RecursionError is a RuntimeError, not a ValueError) → clean 400, never 500.
            return _bad_request(metrics, "invalid JSON")
        if not isinstance(payload, dict):
            return _bad_request(metrics, "expected a JSON object")
        ed2k_hash = payload.get("hash")
        if not isinstance(ed2k_hash, str) or _CANONICAL_HASH_RE.fullmatch(ed2k_hash) is None:
            return _bad_request(metrics, "canonical hash required (32 lowercase hex)")
        expected = payload.get("expected", {})
        if not isinstance(expected, dict):
            return _bad_request(metrics, "expected must be an object")
        config: AnalysisConfig = request.app.state.config
        start = time.monotonic()
        # verify_file is SYNCHRONOUS and blocking (spawns a child + communicate until timeout):
        # running it in a thread frees the event loop, which keeps serving /health and /metrics
        # during the analysis (otherwise the container flaps unhealthy — sandbox-confinement#0).
        verdict, real_meta, checks, outcome = await run_in_threadpool(
            verify_file, _quarantine_dir(request) / ed2k_hash, expected, cfg=config
        )
        metrics.observe(verdict, time.monotonic() - start)
        if outcome is not None:
            # ``outcome`` only exists if a child ran: verify_file short-circuits
            # (missing file, symlink, non-regular type → ``error``) returns None and there is
            # NO technical outcome to classify.
            metrics.observe_child_outcome(outcome)
        _logger.info("verify hash=%s → verdict=%s outcome=%s", ed2k_hash, verdict, outcome)
        metrics.observe_response(200)
        return JSONResponse({"verdict": verdict, "real_meta": real_meta, "checks": checks})
    except Exception:
        # 500 safety net (observability#3): any unforeseen path (mkdtemp on a full FS, etc.) is
        # counted before Starlette generates its default 500 response. We re-raise so the
        # standard ASGI middleware does its job.
        metrics.observe_response(500)
        raise


async def health_endpoint(request: Request) -> JSONResponse:
    """``GET /health`` → 200 (service liveness; crawler full-mode gate, §7)."""
    return JSONResponse({"status": "ok"})


async def metrics_endpoint(request: Request) -> Response:
    """``GET /metrics``: Prometheus exposition of the app's dedicated registry."""
    metrics: VerifierMetrics = request.app.state.metrics
    return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)


def _quarantine_dir(request: Request) -> Path:
    """Quarantine directory injected into the app state (``build_app``)."""
    directory: Path = request.app.state.quarantine_dir
    return directory


def build_app(config: AnalysisConfig) -> Starlette:
    """Build the Starlette app from an ALREADY resolved/validated config (in-process testable).

    The config (rlimits, timeout, checks, quarantine_dir) is resolved ONCE upfront and stored
    in ``state``: ``verify_endpoint`` injects it into ``verify_file`` without re-reading the
    environment per request (cf. error-boundary#0). The quarantine directory follows from it
    (``quarantine_dir``).
    """
    application = Starlette(
        routes=[
            Route("/verify", verify_endpoint, methods=["POST"]),
            Route("/health", health_endpoint, methods=["GET"]),
            Route("/metrics", metrics_endpoint, methods=["GET"]),
        ]
    )
    application.state.config = config
    application.state.quarantine_dir = Path(config.quarantine_dir)
    application.state.metrics = VerifierMetrics()
    return application


# Config resolution/validation AT BOOT: an invalid env (negative RLIMIT, unknown check, violated
# floors) raises here, at module import → uvicorn does not start (fail-fast §8/E-D13), instead of
# a "transient" 500 per request leading to the dead-letter.
app = build_app(AnalysisConfig.from_env(os.environ))
