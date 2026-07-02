import pytest

from catalog_matching.normalization import fold, normalize, tokenize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Télétoon", "teletoon"),  # diacritics stripped (NFKD)
        ("KERORO", "keroro"),  # case folded
        ("café", "cafe"),  # composed accent decomposed then stripped
        ("N°062A", "n 062a"),  # ° non-alphanumeric -> space
        ("« Les demoiselles »", "les demoiselles"),  # guillemets -> spaces
        ("a__b  c", "a b c"),  # punctuation + multiple spaces collapsed
        ("  trim  ", "trim"),  # trim the edges
        ("", ""),  # empty string
        ("Straße", "strasse"),  # casefold: ß -> ss
        ("Sœur", "soeur"),  # ligature œ -> oe
        ("Cæsar", "caesar"),  # ligature æ -> ae
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("N°062A.AVI", "n°062a.avi"),  # punctuation/digits preserved, case folded
        ("Télétoon", "teletoon"),  # accents folded without touching the structure
        ("Straße", "strasse"),  # ß -> ss
        ("Sœur", "soeur"),  # œ -> oe
        ("Cæsar", "caesar"),  # æ -> ae
        ("21/09/2008", "21/09/2008"),  # date separators kept
    ],
)
def test_fold_preserves_punctuation_and_digits(raw: str, expected: str) -> None:
    assert fold(raw) == expected


def test_tokenize_splits_normalized_string() -> None:
    assert tokenize("N°062A « Les demoiselles »") == ["n", "062a", "les", "demoiselles"]


def test_tokenize_empty_string_yields_no_tokens() -> None:
    assert tokenize("   ") == []
