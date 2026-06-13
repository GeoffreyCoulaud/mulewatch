import pytest

from emule_indexer.domain.normalization import fold, normalize, tokenize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Télétoon", "teletoon"),  # diacritiques retirés (NFKD)
        ("KERORO", "keroro"),  # casse repliée
        ("café", "cafe"),  # accent composé décomposé puis retiré
        ("N°062A", "n 062a"),  # ° non-alphanumérique -> espace
        ("« Les demoiselles »", "les demoiselles"),  # guillemets -> espaces
        ("a__b  c", "a b c"),  # ponctuation + espaces multiples compactés
        ("  trim  ", "trim"),  # trim des bords
        ("", ""),  # chaîne vide
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
        ("N°062A.AVI", "n°062a.avi"),  # ponctuation/chiffres préservés, casse repliée
        ("Télétoon", "teletoon"),  # accents repliés sans toucher à la structure
        ("Straße", "strasse"),  # ß -> ss
        ("Sœur", "soeur"),  # œ -> oe
        ("Cæsar", "caesar"),  # æ -> ae
        ("21/09/2008", "21/09/2008"),  # séparateurs de date conservés
    ],
)
def test_fold_preserves_punctuation_and_digits(raw: str, expected: str) -> None:
    assert fold(raw) == expected


def test_tokenize_splits_normalized_string() -> None:
    assert tokenize("N°062A « Les demoiselles »") == ["n", "062a", "les", "demoiselles"]


def test_tokenize_empty_string_yields_no_tokens() -> None:
    assert tokenize("   ") == []
