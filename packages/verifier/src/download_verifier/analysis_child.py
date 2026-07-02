"""Disposable analysis child (analysis spec §4 — DA5/DA8), CHILD side.

``main``: re-validates the canonical hash (defense-in-depth anti-traversal, DA8), reads AT MOST
``cfg.header_bytes`` bytes of the RO file (NOT the whole file — potentially huge hostile content,
DA10), runs ``pipeline.run`` (type_sniff on the header + ffprobe/clamav on the path), prints
``json.dumps({"verdict","real_meta","checks"})`` on stdout, returns 0. Non-canonical hash / missing
argv → returns 2 without egress. Missing/unreadable file (vanished after the parent's ``is_file``) →
VALID ``suspicious`` egress (poison, consistent with DA6). No stack trace in the egress
(best-effort).

This module is executed by re-exec (``python -m download_verifier.analysis_child <hash>``); the
parent (``spawn.py``) confines it (rlimits/setsid/minimal env). In PROD the ``__main__`` reads the
config from the minimal env and uses the real ``ProdFfprobeRunner``/``ProdClamavRunner``. The KERNEL
RING (seccomp-bpf filter, ``confine.py``) is installed AFTER reading the RO header and JUST BEFORE
``pipeline.run`` (the ``Confiner`` is injectable; default = ``ProdConfiner`` if seccomp enabled).
"""

import errno
import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

from download_verifier import pipeline
from download_verifier.checks.clamav import ClamavRunner, ProdClamavRunner
from download_verifier.checks.ffprobe import FfprobeRunner, ProdFfprobeRunner
from download_verifier.config import AnalysisConfig
from download_verifier.confine import Confiner, NoopConfiner, ProdConfiner

_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")


def _default_confiner(config: AnalysisConfig) -> Confiner:
    """Select the ``Confiner`` per config — RETURNS the instance (without calling it)."""
    return ProdConfiner() if config.seccomp_enabled else NoopConfiner()


def _read_header_no_follow(path: Path, header_bytes: int) -> bytes:
    """Read ``header_bytes`` bytes of the file, REFUSING symlinks and non-regular types.

    Sandbox-confinement#4: ``O_NOFOLLOW`` rejects a symlink (raises ``ELOOP``); ``fstat +
    S_ISREG`` rejects any other type (dir, FIFO, socket, device) — the check is done on the
    ``fd`` (not via the path) so it is TOCTOU-immune. Defense-in-depth: a compromised amuled
    sharing the quarantine RW could drop a symlink BETWEEN the parent's ``S_ISREG`` (``check.py``)
    and the open here. Raises ``OSError`` on any refusal; called under a ``try/except OSError``
    that maps to a ``suspicious`` egress.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "non-regular quarantine file")
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = -1  # ownership transferred to fdopen (the ``with`` closes via __exit__)
            return handle.read(header_bytes)
    finally:
        if fd != -1:
            os.close(fd)


def main(
    argv: Sequence[str],
    *,
    ffprobe_runner: FfprobeRunner | None = None,
    clamav_runner: ClamavRunner | None = None,
    cfg: AnalysisConfig | None = None,
    confiner: Confiner | None = None,
) -> int:
    """Analyze ``quarantine/<argv[0]>`` and print the JSON egress; return the exit code."""
    config = cfg if cfg is not None else AnalysisConfig.from_env(os.environ)
    runner = ffprobe_runner if ffprobe_runner is not None else ProdFfprobeRunner(config.timeout_s)
    clamav = clamav_runner if clamav_runner is not None else ProdClamavRunner(config.timeout_s)
    confine = confiner if confiner is not None else _default_confiner(config)
    if len(argv) != 1 or _CANONICAL_HASH_RE.fullmatch(argv[0]) is None:
        return 2
    path = Path(config.quarantine_dir) / argv[0]
    try:
        header = _read_header_no_follow(path, config.header_bytes)
    except OSError:
        _emit("suspicious", {}, [])
        return 0
    confine()  # KERNEL RING: install seccomp HERE (after RO read, before pipeline.run, §7).
    verdict, real_meta, checks = pipeline.run(header, path, runner, clamav, config)
    _emit(verdict, real_meta, checks)
    return 0


def _emit(verdict: str, real_meta: dict[str, object], checks: list[dict[str, object]]) -> None:
    sys.stdout.write(json.dumps({"verdict": verdict, "real_meta": real_meta, "checks": checks}))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
