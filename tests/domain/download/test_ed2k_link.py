from emule_indexer.domain.download.ed2k_link import build_ed2k_link

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"


def test_simple_name_builds_canonical_link() -> None:
    link = build_ed2k_link("Keroro 062A.avi", 12345, _HASH)
    assert link == f"ed2k://|file|Keroro%20062A.avi|12345|{_HASH}|/"


def test_pipe_in_name_is_escaped() -> None:
    # le '|' est le séparateur de champs du lien : il DOIT être échappé (%7C) sinon le
    # cadrage casse (un nom hostile ne doit jamais injecter un champ).
    link = build_ed2k_link("weird|name.avi", 99, _HASH)
    assert "%7C" in link
    assert link.count("|") == 5  # uniquement les 5 séparateurs structurels du lien


def test_non_ascii_name_is_utf8_percent_encoded() -> None:
    link = build_ed2k_link("accentué.mkv", 1, _HASH)
    assert "accentu%C3%A9.mkv" in link


def test_zero_size_is_serialized() -> None:
    link = build_ed2k_link("x.avi", 0, _HASH)
    assert link == f"ed2k://|file|x.avi|0|{_HASH}|/"


def test_hash_is_placed_verbatim() -> None:
    link = build_ed2k_link("a.bin", 5, _HASH)
    assert link.endswith(f"|{_HASH}|/")
