import pytest
import re2

from catalog_matching.interpolation import InterpolationError, interpolate
from catalog_matching.models import TargetSegment


def _target() -> TargetSegment:
    return TargetSegment(
        season=2, seasonal_number=11, absolute_number=62, segment="a", title="Les demoiselles"
    )


def test_interpolate_substitutes_absolute_number_and_segment_escaped() -> None:
    pattern = r"n[°o]?\s*0*{absolute_number}\s*{segment}"
    assert interpolate(pattern, _target()) == r"n[°o]?\s*0*62\s*A"


def test_interpolate_substitutes_season_and_seasonal_number() -> None:
    assert interpolate(r"s0*{season}\s*e0*{seasonal_number}", _target()) == r"s0*2\s*e0*11"


def test_interpolate_escapes_regex_special_title() -> None:
    target = TargetSegment(
        season=1, seasonal_number=1, absolute_number=1, segment="a", title="C++ (demo)"
    )
    result = interpolate(r"prefix {title} suffix", target)
    assert result == r"prefix " + re2.escape("C++ (demo)") + r" suffix"
    assert re2.compile(result).search("prefix C++ (demo) suffix") is not None


def test_interpolate_unknown_placeholder_raises() -> None:
    with pytest.raises(InterpolationError, match="bogus"):
        interpolate(r"a {bogus} b", _target())


def test_interpolate_former_number_placeholder_is_now_unknown() -> None:
    # {number} a été renommé {absolute_number} : désormais inconnu (fail-fast).
    with pytest.raises(InterpolationError, match="number"):
        interpolate(r"{number}", _target())


def test_interpolate_leaves_regex_quantifier_braces_untouched() -> None:
    # Un quantificateur RE2 {2,4} n'est PAS un placeholder et reste intact.
    assert interpolate(r"keroro\d{2,4}{absolute_number}", _target()) == r"keroro\d{2,4}62"


def test_interpolate_mono_gate_empty_for_sole_segment() -> None:
    t = TargetSegment(
        season=1, seasonal_number=10, absolute_number=10, segment="a", title="x", sole_segment=True
    )
    assert interpolate(r"{mono_gate}KEROW", t) == "KEROW"


def test_interpolate_mono_gate_never_match_for_multi_segment() -> None:
    t = TargetSegment(season=2, seasonal_number=11, absolute_number=62, segment="a", title="x")
    assert interpolate(r"{mono_gate}KEROW", t) == r"[^\s\S]KEROW"
