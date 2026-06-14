"""Check ``ffprobe`` (spec analysis §5 — DA10) : cœur de ``real_meta``.

``probe`` invoque ffprobe via un ``FfprobeRunner`` INJECTABLE (prod = subprocess réel ; tests =
JSON canné) avec des flags FIGÉS, parse le JSON DÉFENSIVEMENT (``.get(...)`` partout ; les champs
numériques de ``format`` sont des STRINGS chez ffprobe → parse en float/int dans un try/except,
champ omis s'il manque/n'est pas parsable). Status : exit ≠ 0, JSON vide/illisible/non-objet,
``streams`` vide/absent, ou aucun flux audio/vidéo → ``suspicious`` (prétend être un média, n'en
est pas un) ; sinon ``clean`` + ``real_meta``. ``ffprobe`` tourne en petit-fils sous les
rlimits/timeout/groupe de l'enfant (spec §4/§12) — un ffprobe qui boucle est tué et donne
``suspicious``.
"""

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from download_verifier.checks.base import CheckOutcome
from download_verifier.config import AnalysisConfig

_MEDIA_STREAM_TYPES = frozenset({"video", "audio"})


class FfprobeRunner(Protocol):
    """Exécute ffprobe et rend ``(returncode, stdout)``. Injecté pour les tests."""

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]: ...


class ProdFfprobeRunner:
    """``FfprobeRunner`` de PROD : vrai ``subprocess.run`` (couvert par analysis_integration)."""

    def __init__(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:  # pragma: no cover
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=self._timeout_s,
            check=False,
        )
        return completed.returncode, completed.stdout


def probe(path: Path, runner: FfprobeRunner, cfg: AnalysisConfig) -> CheckOutcome:
    """Sonde ``path`` via ``runner`` ; rend ``CheckOutcome`` (status + ``real_meta``)."""
    argv = [
        cfg.ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    returncode, stdout = runner(argv)
    if returncode != 0:
        return _suspicious()
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return _suspicious()
    if not isinstance(payload, dict):
        return _suspicious()
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams:
        return _suspicious()
    typed = [s for s in streams if isinstance(s, dict)]
    if not any(s.get("codec_type") in _MEDIA_STREAM_TYPES for s in typed):
        return _suspicious()
    return CheckOutcome(name="ffprobe", status="clean", meta=_build_meta(payload, typed))


def _suspicious() -> CheckOutcome:
    return CheckOutcome(name="ffprobe", status="suspicious", meta={})


def _build_meta(payload: dict[str, object], streams: list[dict[str, object]]) -> dict[str, object]:
    meta: dict[str, object] = {}
    fmt = payload.get("format")
    if isinstance(fmt, dict):
        _put(meta, "container", _as_str(fmt.get("format_name")))
        _put(meta, "duration_s", _as_float(fmt.get("duration")))
        _put(meta, "bit_rate", _as_int(fmt.get("bit_rate")))
        _put(meta, "size_bytes", _as_int(fmt.get("size")))
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is not None:
        video_meta = _video_meta(video)
        if video_meta:
            meta["video"] = video_meta
    audios = [m for s in streams if s.get("codec_type") == "audio" if (m := _audio_meta(s))]
    if audios:
        meta["audio"] = audios
    return meta


def _video_meta(stream: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    _put(out, "codec", _as_str(stream.get("codec_name")))
    _put(out, "width", _as_plain_int(stream.get("width")))
    _put(out, "height", _as_plain_int(stream.get("height")))
    _put(out, "frame_rate", _as_str(stream.get("avg_frame_rate")))
    return out


def _audio_meta(stream: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    _put(out, "codec", _as_str(stream.get("codec_name")))
    _put(out, "channels", _as_plain_int(stream.get("channels")))
    _put(out, "sample_rate", _as_int(stream.get("sample_rate")))
    tags = stream.get("tags")
    if isinstance(tags, dict):
        _put(out, "language", _as_str(tags.get("language")))
    return out


def _put(meta: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        meta[key] = value


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_plain_int(value: object) -> int | None:
    # ffprobe rend déjà ces champs (width/height/channels) comme des ints JSON.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_int(value: object) -> int | None:
    # ffprobe rend duration/bit_rate/size/sample_rate comme des STRINGS → parse défensif.
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _as_float(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None
