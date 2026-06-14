"""Spawn de l'enfant d'analyse (spec analysis §4 — DA5/DA8/DA9), côté PARENT.

``run_analysis`` re-exec un enfant Python jetable par fichier (PAS ``os.fork``) : argv minimal,
cwd ``tempfile.mkdtemp()`` jetable (supprimé en ``finally`` même en cas d'exception), env EXPLICITE
minimal (on n'hérite PAS de ``os.environ`` — secrets/VPN ; on ne passe que QUARANTINE_DIR + la
config des checks + un PATH minimal). Le ``ChildRunner`` est INJECTABLE : l'impl PROD fait le vrai
``subprocess.Popen`` (``close_fds=True``, ``preexec_fn=_confine`` = rlimits + setsid,
timeout-kill du
groupe via ``killpg``) — ces lignes système sont ``# pragma: no cover`` (couvertes par
analysis_integration). Le mapping de l'issue (stdout/timeout/exit) est délégué à ``egress.parse``
(défensif, DA6). Le parent ne lit JAMAIS d'octets du fichier (DA8).
"""

import contextlib
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from typing import Protocol

from download_verifier import egress
from download_verifier.config import AnalysisConfig

_CHILD_MODULE = "download_verifier.analysis_child"
_MINIMAL_PATH = "/usr/local/bin:/usr/bin:/bin"


class ChildRunner(Protocol):
    """Exécute l'enfant et rend ``(returncode, stdout, timed_out)``. Injecté pour les tests."""

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]: ...


class ProdChildRunner:
    """``ChildRunner`` de PROD : vrai subprocess confiné (couvert par analysis_integration)."""

    def __init__(self, cfg: AnalysisConfig) -> None:
        self._cfg = cfg

    def __call__(  # pragma: no cover
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]:
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            preexec_fn=self._confine,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # tuer le GROUPE (enfant + petit-fils ffprobe) ; race : si l'enfant est déjà
            # mort, getpgid lève ProcessLookupError → absorbée.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()  # reap : pas de zombie enfant
            return 0, b"", True
        return proc.returncode, stdout, False

    def _confine(self) -> None:  # pragma: no cover
        os.setsid()  # groupe de process dédié → on tue l'enfant ET son petit-fils ffprobe
        cfg = self._cfg
        resource.setrlimit(resource.RLIMIT_CPU, (cfg.rlimit_cpu_s, cfg.rlimit_cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (cfg.rlimit_as_bytes, cfg.rlimit_as_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (cfg.rlimit_fsize_bytes, cfg.rlimit_fsize_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (cfg.rlimit_nproc, cfg.rlimit_nproc))
        resource.setrlimit(resource.RLIMIT_NOFILE, (cfg.rlimit_nofile, cfg.rlimit_nofile))
        # pas de core dump : un crash de l'enfant/ffprobe ne doit pas écrire d'octets du
        # fichier hostile dans le cwd (DA8).
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_analysis(
    ed2k_hash: str, cfg: AnalysisConfig, runner: ChildRunner
) -> tuple[str, dict[str, object], list[object]]:
    """Spawne l'enfant pour ``ed2k_hash`` ; rend ``(verdict, real_meta, checks)`` (DA6)."""
    argv = [sys.executable, "-m", _CHILD_MODULE, ed2k_hash]
    scratch = tempfile.mkdtemp(prefix="analysis-")
    try:
        returncode, stdout, timed_out = runner(
            argv, cwd=scratch, env=_minimal_env(cfg), timeout=cfg.timeout_s
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return egress.parse(stdout, returncode, timed_out, cfg)


def _minimal_env(cfg: AnalysisConfig) -> dict[str, str]:
    """Env EXPLICITE minimal pour l'enfant (DA8) — ne fuit JAMAIS ``os.environ``."""
    return {
        "QUARANTINE_DIR": cfg.quarantine_dir,
        "ENABLED_CHECKS": ",".join(cfg.enabled_checks),
        "FFPROBE_PATH": cfg.ffprobe_path,
        "HEADER_BYTES": str(cfg.header_bytes),
        "ANALYSIS_TIMEOUT_S": str(cfg.timeout_s),
        "PATH": _MINIMAL_PATH,
    }
