from vex_guards.descriptors import (
    ModuleNotImported,
    PackageAbsent,
    PackageMinVersion,
    family,
)
from vex_guards.registry import GUARDS


def test_registry_has_the_eleven_advisories() -> None:
    assert set(GUARDS) == {
        "CVE-2026-11940",
        "CVE-2026-11972",
        "CVE-2026-4360",
        "CVE-2026-0864",
        "CVE-2025-15366",
        "CVE-2025-15367",
        "CVE-2026-12003",
        "CVE-2025-60876",
        "CVE-2016-1405",
        "CVE-2026-58055",
        "GHSA-cq8v-f236-94qc",
    }


def test_tarfile_cves_share_the_module_guard() -> None:
    for cve in ("CVE-2026-11940", "CVE-2026-11972", "CVE-2026-4360"):
        assert GUARDS[cve] == ModuleNotImported("tarfile")


def test_image_family_guards() -> None:
    assert GUARDS["CVE-2026-58055"] == PackageAbsent("nghttp2")
    assert GUARDS["CVE-2016-1405"] == PackageMinVersion("clamav", "0.99")
    assert family(GUARDS["CVE-2026-58055"]) == "image"
