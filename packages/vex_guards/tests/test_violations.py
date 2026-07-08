import dataclasses

import pytest

from vex_guards.violations import Violation


def test_violation_carries_cve_message_and_location() -> None:
    violation = Violation(
        cve="CVE-2026-11940",
        message="tarfile is imported at src/mulewatch/foo.py",
        location="packages/crawler/src/mulewatch/foo.py",
    )
    assert violation.cve == "CVE-2026-11940"
    assert violation.message == "tarfile is imported at src/mulewatch/foo.py"
    assert violation.location == "packages/crawler/src/mulewatch/foo.py"


def test_violation_is_frozen() -> None:
    violation = Violation(cve="CVE-2026-11940", message="boom", location="a/b.py")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(violation, "cve", "CVE-2026-11972")
