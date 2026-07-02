"""Analysis child spawn (analysis spec §4 — DA5/DA8/DA9), PARENT side.

``run_analysis`` re-execs a disposable Python child per file (NOT ``os.fork``): minimal argv,
disposable ``tempfile.mkdtemp()`` cwd (removed in ``finally`` even on exception), EXPLICIT minimal
env (we do NOT inherit ``os.environ`` — secrets/VPN; we only pass QUARANTINE_DIR + the checks
config + a minimal PATH). The ``ChildRunner`` is INJECTABLE: the PROD impl does the real
``subprocess.Popen`` (``close_fds=True``, ``preexec_fn=_confine`` = rlimits + setsid, group
timeout-kill via ``killpg``) — these system lines are ``# pragma: no cover`` (covered by
analysis_integration). The outcome mapping (stdout/timeout/exit) is delegated to ``egress.parse``
(defensive, DA6). The parent NEVER reads bytes of the file (DA8).
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
# Bounded reap window post-timeout (sandbox-confinement#2): a compromised descendant that
# escapes via ``setsid()`` can keep stdout open (EOF never arrives) → ``communicate()`` would
# block indefinitely, freezing the worker (cf. the event loop). We bound the reap window and
# switch to a targeted kill + bounded wait if needed. A runaway orphan remains possible but no
# longer blocks us (cgroups bound its impact).
_REAP_TIMEOUT_S = 2.0


class ChildRunner(Protocol):
    """Run the child and return ``(returncode, stdout, timed_out)``. Injected for tests."""

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]: ...


class ProdChildRunner:
    """PROD ``ChildRunner``: real confined subprocess (covered by analysis_integration)."""

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
            # kill the GROUP (child + ffprobe grandchild); race: if the child is already
            # dead, getpgid raises ProcessLookupError → absorbed.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            # Close the PARENT read-end of the stdout pipe (sandbox-confinement#2): a
            # descendant that escaped via setsid keeps its write-end open → EOF never
            # arrives → ``communicate()`` loops. By cutting it parent-side we free
            # ourselves; the descendant will write into a broken pipe (SIGPIPE/EPIPE).
            if proc.stdout is not None:
                proc.stdout.close()
            try:
                proc.wait(timeout=_REAP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # Last resort: targeted SIGKILL + bounded wait. If even that fails, the
                # child stays a zombie (extremely unlikely after killpg+kill) — we free
                # ourselves anyway, cgroups bound the possible orphan.
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=_REAP_TIMEOUT_S)
            return 0, b"", True
        return proc.returncode, stdout, False

    def _confine(self) -> None:  # pragma: no cover
        os.setsid()  # dedicated process group → we kill the child AND its ffprobe grandchild
        cfg = self._cfg
        resource.setrlimit(resource.RLIMIT_CPU, (cfg.rlimit_cpu_s, cfg.rlimit_cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (cfg.rlimit_as_bytes, cfg.rlimit_as_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (cfg.rlimit_fsize_bytes, cfg.rlimit_fsize_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (cfg.rlimit_nproc, cfg.rlimit_nproc))
        resource.setrlimit(resource.RLIMIT_NOFILE, (cfg.rlimit_nofile, cfg.rlimit_nofile))
        # no core dump: a child/ffprobe crash must not write bytes of the hostile file into
        # the cwd (DA8).
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_analysis(
    ed2k_hash: str, cfg: AnalysisConfig, runner: ChildRunner
) -> tuple[str, dict[str, object], list[object], egress.ChildOutcome]:
    """Spawn the child; return ``(verdict, real_meta, checks, outcome)`` (DA6 + observability#2).

    ``outcome`` is the outcome's TECHNICAL CATEGORY (``ok``/``timeout``/``nonzero_exit``/
    ``egress_overflow``/``malformed``), exposed as a metric in ``app.py`` — orthogonal to the
    business verdict. In a mass incident it lets you see the cause behind a rise in
    ``suspicious`` (without it, operators only have a blind aggregate).
    """
    argv = [sys.executable, "-m", _CHILD_MODULE, ed2k_hash]
    scratch = tempfile.mkdtemp(prefix="analysis-")
    try:
        returncode, stdout, timed_out = runner(
            argv, cwd=scratch, env=_minimal_env(cfg), timeout=cfg.timeout_s
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    verdict, real_meta, checks = egress.parse(stdout, returncode, timed_out, cfg)
    outcome = egress.classify_outcome(stdout, returncode, timed_out, cfg)
    return verdict, real_meta, checks, outcome


def _minimal_env(cfg: AnalysisConfig) -> dict[str, str]:
    """EXPLICIT minimal env for the child (DA8) — NEVER leaks ``os.environ``."""
    return {
        "QUARANTINE_DIR": cfg.quarantine_dir,
        "ENABLED_CHECKS": ",".join(cfg.enabled_checks),
        "FFPROBE_PATH": cfg.ffprobe_path,
        "CLAMSCAN_PATH": cfg.clamscan_path,
        "CLAMAV_DB_DIR": cfg.clamav_db_dir,
        "HEADER_BYTES": str(cfg.header_bytes),
        "ANALYSIS_TIMEOUT_S": str(cfg.timeout_s),
        # the child re-resolves its config from the env: we pass it the kernel ring state.
        "SECCOMP_ENABLED": "1" if cfg.seccomp_enabled else "0",
        "PATH": _MINIMAL_PATH,
    }
