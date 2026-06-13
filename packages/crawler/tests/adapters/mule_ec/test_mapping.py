from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import EcTag, hash16_tag, string_tag, uint_tag
from emule_indexer.adapters.mule_ec.mapping import map_search_results
from emule_indexer.domain.observation import FileObservation

_HASH = bytes(range(16))
_HASH_HEX = _HASH.hex()


def _entry(children: tuple[EcTag, ...]) -> EcTag:
    # EC_TAG_SEARCHFILE : valeur propre = ECID (identifiant de session VOLATIL, piège 13).
    return EcTag(codes.EC_TAG_SEARCHFILE, codes.EC_TAGTYPE_UINT8, b"\x07", children)


def _full_entry() -> EcTag:
    return _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro 062A.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 234567890),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT, 5),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER, 2),
            uint_tag(codes.EC_TAG_PARTFILE_STATUS, 0),  # non mappé → raw_meta
            string_tag(0x0999, "mystère"),  # tag INCONNU → raw_meta, jamais une erreur
        )
    )


def test_maps_a_complete_entry_with_capture_all_raw_meta() -> None:
    observations, skipped = map_search_results((_full_entry(),), "keroro")
    assert skipped == 0
    assert observations == (
        FileObservation(
            ed2k_hash=_HASH_HEX,
            filename="Keroro 062A.avi",
            size_bytes=234567890,
            source_count=5,
            complete_source_count=2,
            keyword="keroro",
            media_length_sec=None,  # EC n'expose AUCUN tag média (réf. §5) — None attendu
            bitrate_kbps=None,
            codec=None,
            file_type=None,
            raw_meta=(("0x0308", "0"), ("0x0999", "mystère")),
        ),
    )


def test_source_counts_default_to_zero_when_absent() -> None:
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
        )
    )
    observations, skipped = map_search_results((entry,), "keroro")
    assert skipped == 0
    assert observations[0].source_count == 0
    assert observations[0].complete_source_count == 0
    assert observations[0].raw_meta == ()


def test_skips_entries_missing_hash_name_or_size_without_failing_the_batch() -> None:
    name = string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi")
    size = uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1)
    hsh = hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH)
    no_hash = _entry((name, size))
    no_name = _entry((size, hsh))
    no_size = _entry((name, hsh))
    observations, skipped = map_search_results((no_hash, _full_entry(), no_name, no_size), "k")
    assert skipped == 3  # le mapper COMPTE les écartés (spec §6)
    assert len(observations) == 1  # une entrée pourrie ne fait JAMAIS échouer le lot


def test_skips_entry_with_malformed_mandatory_tag() -> None:
    # Hash au mauvais type/longueur : entrée inexploitable → écartée, pas d'exception.
    bad_hash = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, b"\x01\x02"),
        )
    )
    observations, skipped = map_search_results((bad_hash,), "k")
    assert observations == ()
    assert skipped == 1


def test_ignores_non_searchfile_top_level_tags() -> None:
    stray = string_tag(codes.EC_TAG_STRING, "bruit")
    observations, skipped = map_search_results((stray, _full_entry()), "k")
    assert len(observations) == 1
    assert skipped == 0  # un tag de premier niveau inattendu n'est PAS une entrée écartée


def test_raw_meta_captures_grandchildren_depth_first_in_wire_order() -> None:
    # Capture-all VRAI : un enfant non mappé qui porte des sous-tags ne perd pas son
    # sous-arbre — chaque nœud devient une paire, parcours profondeur d'abord, ordre wire.
    parent = uint_tag(0x0801, 7, children=(string_tag(0x0802, "fils"),))
    trailing = uint_tag(0x0803, 1)
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            parent,
            trailing,
        )
    )
    observations, skipped = map_search_results((entry,), "k")
    assert skipped == 0
    assert observations[0].raw_meta == (
        ("0x0801", "7"),
        ("0x0802", "fils"),  # le petit-fils suit son parent, AVANT le frère suivant
        ("0x0803", "1"),
    )


def test_search_parent_ecid_appears_nowhere_in_output() -> None:
    # Piège 13 : EC_TAG_SEARCH_PARENT pointe l'ECID d'une AUTRE entrée — identifiant de
    # session volatil, jamais persisté (il casserait la dédup inter-sessions du plan A).
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            uint_tag(codes.EC_TAG_SEARCH_PARENT, 9),
        )
    )
    observations, skipped = map_search_results((entry,), "k")
    assert skipped == 0
    assert observations[0].raw_meta == ()


def test_malformed_optional_count_is_treated_as_absent_not_fatal() -> None:
    # Un compteur OPTIONNEL pourri ne coûte jamais l'observation (seuls hash/nom/taille
    # sont éliminatoires) : présent-mais-malformé = absent = 0.
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            EcTag(codes.EC_TAG_PARTFILE_SOURCE_COUNT, codes.EC_TAGTYPE_UINT32, b"\x01"),
        )
    )
    observations, skipped = map_search_results((entry,), "k")
    assert skipped == 0
    assert observations[0].source_count == 0
    assert observations[0].raw_meta == ()  # délibérément non ressuscité dans raw_meta


def test_duplicate_mapped_tag_second_occurrence_falls_into_raw_meta() -> None:
    # Seule la PREMIÈRE occurrence d'un nom mappé est consommée (= ce que lit find()) ;
    # un doublon hostile ne disparaît pas sans trace, il tombe dans raw_meta.
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "premier.avi"),
            string_tag(codes.EC_TAG_PARTFILE_NAME, "second.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
        )
    )
    observations, skipped = map_search_results((entry,), "k")
    assert skipped == 0
    assert observations[0].filename == "premier.avi"
    assert observations[0].raw_meta == (("0x0301", "second.avi"),)


def test_empty_filename_entry_is_kept() -> None:
    # Nom vide mais hash/taille valides : l'identité est le hash, on GARDE l'observation
    # (le matcher ne matchera simplement pas ce nom).
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, ""),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
        )
    )
    observations, skipped = map_search_results((entry,), "k")
    assert skipped == 0
    assert observations[0].filename == ""


def test_hash_is_rendered_as_lowercase_canonical_hex() -> None:
    # Pin du casing canonique : littéral, PAS recalculé via .hex().
    observations, _ = map_search_results((_full_entry(),), "k")
    assert observations[0].ed2k_hash == "000102030405060708090a0b0c0d0e0f"


def test_raw_meta_renders_ints_strings_and_falls_back_to_hex() -> None:
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            uint_tag(0x0701, 65535),  # entier → décimal
            string_tag(0x0702, "texte"),  # chaîne bien formée → texte
            EcTag(0x0703, codes.EC_TAGTYPE_STRING, b"sans-nul"),  # STRING cassé → hex
            EcTag(0x0704, codes.EC_TAGTYPE_UINT32, b"\x01"),  # largeur menteuse → hex
            EcTag(0x0705, codes.EC_TAGTYPE_CUSTOM, b"\xde\xad"),  # opaque → hex
        )
    )
    observations, _ = map_search_results((entry,), "k")
    assert observations[0].raw_meta == (
        ("0x0701", "65535"),
        ("0x0702", "texte"),
        ("0x0703", "73616e732d6e756c"),
        ("0x0704", "01"),
        ("0x0705", "dead"),
    )
