from collections.abc import Mapping, Sequence
from pathlib import Path

from download_verifier.check import verify_file
from download_verifier.config import AnalysisConfig

_HASH = "a" * 32
_CLEAN_EGRESS = b'{"verdict": "clean", "real_meta": {"container": "mp4"}, "checks": []}'


class _StubChildRunner:
    """ChildRunner injected: returns a canned (rc, stdout, timed_out), captures the argv hash."""

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
    verdict, real_meta, checks, outcome = verify_file(
        tmp_path / "absent", {}, cfg=_cfg(tmp_path), runner=runner
    )
    # outcome=None: no child ran → no technical outcome to classify (observability#2).
    assert (verdict, real_meta, checks, outcome) == ("error", {}, [], None)
    assert runner.seen_hash is None  # the child is NOT spawned for a missing file


def test_directory_is_error_without_spawn(tmp_path: Path) -> None:
    directory = tmp_path / "dir"
    directory.mkdir()
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    assert verify_file(directory, {}, cfg=_cfg(tmp_path), runner=runner)[0] == "error"
    assert runner.seen_hash is None


def test_existing_file_runs_pipeline_and_returns_verdict(tmp_path: Path) -> None:
    target = tmp_path / _HASH
    target.write_bytes(b"x")  # the parent NEVER reads these bytes (the child does — stubbed here)
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    verdict, real_meta, checks, outcome = verify_file(target, {}, cfg=_cfg(tmp_path), runner=runner)
    assert verdict == "clean"
    assert real_meta == {"container": "mp4"}
    assert checks == []
    assert outcome == "ok"
    assert runner.seen_hash == _HASH  # the child was spawned with the right hash


def test_child_failure_maps_to_suspicious(tmp_path: Path) -> None:
    target = tmp_path / _HASH
    target.write_bytes(b"x")
    runner = _StubChildRunner(1, b"", False)
    assert verify_file(target, {}, cfg=_cfg(tmp_path), runner=runner)[0] == "suspicious"


def test_default_runner_is_prod(tmp_path: Path) -> None:
    # cfg is REQUIRED (resolved at boot, error-boundary#0); call WITHOUT a runner to cover the PROD
    # ProdChildRunner default: missing file → error (no spawn, so no real subprocess).
    assert verify_file(tmp_path / "absent", {}, cfg=_cfg(tmp_path))[0] == "error"


def test_symlink_at_quarantine_path_is_error_without_spawn(tmp_path: Path) -> None:
    # sandbox-confinement#4 regression: a symlink (even to a regular file) must be
    # REFUSED — verify_file returns "error", no spawn. ``is_file()`` followed the symlink and
    # passed the guard; we switch to ``os.lstat + S_ISREG`` to refuse any non-regular type.
    # Defense-in-depth: if amuled (RW on the quarantine) were compromised, it could drop
    # a symlink there pointing to an arbitrary file on the verifier fs.
    target = tmp_path / "real.bin"
    target.write_bytes(b"x")
    link = tmp_path / _HASH
    link.symlink_to(target)
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    verdict, real_meta, checks, outcome = verify_file(link, {}, cfg=_cfg(tmp_path), runner=runner)
    assert (verdict, real_meta, checks, outcome) == ("error", {}, [], None)
    assert runner.seen_hash is None
