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
import os
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from download_verifier.check import verify_file

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
    verdict, real_meta, checks = verify_file(_quarantine_dir(request) / ed2k_hash, expected)
    return JSONResponse({"verdict": verdict, "real_meta": real_meta, "checks": checks})


async def health_endpoint(request: Request) -> JSONResponse:
    """``GET /health`` → 200 (vivacité du service ; gate full-mode du crawler, §7)."""
    return JSONResponse({"status": "ok"})


def _quarantine_dir(request: Request) -> Path:
    """Dossier de quarantaine injecté dans l'état de l'app (``build_app``)."""
    directory: Path = request.app.state.quarantine_dir
    return directory


def build_app(quarantine_dir: Path) -> Starlette:
    """Fabrique l'app Starlette liée à un dossier de quarantaine (testable in-process)."""
    application = Starlette(
        routes=[
            Route("/verify", verify_endpoint, methods=["POST"]),
            Route("/health", health_endpoint, methods=["GET"]),
        ]
    )
    application.state.quarantine_dir = quarantine_dir
    return application


def _quarantine_from_env() -> Path:
    return Path(os.environ.get("QUARANTINE_DIR", "/quarantine"))


app = build_app(_quarantine_from_env())
