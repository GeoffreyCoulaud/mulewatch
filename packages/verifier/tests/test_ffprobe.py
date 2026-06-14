import json
from collections.abc import Sequence
from pathlib import Path

from download_verifier.checks.ffprobe import FfprobeRunner, ProdFfprobeRunner, probe
from download_verifier.config import AnalysisConfig

_CFG = AnalysisConfig.from_env({})

_VALID = {
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 576,
            "avg_frame_rate": "25/1",
            "tags": {"language": "fre"},
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "channels": 2,
            "sample_rate": "48000",
            "tags": {"language": "fre"},
        },
    ],
    "format": {
        "filename": "x",
        "nb_streams": 2,
        "format_name": "matroska,webm",
        "duration": "1294.500000",
        "size": "242884608",
        "bit_rate": "1500000",
        "tags": {"title": "t"},
    },
}


class _StubRunner:
    """FfprobeRunner injecté : rend un (returncode, stdout) canné, capture l'argv."""

    def __init__(self, returncode: int, stdout: bytes) -> None:
        self._returncode = returncode
        self._stdout = stdout
        self.calls: list[Sequence[str]] = []

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:
        self.calls.append(argv)
        return self._returncode, self._stdout


def test_prod_ffprobe_runner_constructs() -> None:
    runner = ProdFfprobeRunner(30.0)
    assert runner._timeout_s == 30.0


def test_stub_runner_satisfies_protocol() -> None:
    # mypy contrôle ici la conformité structurelle de _StubRunner au Protocol FfprobeRunner.
    runner: FfprobeRunner = _StubRunner(0, b"")
    assert callable(runner)


def test_valid_media_is_clean_with_real_meta() -> None:
    runner: FfprobeRunner = _StubRunner(0, json.dumps(_VALID).encode())
    outcome = probe(Path("/q/f"), runner, _CFG)
    assert outcome.name == "ffprobe"
    assert outcome.status == "clean"
    assert outcome.meta["container"] == "matroska,webm"
    assert outcome.meta["duration_s"] == 1294.5
    assert outcome.meta["bit_rate"] == 1500000
    assert outcome.meta["size_bytes"] == 242884608
    assert outcome.meta["video"] == {
        "codec": "h264",
        "width": 720,
        "height": 576,
        "frame_rate": "25/1",
    }
    assert outcome.meta["audio"] == [
        {"codec": "aac", "channels": 2, "sample_rate": 48000, "language": "fre"}
    ]


def test_argv_uses_frozen_flags_and_path() -> None:
    runner = _StubRunner(0, json.dumps(_VALID).encode())
    probe(Path("/quarantine/abc"), runner, _CFG)
    assert runner.calls[0] == [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "/quarantine/abc",
    ]


def test_nonzero_exit_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(1, b""), _CFG)
    assert outcome.status == "suspicious"


def test_malformed_json_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(0, b"{not json"), _CFG)
    assert outcome.status == "suspicious"


def test_empty_stdout_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(0, b""), _CFG)
    assert outcome.status == "suspicious"


def test_no_streams_key_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps({"format": {}}).encode()), _CFG)
    assert outcome.status == "suspicious"


def test_empty_streams_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps({"streams": []}).encode()), _CFG)
    assert outcome.status == "suspicious"


def test_streams_without_audio_or_video_is_suspicious() -> None:
    payload = {"streams": [{"codec_type": "subtitle", "codec_name": "srt"}], "format": {}}
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "suspicious"


def test_video_only_is_clean() -> None:
    payload = {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 320, "height": 240}],
        "format": {"format_name": "mp4"},
    }
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert outcome.meta["video"] == {"codec": "h264", "width": 320, "height": 240}
    assert "audio" not in outcome.meta


def test_audio_only_is_clean() -> None:
    payload = {
        "streams": [{"codec_type": "audio", "codec_name": "mp3", "channels": 2}],
        "format": {"format_name": "mp3"},
    }
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert outcome.meta["audio"] == [{"codec": "mp3", "channels": 2}]
    assert "video" not in outcome.meta


def test_unparsable_numeric_strings_are_omitted() -> None:
    payload = {
        "streams": [{"codec_type": "video", "codec_name": "h264"}],
        "format": {"format_name": "mkv", "duration": "N/A", "bit_rate": "", "size": "oops"},
    }
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert "duration_s" not in outcome.meta
    assert "bit_rate" not in outcome.meta
    assert "size_bytes" not in outcome.meta


def test_missing_format_keys_are_omitted() -> None:
    payload = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert "container" not in outcome.meta
    assert outcome.meta["video"] == {"codec": "h264"}


def test_non_object_json_is_suspicious() -> None:
    # JSON valide mais pas un objet (liste) → illisible → suspicious.
    outcome = probe(Path("/q/f"), _StubRunner(0, b"[1,2,3]"), _CFG)
    assert outcome.status == "suspicious"


def test_empty_video_stream_omits_video_key() -> None:
    # Stream vidéo sans aucun champ connu → dict vide → clé "video" omise (étiquetage honnête).
    # Un stream audio valide garde le statut clean.
    payload = {
        "streams": [
            {"codec_type": "video"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"format_name": "mkv"},
    }
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert "video" not in outcome.meta
    assert outcome.meta["audio"] == [{"codec": "aac"}]


def test_empty_audio_stream_omits_audio_key() -> None:
    # Stream audio sans aucun champ connu → dict vide → exclu de la liste ; liste vide → clé omise.
    # Un stream vidéo valide garde le statut clean.
    payload = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio"},
        ],
        "format": {"format_name": "mkv"},
    }
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert outcome.meta["video"] == {"codec": "h264"}
    assert "audio" not in outcome.meta
