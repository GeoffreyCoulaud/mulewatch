"""Service-side seam of the analysis (analysis spec §3/§6 — DA6).

``verify_file`` is the STABLE seam that ``app.py`` calls (unchanged signature): it checks the
EXISTENCE of the quarantined file (``is_file`` — metadata only, the parent NEVER reads the bytes,
DA8), then spawns the disposable analysis child (``spawn.run_analysis``) which runs the checks and
prints a defensively-parsed egress (``egress.parse``). Mapping (DA6, always 200): missing /
non-regular file → ``("error", {}, [])``; otherwise the child's real verdict
(``clean``/``suspicious``/``malicious``, or ``suspicious`` if the child times out/crashes/egresses
badly).

``cfg`` is REQUIRED (resolved once at boot by ``app.build_app`` and injected — no more lazy
per-request resolution, cf. error-boundary#0); ``runner`` is injectable (tests), its default is
the real ``ProdChildRunner``. ``expected`` stays minimal and non-decisive (DA2; the pipeline does
not use it in D-analysis).
"""

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from download_verifier import spawn
from download_verifier.config import AnalysisConfig
from download_verifier.egress import ChildOutcome
from download_verifier.spawn import ChildRunner, ProdChildRunner

_VERDICT_ERROR = "error"


def _is_regular_file_no_follow(path: Path) -> bool:
    """``True`` if ``path`` is a REGULAR file (NOT a symlink, a directory, or a FIFO).

    Sandbox-confinement#4: ``Path.is_file()`` follows symlinks. A compromised amuled sharing the
    quarantine RW could drop a symlink named like a valid hex hash there → ``is_file`` would
    return True and the verifier would open the symlink, following the target. We switch to
    ``os.lstat + S_ISREG``: a symlink is explicitly REJECTED (lstat does not follow), and so is
    any non-regular type (FIFO, socket, device, directory).
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def verify_file(
    quarantine_path: Path,
    expected: Mapping[str, object],
    *,
    cfg: AnalysisConfig,
    runner: ChildRunner | None = None,
) -> tuple[str, dict[str, object], list[object], ChildOutcome | None]:
    """Verify a quarantined file. Returns ``(verdict, real_meta, checks, outcome)`` (DA6).

    ``outcome`` is ``None`` if verify_file short-circuits (missing file, rejected symlink,
    non-regular type — ``error`` verdict, no child); otherwise it is the child's technical
    outcome category (cf. ``spawn.run_analysis``), observed as a metric app-side (observability#2).
    """
    child_runner = runner if runner is not None else ProdChildRunner(cfg)
    if not _is_regular_file_no_follow(quarantine_path):
        return _VERDICT_ERROR, {}, [], None
    return spawn.run_analysis(quarantine_path.name, cfg, child_runner)
