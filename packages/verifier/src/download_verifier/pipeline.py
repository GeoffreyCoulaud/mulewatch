"""Pipeline d'analyse PUR (spec analysis §5) : exécute les checks activés, agrège.

``run`` exécute les checks listés dans ``cfg.enabled_checks`` (dans cet ordre, en filtrant ceux
absents du registre — un ``clamav`` non implémenté est simplement ignoré, DA4), agrège leur
worst-status en un verdict (``clean < suspicious < malicious``), fusionne leurs ``meta`` en un
``real_meta``, et renvoie la trace ``checks`` (``[{name, status, meta}]``). Pur : aucun I/O ici —
``type_sniff`` reçoit l'en-tête déjà lu, ``ffprobe`` reçoit le chemin + son runner injecté.
"""

from pathlib import Path

from download_verifier.checks import clamav as clamav_check
from download_verifier.checks import ffprobe as ffprobe_check
from download_verifier.checks import type_sniff as type_sniff_check
from download_verifier.checks.base import CheckOutcome, worst_status
from download_verifier.checks.clamav import ClamavRunner
from download_verifier.checks.ffprobe import FfprobeRunner
from download_verifier.config import AnalysisConfig


def run(
    header: bytes,
    path: Path,
    ffprobe_runner: FfprobeRunner,
    clamav_runner: ClamavRunner,
    cfg: AnalysisConfig,
) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    """Exécute les checks activés ; rend ``(verdict, real_meta, checks)``."""
    outcomes: list[CheckOutcome] = []
    for name in cfg.enabled_checks:
        if name == "type_sniff":
            outcomes.append(type_sniff_check.sniff(header))
        elif name == "ffprobe":
            outcomes.append(ffprobe_check.probe(path, ffprobe_runner, cfg))
        elif name == "clamav":
            outcomes.append(clamav_check.scan(path, clamav_runner, cfg))
        # tout AUTRE nom (faute de frappe) est ignoré (DA4).
    verdict = worst_status([outcome.status for outcome in outcomes])
    real_meta: dict[str, object] = {}
    for outcome in outcomes:
        real_meta.update(outcome.meta)
    checks: list[dict[str, object]] = [
        {"name": outcome.name, "status": outcome.status, "meta": dict(outcome.meta)}
        for outcome in outcomes
    ]
    return verdict, real_meta, checks
