import pytest

from emule_indexer.domain.normalization import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Télétoon", "teletoon"),            # diacritiques retirés (NFKD)
        ("KERORO", "keroro"),                # minuscules
        ("café", "cafe"),                    # accent composé décomposé puis retiré
        ("N°062A", "n 062a"),                # ° non-alphanumérique -> espace
        ("« Les demoiselles »", "les demoiselles"),  # guillemets -> espaces
        ("a__b  c", "a b c"),                # ponctuation + espaces multiples compactés
        ("  trim  ", "trim"),                # trim des bords
        ("", ""),                            # chaîne vide
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected
