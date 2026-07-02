"""``ffprobe`` check (analysis spec §5 — DA10): the heart of ``real_meta``.

``probe`` invokes ffprobe via an INJECTABLE ``FfprobeRunner`` (prod = real subprocess; tests =
canned JSON) with FIXED flags, parses the JSON DEFENSIVELY (``.get(...)`` everywhere; ffprobe's
``format`` numeric fields are STRINGS → parsed to float/int in a try/except, field omitted if
missing/unparsable). Status: exit ≠ 0, empty/unreadable/non-object JSON, empty/absent
``streams``, or no audio/video stream → ``suspicious`` (claims to be a media, is not one);
otherwise ``clean`` + ``real_meta``. ``ffprobe`` runs as a grandchild under the child's
rlimits/timeout/group (spec §4/§12) — an ffprobe that loops is killed and yields ``suspicious``.
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
    """Run ffprobe and return ``(returncode, stdout)``. Injected for tests."""

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]: ...


class ProdFfprobeRunner:
    """PROD ``FfprobeRunner``: real ``subprocess.run`` (covered by analysis_integration)."""

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
    """Probe ``path`` via ``runner``; return ``CheckOutcome`` (status + ``real_meta``)."""
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
    # ffprobe already returns these fields (width/height/channels) as JSON ints.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_int(value: object) -> int | None:
    # ffprobe returns duration/bit_rate/size/sample_rate as STRINGS → defensive parse.
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
