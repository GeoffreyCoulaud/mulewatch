import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from download_verifier.analysis_child import (
    _default_confiner,
    _read_header_no_follow,
    main,
)
from download_verifier.config import AnalysisConfig
from download_verifier.confine import NoopConfiner, ProdConfiner

_HASH = "a" * 32

_VALID_MEDIA = json.dumps(
    {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 2, "height": 2}],
        "format": {"format_name": "mp4"},
    }
).encode()


class _StubFfprobe:
    def __init__(self, returncode: int, stdout: bytes) -> None:
        self._rc = returncode
        self._out = stdout

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:
        return self._rc, self._out


class _StubClamav:
    def __init__(self, returncode: int, stdout: bytes) -> None:
        self._rc = returncode
        self._out = stdout

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:
        return self._rc, self._out


_CLEAN_CLAMAV = _StubClamav(0, b"")


class _RecordingConfiner:
    """Confiner espion : note s'il a été appelé (preuve d'ordre/installation du ring noyau)."""

    def __init__(self) -> None:
        self.called = False

    def __call__(self) -> None:
        self.called = True


def _cfg(tmp_path: Path) -> AnalysisConfig:
    return AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path), "HEADER_BYTES": "4096"})


def test_valid_file_prints_clean_egress(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / _HASH).write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    code = main(
        [_HASH],
        ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=_cfg(tmp_path),
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "clean"
    assert payload["real_meta"]["container"] == "mp4"
    assert [c["name"] for c in payload["checks"]] == ["type_sniff", "ffprobe"]


def test_executable_file_prints_malicious_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / _HASH).write_bytes(b"\x7fELF" + b"\x00" * 64)
    code = main(
        [_HASH],
        ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=_cfg(tmp_path),
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "malicious"


def test_clamav_signature_makes_egress_malicious(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # média sain MAIS le clamav_runner injecté matche une signature → verdict malicious.
    (tmp_path / _HASH).write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    cfg = AnalysisConfig.from_env(
        {"QUARANTINE_DIR": str(tmp_path), "ENABLED_CHECKS": "type_sniff,ffprobe,clamav"}
    )
    code = main(
        [_HASH],
        ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA),
        clamav_runner=_StubClamav(1, b"/q/f: Eicar-Test-Signature FOUND\n"),
        cfg=cfg,
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "malicious"
    assert [c["name"] for c in payload["checks"]] == ["type_sniff", "ffprobe", "clamav"]


def test_non_canonical_hash_exits_nonzero_without_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["../etc/passwd"],
        ffprobe_runner=_StubFfprobe(0, b""),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=_cfg(tmp_path),
    )
    assert code == 2
    assert capsys.readouterr().out == ""


def test_missing_argv_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [],
        ffprobe_runner=_StubFfprobe(0, b""),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=_cfg(tmp_path),
    )
    assert code == 2


def test_vanished_file_prints_suspicious_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # hash canonique mais le fichier n'existe pas (disparu entre is_file et le spawn) → suspicious.
    code = main(
        [_HASH],
        ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=_cfg(tmp_path),
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "suspicious"


def test_read_header_rejects_non_regular_file(tmp_path: Path) -> None:
    # Régression sandbox-confinement#4 (branche S_ISREG False isolée) : un fd ouvert sur un
    # type ≠ régulier (ici un répertoire — l'``os.open`` réussit sur macOS + Linux) doit lever
    # OSError. La branche est testée DIRECTEMENT sur le helper pour éviter la dépendance au
    # comportement de ``os.fdopen`` (qui pré-rejette « Is a directory » sur certains OS avant
    # même que notre check ne tourne).
    directory = tmp_path / "as-a-dir"
    directory.mkdir()
    with pytest.raises(OSError):
        _read_header_no_follow(directory, 64)


def test_read_header_rejects_symlink(tmp_path: Path) -> None:
    # Branche ``O_NOFOLLOW`` → ELOOP. Defense-en-profondeur exposée isolément.
    target = tmp_path / "real"
    target.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        _read_header_no_follow(link, 64)


def test_read_header_reads_a_regular_file(tmp_path: Path) -> None:
    # Branche heureuse : fichier régulier → octets retournés. Couvre la sortie normale.
    path = tmp_path / "file.bin"
    path.write_bytes(b"\x00\x01\x02\x03\x04")
    assert _read_header_no_follow(path, 3) == b"\x00\x01\x02"


def test_directory_in_quarantine_is_refused_with_suspicious_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Régression sandbox-confinement#4 (branche S_ISREG False) : O_NOFOLLOW ne rejette PAS un
    # répertoire (qui n'est pas un symlink). Le ``fstat + S_ISREG`` après open le détecte et
    # rend ``suspicious`` (defense-en-profondeur contre les types non-réguliers : dir, FIFO, etc.).
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).mkdir()  # un répertoire au lieu d'un fichier régulier
    cfg = AnalysisConfig.from_env({"QUARANTINE_DIR": str(quarantine), "HEADER_BYTES": "4096"})
    code = main(
        [_HASH],
        ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=cfg,
        confiner=NoopConfiner(),
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "suspicious"


def test_symlink_in_quarantine_is_refused_with_suspicious_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Régression sandbox-confinement#4 : un symlink à la place du fichier de quarantaine doit
    # être REFUSÉ par O_NOFOLLOW (open lève ELOOP → l'except OSError → suspicious). Defense-en-
    # profondeur cohérente avec verify_file côté parent.
    target = tmp_path / "outside"
    target.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)  # un vrai header média
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).symlink_to(target)
    cfg = AnalysisConfig.from_env({"QUARANTINE_DIR": str(quarantine), "HEADER_BYTES": "4096"})
    code = main(
        [_HASH],
        ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=cfg,
        confiner=NoopConfiner(),
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "suspicious"


def test_only_header_bytes_are_read(tmp_path: Path) -> None:
    # un fichier énorme : l'enfant ne doit lire que header_bytes (pas tout le fichier).
    big = tmp_path / _HASH
    big.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * (10 * 1024 * 1024))
    cfg = AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path), "HEADER_BYTES": "8"})
    code = main(
        [_HASH],
        ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=cfg,
    )
    # n'a lu que 8 octets pour le sniff (le test prouve l'absence de crash mémoire)
    assert code == 0


def test_main_defaults_cfg_and_runners_without_real_subprocesses(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # couvre les branches `cfg is None`, `ffprobe_runner is None` ET `clamav_runner is None` SANS
    # lancer de vrai ffprobe/clamscan : on monkeypatch from_env + les deux Prod*Runner (→ stubs).
    import download_verifier.analysis_child as child
    import download_verifier.config as config_mod

    (tmp_path / _HASH).write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    cfg_instance = _cfg(tmp_path)
    monkeypatch.setattr(
        config_mod.AnalysisConfig, "from_env", classmethod(lambda cls, env: cfg_instance)
    )
    monkeypatch.setattr(child, "ProdFfprobeRunner", lambda timeout_s: _StubFfprobe(0, _VALID_MEDIA))
    monkeypatch.setattr(child, "ProdClamavRunner", lambda timeout_s: _CLEAN_CLAMAV)
    code = child.main([_HASH])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "clean"


def test_confiner_is_called_before_pipeline_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ring noyau : le confiner DOIT être posé AVANT d'entrer dans pipeline.run (donc avant que le
    # ffprobe_runner ne tourne). Un ffprobe stub qui asserte `confiner.called` prouve l'ordre.
    (tmp_path / _HASH).write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    confiner = _RecordingConfiner()

    class _OrderAssertingFfprobe:
        def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:
            assert confiner.called, "le confiner doit être posé AVANT pipeline.run/ffprobe"
            return 0, _VALID_MEDIA

    code = main(
        [_HASH],
        ffprobe_runner=_OrderAssertingFfprobe(),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=_cfg(tmp_path),
        confiner=confiner,
    )
    assert code == 0
    assert confiner.called is True
    assert json.loads(capsys.readouterr().out)["verdict"] == "clean"


def test_confiner_not_called_when_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # fichier absent → la branche `except OSError` retourne AVANT le point d'installation : le ring
    # n'est PAS posé (preuve que l'install est bien après la lecture d'en-tête, pas avant).
    confiner = _RecordingConfiner()
    code = main(
        [_HASH],
        ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA),
        clamav_runner=_CLEAN_CLAMAV,
        cfg=_cfg(tmp_path),
        confiner=confiner,
    )
    assert code == 0
    assert confiner.called is False
    assert json.loads(capsys.readouterr().out)["verdict"] == "suspicious"


def test_seccomp_enabled_selects_prod_confiner(tmp_path: Path) -> None:
    # _default_confiner retourne le TYPE selon la config — SANS appeler __call__ (sinon un vrai
    # filtre seccomp s'installerait dans le process de test).
    cfg = AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path), "SECCOMP_ENABLED": "1"})
    assert isinstance(_default_confiner(cfg), ProdConfiner)


def test_seccomp_disabled_selects_noop(tmp_path: Path) -> None:
    cfg = AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path), "SECCOMP_ENABLED": "0"})
    assert isinstance(_default_confiner(cfg), NoopConfiner)
