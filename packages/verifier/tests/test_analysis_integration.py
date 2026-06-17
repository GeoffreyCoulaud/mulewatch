"""Intégration D-analysis : spawn RÉEL de l'enfant + VRAI ffprobe (spec analysis §9 — DA9).

Run dédié : ( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )
Dépendance : ffmpeg/ffprobe présents dans le PATH (image Plan F en prod ; dev = paquet système).
Prouve POUR DE VRAI le confinement (ProdChildRunner : re-exec, rlimits/setsid via _confine,
timeout-kill du groupe, env minimal, close_fds) + ProdFfprobeRunner (vrai ffprobe) — tout le code
sous # pragma: no cover. Désélectionné par défaut, exclu de la coverage.
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
    reason="ffmpeg/ffprobe requis pour l'intégration D-analysis",
)


def _seccomp_is_feasible() -> bool:
    # Le ring noyau réel exige pyseccomp + libseccomp + un no_new_privs posable. On essaie de poser
    # un filtre minimal (allow par défaut) DANS UN ENFANT JETABLE (os.fork) pour ne PAS contaminer
    # le process de test — si l'install échoue (lib absente / no_new_privs impossible), on skip.
    pid = os.fork()
    if pid == 0:  # pragma: no cover (enfant : ne revient jamais dans la couverture du parent)
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
    reason="pyseccomp/libseccomp + no_new_privs requis pour le ring noyau réel",
)


def _cfg(quarantine: Path, **overrides: object) -> AnalysisConfig:
    # RLIMIT_NPROC=4096 : la limite par défaut (64) bloque le spawn de ffprobe dans un
    # environnement dev où l'utilisateur a déjà de nombreux processus (RLIMIT_NPROC est
    # global pour l'UID — si l'utilisateur en a déjà >64, tout fork() est refusé).
    # 4096 reste un confinement réel tout en permettant au test de tourner hors-CI.
    env: dict[str, str] = {
        "QUARANTINE_DIR": str(quarantine),
        "FFPROBE_PATH": _FFPROBE or "ffprobe",
        "RLIMIT_NPROC": "4096",
    }
    env.update({key: str(value) for key, value in overrides.items()})
    return AnalysisConfig.from_env(env)


def _verify(quarantine: Path, cfg: AnalysisConfig) -> tuple[str, dict[str, object], list[object]]:
    return verify_file(quarantine / _HASH, {}, cfg=cfg, runner=ProdChildRunner(cfg))


@_NEEDS_FFMPEG
def test_real_small_media_is_clean_with_real_meta(tmp_path: Path) -> None:
    # Smoke du chemin nominal `clean` de bout en bout via le re-exec confiné RÉEL
    # (ProdChildRunner) : le SEUL test réel qui aboutit `clean` (les autres finissent
    # malicious/suspicious/error), donc la seule preuve du happy-path de NOTRE confinement.
    # Ne valide PAS ffprobe (brique tierce).
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    target = quarantine / _HASH
    assert _FFMPEG is not None
    # génère un vrai média minuscule (1 s de couleur unie + un ton audio).
    # -f matroska : obligatoire car le fichier n'a pas d'extension (nom = hash eD2k) ;
    # sans format explicite, ffmpeg refuse de choisir un muxer ("Unable to choose an
    # output format") et sort en erreur.
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
    (quarantine / _HASH).write_bytes(b"ceci n'est pas un media\n" * 16)
    assert _verify(quarantine, _cfg(quarantine))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_oversized_egress_is_suspicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"plain text\n")
    # cap d'égress minuscule → l'égress (même suspicious) dépasse → suspicious (poison).
    assert _verify(quarantine, _cfg(quarantine, EGRESS_CAP_BYTES=1))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_timeout_is_suspicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"plain text\n")
    # timeout ~0 → l'enfant est tué (killpg) avant de finir → suspicious.
    assert _verify(quarantine, _cfg(quarantine, ANALYSIS_TIMEOUT_S=0.001))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_missing_file_is_error(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()  # pas de fichier _HASH
    assert _verify(quarantine, _cfg(quarantine))[0] == "error"


# --- ring noyau (filtre seccomp réel) : le filet minimal = « clean préservé sous filtre ». -------


@_NEEDS_SECCOMP
@_NEEDS_FFMPEG
def test_real_clean_media_stays_clean_under_seccomp(tmp_path: Path) -> None:
    # PREUVE que le filtre seccomp réel (SECCOMP_ENABLED=1) ne casse RIEN de légitime : un vrai
    # petit média sain reste `clean` avec le ring noyau posé dans l'enfant. Si le filtre tuait/
    # cassait l'analyse (ffprobe fork/exec, lecture du fichier), le verdict tomberait suspicious.
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
