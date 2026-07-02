"""D-analysis integration: REAL child spawn + REAL ffprobe (analysis spec §9 — DA9).

Dedicated run: ( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )
Dependency: ffmpeg/ffprobe present in the PATH (Plan F image in prod; dev = system package).
Proves the confinement FOR REAL (ProdChildRunner: re-exec, rlimits/setsid via _confine,
group timeout-kill, minimal env, close_fds) + ProdFfprobeRunner (real ffprobe) — all the code
under # pragma: no cover. Deselected by default, excluded from coverage.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from download_verifier.check import verify_file
from download_verifier.config import AnalysisConfig
from download_verifier.spawn import ProdChildRunner

pytestmark = pytest.mark.analysis_integration

_HASH = "a" * 32

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_NEEDS_FFMPEG = pytest.mark.skipif(
    _FFMPEG is None or _FFPROBE is None,
    reason="ffmpeg/ffprobe required for the D-analysis integration",
)


def _seccomp_is_feasible() -> bool:
    # The real kernel ring requires pyseccomp + libseccomp + a settable no_new_privs. We try to
    # install a minimal filter (allow by default) IN A DISPOSABLE CHILD (os.fork) so as NOT to
    # contaminate the test process — if it fails (missing lib / no_new_privs impossible), we skip.
    pid = os.fork()
    if pid == 0:  # pragma: no cover (child: never returns into the parent's coverage)
        try:
            import pyseccomp

            pyseccomp.SyscallFilter(pyseccomp.ALLOW).load()
            os._exit(0)
        except BaseException:
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


_NEEDS_SECCOMP = pytest.mark.skipif(
    not _seccomp_is_feasible(),
    reason="pyseccomp/libseccomp + no_new_privs required for the real kernel ring",
)


def _cfg(quarantine: Path, **overrides: object) -> AnalysisConfig:
    # RLIMIT_NPROC=4096: the default limit (64) blocks the ffprobe spawn in a dev
    # environment where the user already has many processes (RLIMIT_NPROC is global
    # for the UID — if the user already has >64, every fork() is refused).
    # 4096 stays a real confinement while letting the test run outside CI.
    env: dict[str, str] = {
        "QUARANTINE_DIR": str(quarantine),
        "FFPROBE_PATH": _FFPROBE or "ffprobe",
        "RLIMIT_NPROC": "4096",
    }
    env.update({key: str(value) for key, value in overrides.items()})
    return AnalysisConfig.from_env(env)


def _verify(quarantine: Path, cfg: AnalysisConfig) -> tuple[str, dict[str, object], list[object]]:
    # We strip the 4th element (``outcome``, observability#2) — the integration tests
    # only assert on the historical triple (verdict, real_meta, checks).
    verdict, real_meta, checks, _outcome = verify_file(
        quarantine / _HASH, {}, cfg=cfg, runner=ProdChildRunner(cfg)
    )
    return verdict, real_meta, checks


@_NEEDS_FFMPEG
def test_real_small_media_is_clean_with_real_meta(tmp_path: Path) -> None:
    # End-to-end smoke of the nominal `clean` path via the REAL confined re-exec
    # (ProdChildRunner): the ONLY real test that ends up `clean` (the others end
    # malicious/suspicious/error), so the only proof of OUR confinement's happy-path.
    # Does NOT validate ffprobe (third-party brick).
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    target = quarantine / _HASH
    assert _FFMPEG is not None
    # generate a real tiny media (1 s of solid color + an audio tone).
    # -f matroska: mandatory because the file has no extension (name = eD2k hash);
    # without an explicit format, ffmpeg refuses to choose a muxer ("Unable to choose an
    # output format") and exits with an error.
    subprocess.run(
        [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            "-f",
            "matroska",
            str(target),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    verdict, real_meta, checks = _verify(quarantine, _cfg(quarantine))
    assert verdict == "clean"
    assert real_meta.get("video") is not None
    assert real_meta.get("container") is not None
    check_dicts = cast(list[dict[str, object]], checks)
    assert {c["name"] for c in check_dicts} == {"type_sniff", "ffprobe"}


@_NEEDS_FFMPEG
def test_real_executable_is_malicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 256)
    assert _verify(quarantine, _cfg(quarantine))[0] == "malicious"


@_NEEDS_FFMPEG
def test_real_shebang_script_is_malicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"#!/bin/sh\necho hello\n")
    assert _verify(quarantine, _cfg(quarantine))[0] == "malicious"


@_NEEDS_FFMPEG
def test_real_plain_text_is_suspicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"this is not a media\n" * 16)
    assert _verify(quarantine, _cfg(quarantine))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_oversized_egress_is_suspicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"plain text\n")
    # tiny egress cap → the egress (even suspicious) exceeds it → suspicious (poison).
    assert _verify(quarantine, _cfg(quarantine, EGRESS_CAP_BYTES=1))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_timeout_is_suspicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"plain text\n")
    # timeout ~0 → the child is killed (killpg) before finishing → suspicious.
    assert _verify(quarantine, _cfg(quarantine, ANALYSIS_TIMEOUT_S=0.001))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_missing_file_is_error(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()  # no _HASH file
    assert _verify(quarantine, _cfg(quarantine))[0] == "error"


# --- kernel ring (real seccomp filter): the minimal net = "clean preserved under filter". --------


@_NEEDS_SECCOMP
@_NEEDS_FFMPEG
def test_real_clean_media_stays_clean_under_seccomp(tmp_path: Path) -> None:
    # PROOF that the real seccomp filter (SECCOMP_ENABLED=1) breaks NOTHING legitimate: a real
    # small healthy media stays `clean` with the kernel ring installed in the child. If the filter
    # killed/broke the analysis (ffprobe fork/exec, file read), the verdict would turn suspicious.
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    target = quarantine / _HASH
    assert _FFMPEG is not None
    subprocess.run(
        [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            "-f",
            "matroska",
            str(target),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    cfg = _cfg(quarantine, SECCOMP_ENABLED="1")
    verdict, real_meta, _ = _verify(quarantine, cfg)
    assert verdict == "clean"
    assert real_meta.get("container") is not None
