import re

import pytest

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
    assert result == r"prefix " + re.escape("C++ (demo)") + r" suffix"
    assert re.compile(result, re.ASCII).search("prefix C++ (demo) suffix") is not None


def test_interpolate_unknown_placeholder_raises() -> None:
    with pytest.raises(InterpolationError, match="bogus"):
        interpolate(r"a {bogus} b", _target())


def test_interpolate_former_number_placeholder_is_now_unknown() -> None:
    # {number} was renamed {absolute_number}: now unknown (fail-fast).
    with pytest.raises(InterpolationError, match="number"):
        interpolate(r"{number}", _target())


def test_interpolate_leaves_regex_quantifier_braces_untouched() -> None:
    # An RE2 quantifier {2,4} is NOT a placeholder and stays intact.
    assert interpolate(r"keroro\d{2,4}{absolute_number}", _target()) == r"keroro\d{2,4}62"


def test_interpolate_mono_gate_is_now_unknown() -> None:
    # {mono_gate} was retired with the multi-target fan-out: now an unknown placeholder.
    with pytest.raises(InterpolationError, match="mono_gate"):
        interpolate(r"{mono_gate}KEROW", _target())
