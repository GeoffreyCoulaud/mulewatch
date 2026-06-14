import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from download_verifier.analysis_child import main
from download_verifier.config import AnalysisConfig

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


def _cfg(tmp_path: Path) -> AnalysisConfig:
    return AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path), "HEADER_BYTES": "4096"})


def test_valid_file_prints_clean_egress(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / _HASH).write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    code = main([_HASH], ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA), cfg=_cfg(tmp_path))
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "clean"
    assert payload["real_meta"]["container"] == "mp4"
    assert [c["name"] for c in payload["checks"]] == ["type_sniff", "ffprobe"]


def test_executable_file_prints_malicious_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / _HASH).write_bytes(b"\x7fELF" + b"\x00" * 64)
    code = main([_HASH], ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA), cfg=_cfg(tmp_path))
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "malicious"


def test_non_canonical_hash_exits_nonzero_without_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["../etc/passwd"], ffprobe_runner=_StubFfprobe(0, b""), cfg=_cfg(tmp_path))
    assert code == 2
    assert capsys.readouterr().out == ""


def test_missing_argv_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main([], ffprobe_runner=_StubFfprobe(0, b""), cfg=_cfg(tmp_path))
    assert code == 2


def test_vanished_file_prints_suspicious_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # hash canonique mais le fichier n'existe pas (disparu entre is_file et le spawn) → suspicious.
    code = main([_HASH], ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA), cfg=_cfg(tmp_path))
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "suspicious"


def test_only_header_bytes_are_read(tmp_path: Path) -> None:
    # un fichier énorme : l'enfant ne doit lire que header_bytes (pas tout le fichier).
    big = tmp_path / _HASH
    big.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * (10 * 1024 * 1024))
    cfg = AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path), "HEADER_BYTES": "8"})
    code = main([_HASH], ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA), cfg=cfg)
    # n'a lu que 8 octets pour le sniff (le test prouve l'absence de crash mémoire)
    assert code == 0


def test_main_defaults_cfg_and_runner_without_real_ffprobe(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # couvre les branches `cfg is None` et `ffprobe_runner is None` SANS lancer un vrai ffprobe :
    # on monkeypatch from_env (→ cfg de test) et ProdFfprobeRunner (→ stub).
    import download_verifier.analysis_child as child
    import download_verifier.config as config_mod

    (tmp_path / _HASH).write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    cfg_instance = _cfg(tmp_path)
    monkeypatch.setattr(
        config_mod.AnalysisConfig, "from_env", classmethod(lambda cls, env: cfg_instance)
    )
    monkeypatch.setattr(child, "ProdFfprobeRunner", lambda timeout_s: _StubFfprobe(0, _VALID_MEDIA))
    code = child.main([_HASH])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "clean"
