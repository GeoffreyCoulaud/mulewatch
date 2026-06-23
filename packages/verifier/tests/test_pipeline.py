import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from download_verifier import pipeline
from download_verifier.config import AnalysisConfig

_BASE = AnalysisConfig.from_env({})


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

_VALID_MEDIA = json.dumps(
    {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1, "height": 1}],
        "format": {"format_name": "mp4"},
    }
).encode()


def _cfg(checks: tuple[str, ...]) -> AnalysisConfig:
    return AnalysisConfig.from_env({"ENABLED_CHECKS": ",".join(checks)})


def test_clean_media_aggregates_to_clean() -> None:
    verdict, real_meta, checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _CLEAN_CLAMAV,
        _BASE,
    )
    assert verdict == "clean"
    assert real_meta["container"] == "mp4"
    assert real_meta["sniffed_type"] is not None
    assert [c["name"] for c in checks] == ["type_sniff", "ffprobe"]
    assert {c["status"] for c in checks} == {"clean"}


def test_executable_header_makes_verdict_malicious() -> None:
    verdict, _real_meta, checks = pipeline.run(
        b"\x7fELF" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _CLEAN_CLAMAV,
        _BASE,
    )
    assert verdict == "malicious"  # type_sniff malicious écrase ffprobe clean
    statuses = {c["name"]: c["status"] for c in checks}
    assert statuses["type_sniff"] == "malicious"


def test_non_media_makes_verdict_suspicious() -> None:
    # en-tête texte (type_sniff clean) + ffprobe échoue (suspicious) → worst = suspicious.
    verdict, _real_meta, _checks = pipeline.run(
        b"plain text not a media\n",
        Path("/q/f"),
        _StubFfprobe(1, b""),
        _CLEAN_CLAMAV,
        _BASE,
    )
    assert verdict == "suspicious"


def test_enabled_checks_selects_only_type_sniff() -> None:
    verdict, real_meta, checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(1, b""),  # ffprobe échouerait, mais il est DÉSACTIVÉ
        _CLEAN_CLAMAV,
        _cfg(("type_sniff",)),
    )
    assert verdict == "clean"
    assert [c["name"] for c in checks] == ["type_sniff"]
    assert "container" not in real_meta  # ffprobe n'a pas tourné


def test_enabled_checks_selects_only_ffprobe() -> None:
    verdict, real_meta, checks = pipeline.run(
        b"\x7fELF" + b"\x00" * 64,  # serait malicious, mais type_sniff est DÉSACTIVÉ
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _CLEAN_CLAMAV,
        _cfg(("ffprobe",)),
    )
    assert verdict == "clean"
    assert [c["name"] for c in checks] == ["ffprobe"]
    assert "sniffed_type" not in real_meta


def test_unknown_check_name_is_ignored() -> None:
    # Défense en profondeur (DA4) : from_env REJETTE désormais un nom inconnu au chargement
    # (config-validation#4) — donc on contourne from_env via dataclasses.replace pour vérifier
    # que MÊME si un « clamavv » atteignait le pipeline, il serait ignoré (pas de crash, pas de
    # check fantôme).
    verdict, _real_meta, checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _CLEAN_CLAMAV,
        replace(_BASE, enabled_checks=("type_sniff", "clamavv", "ffprobe")),
    )
    assert verdict == "clean"
    assert [c["name"] for c in checks] == ["type_sniff", "ffprobe"]


def test_clamav_malicious_overrides_clean_media() -> None:
    # média clean (type_sniff clean + ffprobe clean) MAIS clamav matche une signature → malicious.
    verdict, _real_meta, checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _StubClamav(1, b"/q/f: Eicar-Test-Signature FOUND\n"),
        _cfg(("type_sniff", "ffprobe", "clamav")),
    )
    assert verdict == "malicious"
    assert [c["name"] for c in checks] == ["type_sniff", "ffprobe", "clamav"]
    statuses = {c["name"]: c["status"] for c in checks}
    assert statuses["clamav"] == "malicious"


def test_clamav_clean_keeps_clean() -> None:
    verdict, _real_meta, checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _CLEAN_CLAMAV,
        _cfg(("type_sniff", "ffprobe", "clamav")),
    )
    assert verdict == "clean"
    assert [c["name"] for c in checks] == ["type_sniff", "ffprobe", "clamav"]


def test_clamav_suspicious_aggregates() -> None:
    # ffprobe clean + clamav erreur (rc 2 → suspicious) → worst = suspicious.
    verdict, _real_meta, _checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _StubClamav(2, b"ERROR: cannot open database"),
        _cfg(("ffprobe", "clamav")),
    )
    assert verdict == "suspicious"


def test_enabled_checks_selects_only_clamav() -> None:
    verdict, _real_meta, checks = pipeline.run(
        b"\x7fELF" + b"\x00" * 64,  # serait malicious, mais type_sniff est DÉSACTIVÉ
        Path("/q/f"),
        _StubFfprobe(1, b""),  # échouerait, mais ffprobe est DÉSACTIVÉ
        _CLEAN_CLAMAV,
        _cfg(("clamav",)),
    )
    assert verdict == "clean"
    assert [c["name"] for c in checks] == ["clamav"]
