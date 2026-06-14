from collections.abc import Mapping, Sequence
from pathlib import Path

from download_verifier.check import verify_file
from download_verifier.config import AnalysisConfig

_HASH = "a" * 32
_CLEAN_EGRESS = b'{"verdict": "clean", "real_meta": {"container": "mp4"}, "checks": []}'


class _StubChildRunner:
    """ChildRunner injecté : rend un (rc, stdout, timed_out) canné, capture le hash de l'argv."""

    def __init__(self, returncode: int, stdout: bytes, timed_out: bool) -> None:
        self._result = (returncode, stdout, timed_out)
        self.seen_hash: str | None = None

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]:
        self.seen_hash = argv[-1]
        return self._result


def _cfg(tmp_path: Path) -> AnalysisConfig:
    return AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path)})


def test_missing_file_is_error_without_spawn(tmp_path: Path) -> None:
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    verdict, real_meta, checks = verify_file(
        tmp_path / "absent", {}, cfg=_cfg(tmp_path), runner=runner
    )
    assert (verdict, real_meta, checks) == ("error", {}, [])
    assert runner.seen_hash is None  # l'enfant n'est PAS spawné pour un fichier absent


def test_directory_is_error_without_spawn(tmp_path: Path) -> None:
    directory = tmp_path / "dir"
    directory.mkdir()
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    assert verify_file(directory, {}, cfg=_cfg(tmp_path), runner=runner)[0] == "error"
    assert runner.seen_hash is None


def test_existing_file_runs_pipeline_and_returns_verdict(tmp_path: Path) -> None:
    target = tmp_path / _HASH
    target.write_bytes(b"x")  # le parent ne lit JAMAIS ces octets (l'enfant si — stubé ici)
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    verdict, real_meta, checks = verify_file(target, {}, cfg=_cfg(tmp_path), runner=runner)
    assert verdict == "clean"
    assert real_meta == {"container": "mp4"}
    assert checks == []
    assert runner.seen_hash == _HASH  # l'enfant a été spawné avec le bon hash


def test_child_failure_maps_to_suspicious(tmp_path: Path) -> None:
    target = tmp_path / _HASH
    target.write_bytes(b"x")
    runner = _StubChildRunner(1, b"", False)
    assert verify_file(target, {}, cfg=_cfg(tmp_path), runner=runner)[0] == "suspicious"


def test_default_cfg_and_runner_are_prod(tmp_path: Path) -> None:
    # appel SANS cfg/runner pour couvrir les défauts PROD : fichier absent → error (pas de spawn).
    assert verify_file(tmp_path / "absent", {})[0] == "error"
