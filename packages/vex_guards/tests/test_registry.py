from vex_guards.descriptors import family
from vex_guards.registry import GUARDS


def test_every_registered_guard_is_source_family() -> None:
    # The single shipped image carries only source-family claims; an image-family
    # guard here would be orphaned, which check_claim_coverage rejects.
    assert {family(g) for g in GUARDS.values()} == {"source"}
