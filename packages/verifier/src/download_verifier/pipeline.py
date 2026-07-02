"""PURE analysis pipeline (analysis spec §5): runs the enabled checks, aggregates.

``run`` runs the checks listed in ``cfg.enabled_checks`` (in that order, filtering out those
absent from the registry — an unimplemented ``clamav`` is simply ignored, DA4), aggregates their
worst-status into a verdict (``clean < suspicious < malicious``), merges their ``meta`` into a
``real_meta``, and returns the ``checks`` trace (``[{name, status, meta}]``). Pure: no I/O here —
``type_sniff`` receives the already-read header, ``ffprobe`` the path + its injected runner.
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
    """Run the enabled checks; return ``(verdict, real_meta, checks)``."""
    outcomes: list[CheckOutcome] = []
    for name in cfg.enabled_checks:
        if name == "type_sniff":
            outcomes.append(type_sniff_check.sniff(header))
        elif name == "ffprobe":
            outcomes.append(ffprobe_check.probe(path, ffprobe_runner, cfg))
        elif name == "clamav":
            outcomes.append(clamav_check.scan(path, clamav_runner, cfg))
        # any OTHER name (typo) is ignored (DA4).
    verdict = worst_status([outcome.status for outcome in outcomes])
    real_meta: dict[str, object] = {}
    for outcome in outcomes:
        real_meta.update(outcome.meta)
    checks: list[dict[str, object]] = [
        {"name": outcome.name, "status": outcome.status, "meta": dict(outcome.meta)}
        for outcome in outcomes
    ]
    return verdict, real_meta, checks
