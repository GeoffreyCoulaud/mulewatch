from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import EcTag, hash16_tag, string_tag, uint_tag
from emule_indexer.adapters.mule_ec.mapping import map_search_results
from emule_indexer.domain.observation import FileObservation

_HASH = bytes(range(16))
_HASH_HEX = _HASH.hex()


def _entry(children: tuple[EcTag, ...]) -> EcTag:
    # EC_TAG_SEARCHFILE: own value = ECID (VOLATILE session identifier, pitfall 13).
    return EcTag(codes.EC_TAG_SEARCHFILE, codes.EC_TAGTYPE_UINT8, b"\x07", children)


def _full_entry() -> EcTag:
    return _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro 062A.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 234567890),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT, 5),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER, 2),
            uint_tag(codes.EC_TAG_PARTFILE_STATUS, 0),  # not mapped → raw_meta
            string_tag(0x0999, "mystère"),  # UNKNOWN tag → raw_meta, never an error
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
            media_length_sec=None,  # EC exposes NO media tag (ref. §5) — None expected
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
    assert skipped == 3  # the mapper COUNTS the discarded ones (spec §6)
    assert len(observations) == 1  # a corrupt entry NEVER fails the batch


def test_skips_entry_with_malformed_mandatory_tag() -> None:
    # Hash of wrong type/length: unusable entry → discarded, no exception.
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
    assert skipped == 0  # an unexpected top-level tag is NOT a discarded entry


def test_raw_meta_captures_grandchildren_depth_first_in_wire_order() -> None:
    # TRUE capture-all: an unmapped child that carries sub-tags does not lose its
    # subtree — each node becomes a pair, depth-first traversal, wire order.
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
        ("0x0802", "fils"),  # the grandchild follows its parent, BEFORE the next sibling
        ("0x0803", "1"),
    )


def test_search_parent_ecid_appears_nowhere_in_output() -> None:
    # Pitfall 13: EC_TAG_SEARCH_PARENT points to the ECID of ANOTHER entry — volatile
    # session identifier, never persisted (it would break plan A's cross-session dedup).
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
    # A corrupt OPTIONAL counter never costs the observation (only hash/name/size
    # are eliminating): present-but-malformed = absent = 0.
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
    assert observations[0].raw_meta == ()  # deliberately not resurrected into raw_meta


def test_duplicate_mapped_tag_second_occurrence_falls_into_raw_meta() -> None:
    # Only the FIRST occurrence of a mapped name is consumed (= what find() reads);
    # a hostile duplicate does not vanish without trace, it falls into raw_meta.
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
    # Empty name but valid hash/size: the identity is the hash, we KEEP the observation
    # (the matcher will simply not match this name).
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
    # Pins the canonical casing: literal, NOT recomputed via .hex().
    observations, _ = map_search_results((_full_entry(),), "k")
    assert observations[0].ed2k_hash == "000102030405060708090a0b0c0d0e0f"


def test_raw_meta_renders_ints_strings_and_falls_back_to_hex() -> None:
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            uint_tag(0x0701, 65535),  # integer → decimal
            string_tag(0x0702, "texte"),  # well-formed string → text
            EcTag(0x0703, codes.EC_TAGTYPE_STRING, b"sans-nul"),  # broken STRING → hex
            EcTag(0x0704, codes.EC_TAGTYPE_UINT32, b"\x01"),  # lying width → hex
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
