from collections.abc import Sequence
from pathlib import Path

from download_verifier.checks.clamav import ClamavRunner, ProdClamavRunner, scan
from download_verifier.config import AnalysisConfig

_CFG = AnalysisConfig.from_env({})


class _StubRunner:
    """ClamavRunner injected: returns a canned (returncode, stdout), captures the argv."""

    def __init__(self, returncode: int, stdout: bytes) -> None:
        self._returncode = returncode
        self._stdout = stdout
        self.calls: list[Sequence[str]] = []

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:
        self.calls.append(argv)
        return self._returncode, self._stdout


def test_prod_clamav_runner_constructs() -> None:
    runner = ProdClamavRunner(30.0)
    assert runner._timeout_s == 30.0


def test_stub_runner_satisfies_protocol() -> None:
    # mypy here checks _StubRunner's structural conformance to the ClamavRunner Protocol.
    runner: ClamavRunner = _StubRunner(0, b"")
    assert callable(runner)


def test_clean_when_rc_zero() -> None:
    outcome = scan(Path("/q/f"), _StubRunner(0, b""), _CFG)
    assert outcome.name == "clamav"
    assert outcome.status == "clean"
    assert outcome.meta == {}


def test_malicious_when_rc_one_with_signature() -> None:
    runner = _StubRunner(1, b"/q/f: Eicar-Test-Signature FOUND\n")
    outcome = scan(Path("/q/f"), runner, _CFG)
    assert outcome.status == "malicious"
    assert outcome.meta["clamav_signature"] == "Eicar-Test-Signature"


def test_malicious_when_rc_one_without_parsable_signature() -> None:
    outcome = scan(Path("/q/f"), _StubRunner(1, b"garbage"), _CFG)
    assert outcome.status == "malicious"
    assert "clamav_signature" not in outcome.meta


def test_suspicious_when_rc_two() -> None:
    outcome = scan(Path("/q/f"), _StubRunner(2, b"ERROR: cannot open database"), _CFG)
    assert outcome.status == "suspicious"
    assert outcome.meta == {}


def test_suspicious_when_rc_other() -> None:
    # keeps the else branch of "rc >= 2": any other code (here 40) → suspicious.
    outcome = scan(Path("/q/f"), _StubRunner(40, b""), _CFG)
    assert outcome.status == "suspicious"


def test_argv_uses_frozen_flags_and_db_and_path() -> None:
    # Explicit bounds passed to clamscan (sandbox-confinement#3): defense-in-depth against
    # zip-bomb / recursion / scan-too-long. Calibrated NOT to hinder a media of several hundred
    # MiB (cf. the module's _CLAMSCAN_LIMITS constants).
    runner = _StubRunner(0, b"")
    scan(Path("/quarantine/abc"), runner, _CFG)
    assert runner.calls[0] == [
        "clamscan",
        "--no-summary",
        "--stdout",
        "--max-scansize=2048M",
        "--max-filesize=2048M",
        "--max-recursion=10",
        "--max-files=1000",
        "--max-scantime=120000",
        "--database",
        "/clamav-db",
        "/quarantine/abc",
    ]


def test_signature_line_without_colon_space_returns_none() -> None:
    # "FOUND" present but no ": " → the `": " in line` branch is False → no signature.
    outcome = scan(Path("/q/f"), _StubRunner(1, b"NoColon FOUND"), _CFG)
    assert outcome.status == "malicious"
    assert "clamav_signature" not in outcome.meta


def test_signature_line_with_empty_token_returns_none() -> None:
    # ": FOUND" → the token between ": " and "FOUND" is empty → `or None` → no signature.
    outcome = scan(Path("/q/f"), _StubRunner(1, b"/q/f:  FOUND"), _CFG)
    assert outcome.status == "malicious"
    assert "clamav_signature" not in outcome.meta
