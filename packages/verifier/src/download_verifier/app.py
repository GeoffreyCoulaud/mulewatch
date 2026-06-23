"""App Starlette du verifier (spec verify §4 — DÉCISION DV1/DV2).

``POST /verify {hash, expected}`` → ``{verdict, real_meta, checks}`` : validation STRICTE et
BORNÉE (corps lu en bytes, taille plafonnée AVANT parse → 400 ; hash canonique exigé pour ne
jamais sortir du dossier de quarantaine — pas de traversal) ; délègue à ``check.verify_file``.
``GET /health`` → 200 (le crawler fail-fast au démarrage si ce health-check échoue, §7).

Stateless / no-DB / no-domain / no-Internet (spec §4) : ne lit que ``quarantine/<hash>`` en RO.
Le dossier de quarantaine vient de la config du service (``QUARANTINE_DIR`` env, défaut
``/quarantine``). ``build_app(quarantine_dir)`` est la fabrique testable ; ``app`` (module-level)
est l'instance que ``uvicorn`` charge par chemin d'import (``download_verifier.app:app``).
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

# Hash eD2k canonique (32 hex minuscules) : la SEULE forme acceptée → jamais de traversal hors
# du dossier de quarantaine (un "../" ou un "/" ne matche pas et donne 400).
_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")

# Corps borné : un /verify légitime est minuscule ({hash, expected}). 64 Kio est généreux et
# protège d'un corps illimité chargé en mémoire (parsing défensif côté service aussi, §8).
_MAX_BODY_BYTES = 65536


def _bad_request(detail: str) -> JSONResponse:
    return JSONResponse({"error": detail}, status_code=400)


async def verify_endpoint(request: Request) -> JSONResponse:
    """``POST /verify`` : valide (strict + borné), analyse (enfant confiné, DA6), rend le résultat.

    Le NO-OP n'existe plus : l'analyse spawne un enfant confiné (``check.verify_file`` → DA6).
    """
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        return _bad_request("corps trop volumineux")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        # RecursionError : corps sous le cap d'octets mais trop profondément imbriqué
        # (RecursionError est un RuntimeError, pas un ValueError) → 400 propre, jamais 500.
        return _bad_request("JSON invalide")
    if not isinstance(payload, dict):
        return _bad_request("objet JSON attendu")
    ed2k_hash = payload.get("hash")
    if not isinstance(ed2k_hash, str) or _CANONICAL_HASH_RE.fullmatch(ed2k_hash) is None:
        return _bad_request("hash canonique requis (32 hex minuscules)")
    expected = payload.get("expected", {})
    if not isinstance(expected, dict):
        return _bad_request("expected doit être un objet")
    metrics: VerifierMetrics = request.app.state.metrics
    config: AnalysisConfig = request.app.state.config
    start = time.monotonic()
    # verify_file est SYNCHRONE et bloquant (spawn d'un enfant + communicate jusqu'au timeout) :
    # l'exécuter dans un thread libère l'event loop, qui continue de servir /health et /metrics
    # pendant l'analyse (sinon le conteneur flappe en unhealthy — sandbox-confinement#0).
    verdict, real_meta, checks = await run_in_threadpool(
        verify_file, _quarantine_dir(request) / ed2k_hash, expected, cfg=config
    )
    metrics.observe(verdict, time.monotonic() - start)
    _logger.info("verify hash=%s → verdict=%s", ed2k_hash, verdict)
    return JSONResponse({"verdict": verdict, "real_meta": real_meta, "checks": checks})


async def health_endpoint(request: Request) -> JSONResponse:
    """``GET /health`` → 200 (vivacité du service ; gate full-mode du crawler, §7)."""
    return JSONResponse({"status": "ok"})


async def metrics_endpoint(request: Request) -> Response:
    """``GET /metrics`` : exposition Prometheus du registre dédié de l'app."""
    metrics: VerifierMetrics = request.app.state.metrics
    return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)


def _quarantine_dir(request: Request) -> Path:
    """Dossier de quarantaine injecté dans l'état de l'app (``build_app``)."""
    directory: Path = request.app.state.quarantine_dir
    return directory


def build_app(config: AnalysisConfig) -> Starlette:
    """Fabrique l'app Starlette à partir d'une config DÉJÀ résolue/validée (testable in-process).

    La config (rlimits, timeout, checks, quarantine_dir) est résolue UNE fois en amont et stockée
    dans ``state`` : ``verify_endpoint`` l'injecte à ``verify_file`` sans re-lire l'environnement
    par requête (cf. error-boundary#0). Le dossier de quarantaine en découle (``quarantine_dir``).
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


# Résolution/validation de la config AU BOOT : une env invalide (RLIMIT négatif, check inconnu,
# planchers violés) lève ici, à l'import du module → uvicorn ne démarre pas (fail-fast §8/E-D13),
# au lieu d'un 500 « transitoire » par requête menant au dead-letter.
app = build_app(AnalysisConfig.from_env(os.environ))
