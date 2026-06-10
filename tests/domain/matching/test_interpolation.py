import datetime

import pytest
import re2

from emule_indexer.domain.matching.interpolation import (
    FRENCH_MONTHS,
    InterpolationError,
    date_alternation_pattern,
    interpolate,
)
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.normalization import fold


def test_french_months_are_accent_free_and_complete() -> None:
    assert FRENCH_MONTHS == {
        1: "janvier",
        2: "fevrier",
        3: "mars",
        4: "avril",
        5: "mai",
        6: "juin",
        7: "juillet",
        8: "aout",
        9: "septembre",
        10: "octobre",
        11: "novembre",
        12: "decembre",
    }


def test_date_alternation_matches_known_forms() -> None:
    pattern = date_alternation_pattern(datetime.date(2008, 9, 21))
    compiled = re2.compile(pattern)
    for text in (
        "diffuse le 21 septembre 2008 sur teletoon",
        "keroro 21/09/2008.avi",
        "2008-09-21 keroro.avi",
    ):
        assert compiled.search(fold(text)) is not None


def test_date_alternation_does_not_match_unrelated_date() -> None:
    pattern = date_alternation_pattern(datetime.date(2008, 9, 21))
    compiled = re2.compile(pattern)
    assert compiled.search(fold("2007-01-01 autre chose")) is None


def test_date_alternation_single_digit_day_not_matched_inside_larger_number() -> None:
    # Le jour 5 ne doit pas matcher dans "15/09/2008" (bord numérique \b).
    pattern = date_alternation_pattern(datetime.date(2008, 9, 5))
    compiled = re2.compile(pattern)
    assert compiled.search(fold("keroro 15/09/2008.avi")) is None
    # ... mais les formes légitimes du jour 5 matchent toujours :
    assert compiled.search(fold("keroro 5/09/2008.avi")) is not None
    assert compiled.search(fold("keroro 05/09/2008.avi")) is not None


def test_date_alternation_matches_dates_adjacent_to_release_separators() -> None:
    # Bords non-chiffres courants en P2P (_ , lettre, bord de chaîne) doivent matcher.
    pattern = date_alternation_pattern(datetime.date(2008, 9, 21))
    compiled = re2.compile(pattern)
    for text in (
        "keroro_21/09/2008.avi",  # underscore avant le jour
        "2008-09-21_keroro.mkv",  # underscore après le jour (forme ymd)
        "x21/09/2008",  # lettre collée au jour
        "21/09/2008",  # date seule, bords de chaîne
    ):
        assert compiled.search(fold(text)) is not None


def _target(broadcast_date: datetime.date | None = None) -> TargetSegment:
    return TargetSegment(
        season=2,
        number=62,
        segment="a",
        title="Les demoiselles",
        broadcast_date=broadcast_date,
    )


def test_interpolate_substitutes_number_and_segment_escaped() -> None:
    pattern = r"n[°o]?\s*0*{number}\s*{segment}"
    result = interpolate(pattern, _target())
    assert result == r"n[°o]?\s*0*62\s*A"


def test_interpolate_escapes_regex_special_title() -> None:
    target = TargetSegment(season=1, number=1, segment="a", title="C++ (demo)")
    result = interpolate(r"prefix {title} suffix", target)
    # re2.escape rend le titre littéral : '+', '(', ')' et l'espace sont échappés.
    assert result == r"prefix " + re2.escape("C++ (demo)") + r" suffix"
    # Le fragment échappé compile et matche le texte littéral exact.
    assert re2.compile(result).search("prefix C++ (demo) suffix") is not None


def test_interpolate_date_alt_inserts_raw_fragment() -> None:
    target = _target(broadcast_date=datetime.date(2008, 9, 21))
    result = interpolate(r"{date_alt}", target)
    assert result == date_alternation_pattern(datetime.date(2008, 9, 21))
    assert re2.compile(result).search(fold("21/09/2008")) is not None


def test_interpolate_unknown_placeholder_raises() -> None:
    with pytest.raises(InterpolationError, match="bogus"):
        interpolate(r"a {bogus} b", _target())


def test_interpolate_date_alt_without_date_raises() -> None:
    with pytest.raises(InterpolationError, match="date_alt"):
        interpolate(r"{date_alt}", _target(broadcast_date=None))


def test_interpolate_leaves_regex_quantifier_braces_untouched() -> None:
    # Un quantificateur RE2 {2,4} n'est PAS un placeholder et reste intact.
    pattern = r"keroro\d{2,4}{number}"
    assert interpolate(pattern, _target()) == r"keroro\d{2,4}62"
