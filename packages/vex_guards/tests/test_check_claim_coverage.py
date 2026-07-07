import pytest

from vex_guards import check_claim_coverage
from vex_guards.descriptors import ModuleNotImported, PackageAbsent


def test_main_returns_zero_on_the_real_registry_and_vex() -> None:
    # The TDD payoff: Task 3's authored VEX and Task 2's registry were built to
    # agree, so the bijection check must be green with no injection at all.
    assert check_claim_coverage.main() == 0


def test_claim_without_a_guard_fails_and_names_the_cve(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        check_claim_coverage,
        "all_claims",
        lambda _paths: {"CVE-9999": "vulnerable_code_not_in_execute_path"},
    )
    monkeypatch.setattr(check_claim_coverage, "GUARDS", {})

    assert check_claim_coverage.main() == 1
    out = capsys.readouterr().out
    assert "::error::CVE-9999: claim has no guard" in out


def test_guard_without_a_claim_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(check_claim_coverage, "all_claims", lambda _paths: {})
    monkeypatch.setattr(
        check_claim_coverage,
        "GUARDS",
        {"CVE-8888": ModuleNotImported("tarfile")},
    )

    assert check_claim_coverage.main() == 1
    out = capsys.readouterr().out
    assert "::error::CVE-8888: guard has no claim" in out


def test_justification_that_mismatches_the_guard_family_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # An image guard expects ``vulnerable_code_not_present``; pairing it with the
    # source justification is the family mismatch the bijection check rejects.
    monkeypatch.setattr(
        check_claim_coverage,
        "all_claims",
        lambda _paths: {"CVE-7777": "vulnerable_code_not_in_execute_path"},
    )
    monkeypatch.setattr(
        check_claim_coverage,
        "GUARDS",
        {"CVE-7777": PackageAbsent("nghttp2")},
    )

    assert check_claim_coverage.main() == 1
    out = capsys.readouterr().out
    assert "::error::CVE-7777: justification" in out
    assert "does not match guard family" in out
