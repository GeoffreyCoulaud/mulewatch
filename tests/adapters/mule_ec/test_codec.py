import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    empty_tag,
    hash16_tag,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import EcProtocolError

# ---------------------------------------------------------------- builders


def test_uint_tag_encodes_shortest_width_like_amule_initint() -> None:
    # Réf. §3 : « les entiers sont toujours encodés au plus court » (InitInt, ECTag.cpp:207-221).
    assert uint_tag(0x0001, 0xAB) == EcTag(0x0001, codes.EC_TAGTYPE_UINT8, b"\xab")
    assert uint_tag(0x0001, 0x0204) == EcTag(0x0001, codes.EC_TAGTYPE_UINT16, b"\x02\x04")
    assert uint_tag(0x0001, 0x02000001) == EcTag(
        0x0001, codes.EC_TAGTYPE_UINT32, b"\x02\x00\x00\x01"
    )
    assert uint_tag(0x0001, 0x6B5E8D3A12F0C4D7) == EcTag(
        0x0001, codes.EC_TAGTYPE_UINT64, b"\x6b\x5e\x8d\x3a\x12\xf0\xc4\xd7"
    )
    assert uint_tag(0x0001, 0) == EcTag(0x0001, codes.EC_TAGTYPE_UINT8, b"\x00")
    # Bornes exactes de transition de largeur (régression interop : sel d'auth tronqué sinon).
    assert uint_tag(0x0001, 0xFF) == EcTag(0x0001, codes.EC_TAGTYPE_UINT8, b"\xff")
    assert uint_tag(0x0001, 0x100) == EcTag(0x0001, codes.EC_TAGTYPE_UINT16, b"\x01\x00")
    assert uint_tag(0x0001, 0xFFFF) == EcTag(0x0001, codes.EC_TAGTYPE_UINT16, b"\xff\xff")
    assert uint_tag(0x0001, 0x10000) == EcTag(0x0001, codes.EC_TAGTYPE_UINT32, b"\x00\x01\x00\x00")
    assert uint_tag(0x0001, 0xFFFFFFFF) == EcTag(
        0x0001, codes.EC_TAGTYPE_UINT32, b"\xff\xff\xff\xff"
    )
    assert uint_tag(0x0001, 0x100000000) == EcTag(
        0x0001, codes.EC_TAGTYPE_UINT64, b"\x00\x00\x00\x01\x00\x00\x00\x00"
    )


def test_uint_tag_rejects_negative_and_oversized_values() -> None:
    with pytest.raises(EcProtocolError):
        uint_tag(0x0001, -1)
    with pytest.raises(EcProtocolError):
        uint_tag(0x0001, 1 << 64)


def test_string_tag_appends_the_final_nul_inside_the_value() -> None:
    # Réf. §3/§9 piège 10 : UTF-8 + NUL final INCLUS dans TAGLEN.
    tag = string_tag(codes.EC_TAG_CLIENT_NAME, "probe")
    assert tag == EcTag(codes.EC_TAG_CLIENT_NAME, codes.EC_TAGTYPE_STRING, b"probe\x00")
    assert string_tag(codes.EC_TAG_SEARCH_FILE_TYPE, "").value == b"\x00"


def test_hash16_tag_requires_exactly_16_bytes() -> None:
    digest = bytes(range(16))
    assert hash16_tag(codes.EC_TAG_PASSWD_HASH, digest) == EcTag(
        codes.EC_TAG_PASSWD_HASH, codes.EC_TAGTYPE_HASH16, digest
    )
    with pytest.raises(EcProtocolError):
        hash16_tag(codes.EC_TAG_PASSWD_HASH, b"\x00" * 15)


def test_empty_tag_is_custom_type_with_no_value() -> None:
    # Réf. §2 : CECEmptyTag -> type CUSTOM (1), TAGLEN 0 — la forme des tags EC_TAG_CAN_*.
    assert empty_tag(codes.EC_TAG_CAN_ZLIB) == EcTag(
        codes.EC_TAG_CAN_ZLIB, codes.EC_TAGTYPE_CUSTOM, b""
    )


# ---------------------------------------------------------------- accesseurs


def test_int_value_reads_all_four_widths() -> None:
    # Réf. §9 piège 4 : lire « un entier » en acceptant les 4 largeurs (équivalent GetInt()).
    assert EcTag(0x000B, codes.EC_TAGTYPE_UINT8, b"\xab").int_value() == 0xAB
    assert EcTag(0x000B, codes.EC_TAGTYPE_UINT16, b"\x02\x04").int_value() == 0x0204
    assert EcTag(0x000B, codes.EC_TAGTYPE_UINT32, b"\x02\x00\x00\x01").int_value() == 0x02000001
    assert (
        EcTag(0x000B, codes.EC_TAGTYPE_UINT64, b"\x6b\x5e\x8d\x3a\x12\xf0\xc4\xd7").int_value()
        == 0x6B5E8D3A12F0C4D7
    )


def test_int_value_rejects_non_int_type_and_lying_width() -> None:
    with pytest.raises(EcProtocolError):
        EcTag(0x000B, codes.EC_TAGTYPE_STRING, b"12\x00").int_value()
    with pytest.raises(EcProtocolError):
        EcTag(0x000B, codes.EC_TAGTYPE_UINT32, b"\x01\x02").int_value()  # largeur menteuse


def test_string_value_strips_the_final_nul_and_never_crashes_on_hostile_bytes() -> None:
    assert EcTag(0x0000, codes.EC_TAGTYPE_STRING, b"keroro\x00").string_value() == "keroro"
    # Octets non-UTF-8 dans un nom hostile : remplacés, jamais d'exception (errors="replace").
    hostile = EcTag(0x0000, codes.EC_TAGTYPE_STRING, b"\xff\xfe\x00")
    assert "�" in hostile.string_value()


def test_string_value_rejects_wrong_type_or_missing_nul() -> None:
    with pytest.raises(EcProtocolError):
        EcTag(0x0000, codes.EC_TAGTYPE_UINT8, b"\x01").string_value()
    with pytest.raises(EcProtocolError):
        EcTag(0x0000, codes.EC_TAGTYPE_STRING, b"sans-nul").string_value()


def test_ipv4_value_renders_ip_and_port() -> None:
    # Réf. §3 : 6 octets = 4 octets d'IP + port uint16 big-endian (ECTag.cpp:108-116).
    tag = EcTag(
        codes.EC_TAG_SERVER, codes.EC_TAGTYPE_IPV4, bytes([1, 2, 3, 4]) + (4661).to_bytes(2, "big")
    )
    assert tag.ipv4_value() == "1.2.3.4:4661"


def test_ipv4_value_rejects_wrong_type_or_length() -> None:
    with pytest.raises(EcProtocolError):
        bad_type = EcTag(codes.EC_TAG_SERVER, codes.EC_TAGTYPE_CUSTOM, b"\x01\x02\x03\x04\x12\x35")
        bad_type.ipv4_value()
    with pytest.raises(EcProtocolError):
        EcTag(codes.EC_TAG_SERVER, codes.EC_TAGTYPE_IPV4, b"\x01\x02\x03\x04").ipv4_value()


def test_find_returns_first_child_by_logical_name_or_none() -> None:
    child_a = uint_tag(0x030A, 5)
    child_b = uint_tag(0x030D, 2)
    parent = EcTag(0x0700, codes.EC_TAGTYPE_UINT8, b"\x01", (child_a, child_b))
    assert parent.find(0x030D) is child_b
    assert parent.find(0x9999) is None
    # Doublons : le PREMIER gagne (sémantique documentée, le décodeur s'y fiera).
    dup_a = uint_tag(0x030A, 1)
    dup_b = uint_tag(0x030A, 2)
    parent_dup = EcTag(0x0700, codes.EC_TAGTYPE_UINT8, b"\x01", (dup_a, dup_b))
    assert parent_dup.find(0x030A) is dup_a


def test_packet_find_returns_first_top_level_tag_or_none() -> None:
    tag = string_tag(codes.EC_TAG_STRING, "ok")
    packet = EcPacket(codes.EC_OP_STRINGS, (tag,))
    assert packet.find(codes.EC_TAG_STRING) is tag
    assert packet.find(codes.EC_TAG_CONNSTATE) is None
    assert EcPacket(codes.EC_OP_NOOP).tags == ()
    # Doublons au premier niveau : le PREMIER gagne.
    dup_a = string_tag(codes.EC_TAG_STRING, "premier")
    dup_b = string_tag(codes.EC_TAG_STRING, "second")
    assert EcPacket(codes.EC_OP_STRINGS, (dup_a, dup_b)).find(codes.EC_TAG_STRING) is dup_a
