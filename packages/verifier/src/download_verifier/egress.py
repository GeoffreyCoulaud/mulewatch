"""Contrat d'égress de l'enfant (spec analysis §4/§6 — DA6) : parse DÉFENSIF côté parent.

``parse`` mappe l'issue de l'enfant en ``(verdict, real_meta, checks)`` de façon TOUJOURS
déterministe (jamais d'exception remontée — le service répond 200, §6). Un enfant qui timeout,
sort en erreur, dépasse le cap d'octets, ou rend un égress illisible/hors-schéma est un signal de
POISON → ``suspicious``. Schéma strict : objet ``{verdict ∈ {clean,suspicious,malicious}: str,
real_meta: obj, checks: list}``. Tout écart → ``suspicious``.

``classify_outcome`` rend la CATÉGORIE technique de l'issue (observability#2) — orthogonale au
verdict métier : ``ok`` (le child a sorti un égress valide), ``timeout`` (wall-clock écoulé),
``nonzero_exit`` (le child a crashé / dépassé un rlimit / sorti != 0), ``egress_overflow`` (le
stdout dépasse le cap), ``malformed`` (égress illisible / hors-schéma). En incident de masse, on
voit ``suspicious`` monter en valeur métier ET la CAUSE technique (timeout, crash, etc.).

DÉCISION (audit 2026-06-23 / error-boundary#3) : un crash interne d'un runner (ffprobe, clamav)
fait CRASHER le child (returncode ≠ 0) au lieu d'écrire un égress JSON ``suspicious`` propre.
C'est VOULU : le mapping ``returncode != 0 → suspicious`` côté parent est le contrat de
défense (DA6 — un child compromis ne peut PAS mentir s'il ne contrôle pas le returncode). Le
test ``test_nonzero_returncode_is_suspicious`` fige cette frontière.
"""

import json
from typing import Literal

from download_verifier.checks.base import STATUS_RANK
from download_verifier.config import AnalysisConfig

ChildOutcome = Literal["ok", "timeout", "nonzero_exit", "egress_overflow", "malformed"]

_VALID_VERDICTS = frozenset(STATUS_RANK)


def _poison() -> tuple[str, dict[str, object], list[object]]:
    """Verdict de poison déterministe (valeurs NEUVES → pas de mutation partagée)."""
    return "suspicious", {}, []


def parse(
    stdout: bytes, returncode: int, timed_out: bool, cfg: AnalysisConfig
) -> tuple[str, dict[str, object], list[object]]:
    """Mappe l'égress enfant en ``(verdict, real_meta, checks)`` (jamais d'exception)."""
    if timed_out or returncode != 0 or len(stdout) > cfg.egress_cap_bytes:
        return _poison()
    try:
        payload = json.loads(stdout)
    # RecursionError = défense en profondeur (cf. app.py §8) ; pas de test dédié car json.loads
    # (impl C) ne récurse pas en CPython 3.12 — la branche except est couverte par les cas non-JSON.
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _poison()
    if not isinstance(payload, dict):
        return _poison()
    verdict = payload.get("verdict")
    real_meta = payload.get("real_meta")
    checks = payload.get("checks")
    if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
        return _poison()
    if not isinstance(real_meta, dict) or not isinstance(checks, list):
        return _poison()
    return verdict, real_meta, checks


def classify_outcome(
    stdout: bytes, returncode: int, timed_out: bool, cfg: AnalysisConfig
) -> ChildOutcome:
    """Catégorie technique de l'issue (observability#2) — orthogonale au verdict.

    Mêmes filtres défensifs que ``parse`` (ordre identique : un égress sain ne tombe pas dans
    une catégorie d'incident). ``ok`` ⇔ ``parse`` rendrait le verdict du JSON ; tout le reste
    est une CAUSE technique d'incident distincte à exposer en métrique.
    """
    if timed_out:
        return "timeout"
    if returncode != 0:
        return "nonzero_exit"
    if len(stdout) > cfg.egress_cap_bytes:
        return "egress_overflow"
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return "malformed"
    if not isinstance(payload, dict):
        return "malformed"
    verdict = payload.get("verdict")
    real_meta = payload.get("real_meta")
    checks = payload.get("checks")
    if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
        return "malformed"
    if not isinstance(real_meta, dict) or not isinstance(checks, list):
        return "malformed"
    return "ok"
