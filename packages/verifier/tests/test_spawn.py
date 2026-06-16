import contextlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from download_verifier.config import AnalysisConfig
from download_verifier.spawn import ProdChildRunner, run_analysis

_CFG = AnalysisConfig.from_env({"QUARANTINE_DIR": "/quar", "ENABLED_CHECKS": "type_sniff,ffprobe"})
_HASH = "a" * 32
_VALID_EGRESS = b'{"verdict": "clean", "real_meta": {"container": "mp4"}, "checks": []}'


class _RecordingRunner:
    """ChildRunner injecté : capture argv/cwd/env/timeout, rend un (rc, stdout, timed_out) canné."""

    def __init__(self, returncode: int, stdout: bytes, timed_out: bool) -> None:
        self._result = (returncode, stdout, timed_out)
        self.argv: Sequence[str] = ()
        self.cwd = ""
        self.env: Mapping[str, str] = {}
        self.timeout = 0.0
        self.cwd_existed_during_call = False

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]:
        self.argv = list(argv)
        self.cwd = cwd
        self.env = dict(env)
        self.timeout = timeout
        self.cwd_existed_during_call = Path(cwd).is_dir()
        return self._result


def test_valid_child_output_is_parsed() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    verdict, real_meta, checks = run_analysis(_HASH, _CFG, runner)
    assert verdict == "clean"
    assert real_meta == {"container": "mp4"}
    assert checks == []


def test_timed_out_child_is_suspicious() -> None:
    assert run_analysis(_HASH, _CFG, _RecordingRunner(0, b"", True)) == ("suspicious", {}, [])


def test_nonzero_exit_child_is_suspicious() -> None:
    assert run_analysis(_HASH, _CFG, _RecordingRunner(1, b"", False)) == ("suspicious", {}, [])


def test_oversized_child_output_is_suspicious() -> None:
    huge = b'{"verdict":"clean","real_meta":{},"checks":[]}' + b" " * (_CFG.egress_cap_bytes + 1)
    assert run_analysis(_HASH, _CFG, _RecordingRunner(0, huge, False)) == ("suspicious", {}, [])


def test_argv_targets_the_child_module_with_hash() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert runner.argv == [sys.executable, "-m", "download_verifier.analysis_child", _HASH]


def test_timeout_is_passed_from_config() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert runner.timeout == _CFG.timeout_s


def test_cwd_is_a_real_temp_dir_during_call_and_removed_after() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert runner.cwd_existed_during_call is True
    assert not Path(runner.cwd).exists()  # supprimé en finally


def test_temp_dir_is_removed_even_when_runner_raises() -> None:
    captured: list[str] = []

    class _BoomRunner:
        def __call__(
            self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
        ) -> tuple[int, bytes, bool]:
            captured.append(cwd)
            raise RuntimeError("boom")

    with contextlib.suppress(RuntimeError):
        run_analysis(_HASH, _CFG, _BoomRunner())
    assert captured  # le runner a bien été appelé
    assert not Path(captured[0]).exists()  # tmpdir nettoyé malgré l'exception


def test_minimal_env_contains_only_whitelisted_vars() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert runner.env["QUARANTINE_DIR"] == "/quar"
    assert runner.env["ENABLED_CHECKS"] == "type_sniff,ffprobe"
    assert runner.env["FFPROBE_PATH"] == _CFG.ffprobe_path
    assert runner.env["CLAMSCAN_PATH"] == _CFG.clamscan_path
    assert runner.env["CLAMAV_DB_DIR"] == _CFG.clamav_db_dir
    assert runner.env["HEADER_BYTES"] == str(_CFG.header_bytes)
    assert runner.env["ANALYSIS_TIMEOUT_S"] == str(_CFG.timeout_s)
    assert set(runner.env) == {
        "QUARANTINE_DIR",
        "ENABLED_CHECKS",
        "FFPROBE_PATH",
        "CLAMSCAN_PATH",
        "CLAMAV_DB_DIR",
        "HEADER_BYTES",
        "ANALYSIS_TIMEOUT_S",
        "PATH",
    }


def test_minimal_env_does_not_leak_parent_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_VPN_TOKEN", "do-not-leak")
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert "SECRET_VPN_TOKEN" not in runner.env


def test_prod_child_runner_constructs() -> None:
    # le constructeur n'est pas pragma ; __call__ (vrai subprocess) l'est.
    assert isinstance(ProdChildRunner(_CFG), ProdChildRunner)
