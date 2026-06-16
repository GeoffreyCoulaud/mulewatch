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

_CLAMSCAN = shutil.which("clamscan")
# Dossier de la base de signatures réelle : pointable vers le volume du sidecar freshclam via
# CLAMAV_DB_DIR ; défaut = /var/lib/clamav (freshclam local). On exige clamscan ET une base
# présente (au moins un *.cvd/*.cld — sinon clamscan rend rc≥2, le scan EICAR ne prouverait rien).
_CLAMAV_DB_DIR = os.environ.get("CLAMAV_DB_DIR", "/var/lib/clamav")


def _has_signature_base(db_dir: str) -> bool:
    base = Path(db_dir)
    return base.is_dir() and (any(base.glob("*.cvd")) or any(base.glob("*.cld")))


_NEEDS_CLAMAV = pytest.mark.skipif(
    _CLAMSCAN is None or not _has_signature_base(_CLAMAV_DB_DIR),
    reason=(
        "clamscan + une base de signatures requis pour l'intégration clamav "
        f"(CLAMAV_DB_DIR={_CLAMAV_DB_DIR})"
    ),
)

# Le fichier de test antivirus standard EICAR (inerte, reconnu par tous les moteurs).
_EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _clamav_cfg(quarantine: Path, **overrides: object) -> AnalysisConfig:
    env: dict[str, str] = {
        "QUARANTINE_DIR": str(quarantine),
        "FFPROBE_PATH": _FFPROBE or "ffprobe",
        "CLAMSCAN_PATH": _CLAMSCAN or "clamscan",
        "CLAMAV_DB_DIR": _CLAMAV_DB_DIR,
        # RLIMIT_NPROC élevé pour le dev bare-metal (cf. _cfg) ; les rlimits AS/CPU sont relâchés
        # AUTOMATIQUEMENT par from_env dès que clamav est dans ENABLED_CHECKS (§6.2).
        "RLIMIT_NPROC": "4096",
    }
    env.update({key: str(value) for key, value in overrides.items()})
    return AnalysisConfig.from_env(env)


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


# --- clamav (3ᵉ source de verdict) : vrai clamscan + vraie base (Geoffrey). -------------------


@_NEEDS_CLAMAV
def test_real_eicar_is_malicious(tmp_path: Path) -> None:
    # LE test de bout en bout : re-exec child confiné (rlimits relâchés §6.2) + vrai clamscan +
    # vraie base → l'EICAR matche → verdict malicious.
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(_EICAR)
    cfg = _clamav_cfg(quarantine, ENABLED_CHECKS="clamav")
    assert _verify(quarantine, cfg)[0] == "malicious"


@_NEEDS_CLAMAV
@_NEEDS_FFMPEG
def test_real_clean_media_passes_clamav(tmp_path: Path) -> None:
    # un vrai petit média sain, les 3 checks actifs → clean. PROUVE l'ordre de grandeur des rlimits
    # relâchés §6.1 : si clamscan OOM/CPU-kill sur le rlimit AS/CPU, le verdict tomberait suspicious
    # et CE test échouerait → signal pour relever RLIMIT_AS_BYTES_CLAMAV / RLIMIT_CPU_S_CLAMAV.
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
    cfg = _clamav_cfg(quarantine, ENABLED_CHECKS="type_sniff,ffprobe,clamav")
    assert _verify(quarantine, cfg)[0] == "clean"


@_NEEDS_CLAMAV
def test_real_missing_base_is_suspicious(tmp_path: Path) -> None:
    # base absente (CLAMAV_DB_DIR vide) → clamscan rend rc≥2 → suspicious (défensif).
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"plain harmless text\n")
    empty_db = tmp_path / "empty-db"
    empty_db.mkdir()
    cfg = _clamav_cfg(quarantine, ENABLED_CHECKS="clamav", CLAMAV_DB_DIR=str(empty_db))
    assert _verify(quarantine, cfg)[0] == "suspicious"
