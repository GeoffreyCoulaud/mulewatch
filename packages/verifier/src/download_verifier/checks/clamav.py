"""``clamav`` check (clamav spec §3): 3rd verdict source, by viral SIGNATURES.

``scan`` invokes ``clamscan`` via an INJECTABLE ``ClamavRunner`` (prod = real subprocess; tests =
canned ``(rc, stdout)``) with FIXED flags. ``clamscan`` encodes its verdict in its EXIT CODE:
``0`` → no virus (``clean``), ``1`` → virus found (``malicious``), ``≥2`` → error (missing/corrupt
database, I/O…) → ``suspicious`` (defensive: we cannot claim "safe" without a database, nor do we
discard the file). On a match, ``_parse_signature`` extracts the signature name BEST-EFFORT for
``meta`` (purely informational — the ``malicious`` verdict is unchanged). ``clamav`` runs in the
confined child like ``ffprobe`` (local RO database + local file, no network); a ``clamscan`` that
loops/exceeds the rlimits is killed by the parent and yields ``suspicious`` via the egress.
``error`` is NEVER a check status (reserved service-level).
"""

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from download_verifier.checks.base import CheckOutcome
from download_verifier.config import AnalysisConfig

# Explicit bounds passed to ``clamscan`` (sandbox-confinement#3) — defense-in-depth. The vectors
# are ALREADY bounded by the parent's rlimits + mem_limit + wall-clock, but we align the posture
# of the clamav engine itself (recursion zip-bombs, extracted files that exceed the rlimits
# before they bite, etc.). The bounds are calibrated NOT to hinder normal use (episode media
# ~500 MiB max):
#
#  - ``--max-scansize=2048M`` / ``--max-filesize=2048M``: 4× the usual size (clamav's 100M / 25M
#    default is too tight for a media file);
#  - ``--max-recursion=10``: a legitimate media has no nested archive;
#  - ``--max-files=1000``: same (a non-archive media does not produce 1000 units);
#  - ``--max-scantime=120000`` (ms = 120 s): consistent with the parent's ``RLIMIT_CPU_S``.
_CLAMSCAN_LIMITS: tuple[str, ...] = (
    "--max-scansize=2048M",
    "--max-filesize=2048M",
    "--max-recursion=10",
    "--max-files=1000",
    "--max-scantime=120000",
)


class ClamavRunner(Protocol):
    """Run clamscan and return ``(returncode, stdout)``. Injected for tests."""

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]: ...


class ProdClamavRunner:
    """PROD ``ClamavRunner``: real ``subprocess.run`` (covered by analysis_integration)."""

    def __init__(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:  # pragma: no cover
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=self._timeout_s,
            check=False,
        )
        return completed.returncode, completed.stdout


def scan(path: Path, runner: ClamavRunner, cfg: AnalysisConfig) -> CheckOutcome:
    """Scan ``path`` via ``runner``; return ``CheckOutcome`` (status + meta)."""
    argv = [
        cfg.clamscan_path,
        "--no-summary",
        "--stdout",
        *_CLAMSCAN_LIMITS,
        "--database",
        cfg.clamav_db_dir,
        str(path),
    ]
    returncode, stdout = runner(argv)
    if returncode == 0:
        return CheckOutcome(name="clamav", status="clean", meta={})
    if returncode == 1:
        signature = _parse_signature(stdout)
        meta: dict[str, object] = {}
        if signature is not None:
            meta["clamav_signature"] = signature
        return CheckOutcome(name="clamav", status="malicious", meta=meta)
    # rc >= 2 (or anything else): clamscan error (missing/corrupt database, I/O…) → defensive.
    return CheckOutcome(name="clamav", status="suspicious", meta={})


def _parse_signature(stdout: bytes) -> str | None:
    """Extract the signature name BEST-EFFORT from a ``<file>: <sig> FOUND`` line; else ``None``."""
    for line in stdout.decode("utf-8", "replace").splitlines():
        if line.endswith(" FOUND") and ": " in line:
            return line.rsplit(": ", 1)[1].removesuffix(" FOUND").strip() or None
    return None
