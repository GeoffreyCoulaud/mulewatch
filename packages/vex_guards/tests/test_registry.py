from vex_guards.descriptors import ModuleNotImported, family
from vex_guards.registry import GUARDS


def test_tarfile_cves_share_the_module_guard() -> None:
    for cve in ("CVE-2026-11940", "CVE-2026-11972", "CVE-2026-4360"):
        assert GUARDS[cve] == ModuleNotImported("tarfile")


def test_every_registered_guard_is_source_family() -> None:
    # The single shipped image carries only source-family claims; an image-family
    # guard here would be orphaned, which check_claim_coverage rejects.
    assert {family(g) for g in GUARDS.values()} == {"source"}
