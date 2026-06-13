import zlib

import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    decode_header,
    decode_packet,
    empty_tag,
    encode_packet,
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


# ---------------------------------------------------------------- encodage

# Trame AUTH_REQ de référence, dérivée OCTET PAR OCTET de la réf. §1/§2/§4 :
#   en-tête 8 octets : flags=0x00000020 (base seule, DÉCISION 2), length=0x00000024 (36)
#   payload : opcode 0x02 (EC_OP_AUTH_REQ) ; TAGCOUNT 0x0003
#     tag1 : TAGNAME 0x0200 (= 0x0100 CLIENT_NAME << 1, bit enfants 0), TAGTYPE 0x06 (STRING),
#            TAGLEN 0x00000006, valeur "probe\0" (NUL inclus, piège 10)
#     tag2 : TAGNAME 0x0202 (= 0x0101 CLIENT_VERSION << 1), STRING, TAGLEN 4, "1.0\0"
#     tag3 : TAGNAME 0x0004 (= 0x0002 PROTOCOL_VERSION << 1), TAGTYPE 0x03 (UINT16 :
#            0x0204 émis au plus court), TAGLEN 2, valeur 0x0204
# Le groupement par champ EST la dérivation ; ruff format recollerait les chaînes.
# fmt: off
_AUTH_REQ_FRAME = bytes.fromhex(
    "00000020" "00000024"
    "02" "0003"
    "0200" "06" "00000006" "70726f626500"
    "0202" "06" "00000004" "312e3000"
    "0004" "03" "00000002" "0204"
)
# fmt: on


def _auth_req_packet() -> EcPacket:
    return EcPacket(
        codes.EC_OP_AUTH_REQ,
        (
            string_tag(codes.EC_TAG_CLIENT_NAME, "probe"),
            string_tag(codes.EC_TAG_CLIENT_VERSION, "1.0"),
            uint_tag(codes.EC_TAG_PROTOCOL_VERSION, codes.EC_CURRENT_PROTOCOL_VERSION),
        ),
    )


def test_encode_packet_produces_the_exact_auth_req_frame() -> None:
    assert encode_packet(_auth_req_packet()) == _AUTH_REQ_FRAME


# Trame SEARCH_RESULTS imbriquée, dérivée de la réf. §2 (TAGLEN, piège 3) et §5 :
#   parent : TAGNAME 0x0E01 (= EC_TAG_SEARCHFILE (0x0700) << 1 | 1 enfants), TAGTYPE 0x02 (UINT8 :
#            ECID=1 émis au plus court), TAGLEN 0x52 (82), TAGCOUNT 0x0006
#   TAGLEN parent = valeur propre (1) + Σ enfants (TAGLEN + 7 d'en-tête chacun, aucun
#   petit-enfant donc pas de +2) = 1 + (16+7)+(4+7)+(16+7)+(1+7)+(1+7)+(1+7) = 82
#   enfants (TAGNAME = nom << 1) :
#     0x0602 (=0x0301 NAME) STRING  len 16 : "Keroro 062A.avi\0"
#     0x0606 (=0x0303 SIZE_FULL) UINT32 len 4 : 234567890 = 0x0DFB38D2
#     0x063C (=0x031E HASH) HASH16 len 16 : 000102...0f
#     0x0614 (=0x030A SOURCE_COUNT) UINT8 len 1 : 5
#     0x061A (=0x030D SOURCE_COUNT_XFER) UINT8 len 1 : 2
#     0x1332 (=0x0999 tag INCONNU forgé) UINT8 len 1 : 7
#   payload = opcode(1) + tagcount(2) + en-tête parent(7) + TAGCOUNT parent(2) + 82 = 94 = 0x5E
# fmt: off
_SEARCH_RESULT_FRAME = bytes.fromhex(
    "00000020" "0000005e"
    "28" "0001"
    "0e01" "02" "00000052" "0006"
    "0602" "06" "00000010" "4b65726f726f20303632412e61766900"
    "0606" "04" "00000004" "0dfb38d2"
    "063c" "09" "00000010" "000102030405060708090a0b0c0d0e0f"
    "0614" "02" "00000001" "05"
    "061a" "02" "00000001" "02"
    "1332" "02" "00000001" "07"
    "01"
)
# fmt: on


def _search_result_packet() -> EcPacket:
    entry = EcTag(
        codes.EC_TAG_SEARCHFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",  # ECID (identifiant de session VOLATIL, piège 13 — jamais persisté)
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro 062A.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 234567890),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, bytes(range(16))),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT, 5),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER, 2),
            uint_tag(0x0999, 7),  # tag inconnu : doit voyager sans erreur (capture-all)
        ),
    )
    return EcPacket(codes.EC_OP_SEARCH_RESULTS, (entry,))


def test_encode_packet_handles_children_taglen_and_tagcount() -> None:
    # Vérifie le piège 3 : TAGLEN parent inclut en-têtes des enfants, PAS son propre TAGCOUNT.
    assert encode_packet(_search_result_packet()) == _SEARCH_RESULT_FRAME


def test_encode_packet_with_no_tags_is_the_minimal_frame() -> None:
    # NOOP sans tag : payload = opcode (1) + TAGCOUNT 0x0000 (2) = 3 octets.
    expected = bytes.fromhex("00000020" "00000003" "01" "0000")  # fmt: skip
    assert encode_packet(EcPacket(codes.EC_OP_NOOP)) == expected


def test_encode_packet_counts_grandchildren_in_taglen() -> None:
    # Profondeur 3 (réf. §2, GetTagLen) : le TAGLEN d'un parent compte la taille sérialisée
    # COMPLÈTE de chaque enfant, Y COMPRIS le TAGCOUNT (2 octets) d'un enfant qui a lui-même
    # des enfants. leaf = 7+1 = 8 ; middle = 7+2+(1+8) = 18 ; TAGLEN(grand) = 1+18 = 19 = 0x13 ;
    # payload = 1+2+(7+2+19) = 31 = 0x1F.
    leaf = uint_tag(0x030A, 5)
    middle = EcTag(0x0301, codes.EC_TAGTYPE_UINT8, b"\x02", (leaf,))
    grand = EcTag(0x0700, codes.EC_TAGTYPE_UINT8, b"\x01", (middle,))
    # fmt: off
    expected = bytes.fromhex(
        "00000020" "0000001f"
        "07" "0001"
        "0e01" "02" "00000013" "0001"
        "0603" "02" "00000009" "0001"
        "0614" "02" "00000001" "05"
        "02" "01"
    )
    # fmt: on
    assert encode_packet(EcPacket(codes.EC_OP_MISC_DATA, (grand,))) == expected


# ---------------------------------------------------------------- décodage nominal


def test_decode_header_accepts_base_and_base_zlib_flags() -> None:
    assert decode_header(bytes.fromhex("00000020" "00000003")) == (0x20, 3)  # fmt: skip
    assert decode_header(bytes.fromhex("00000021" "00000010")) == (0x21, 16)  # fmt: skip


def test_decode_packet_rebuilds_the_auth_req_tree() -> None:
    assert decode_packet(_AUTH_REQ_FRAME) == _auth_req_packet()


def test_decode_packet_rebuilds_the_nested_search_result_tree() -> None:
    # Piège 2 (TAGNAME >> 1) et piège 3 (valeur propre = TAGLEN - Σ enfants) traversés.
    assert decode_packet(_SEARCH_RESULT_FRAME) == _search_result_packet()


def test_decode_packet_minimal_noop() -> None:
    frame = bytes.fromhex("00000020" "00000003" "01" "0000")  # fmt: skip
    assert decode_packet(frame) == EcPacket(codes.EC_OP_NOOP)


def test_roundtrip_encode_decode_is_identity_on_forged_packets() -> None:
    # Round-trip sur un éventail de formes : tags vides, toutes largeurs d'entiers, hash,
    # chaînes accentuées, imbrication à 3 niveaux, valeur propre + enfants simultanés.
    deep = EcTag(
        0x0700,
        codes.EC_TAGTYPE_UINT16,
        b"\x12\x34",
        (
            empty_tag(codes.EC_TAG_CAN_ZLIB),
            string_tag(0x0301, "épisode 062A — « démo »"),
            EcTag(
                0x0500,
                codes.EC_TAGTYPE_IPV4,
                bytes([10, 0, 0, 1]) + (4712).to_bytes(2, "big"),
                (string_tag(0x0501, "serveur"),),
            ),
        ),
    )
    packets = [
        EcPacket(codes.EC_OP_NOOP),
        _auth_req_packet(),
        _search_result_packet(),
        EcPacket(
            codes.EC_OP_MISC_DATA,
            (
                deep,
                uint_tag(0x0001, 0),
                uint_tag(0x0002, 0xFFFF),
                uint_tag(0x0003, 0xFFFFFFFF),
                uint_tag(0x0004, (1 << 64) - 1),
                hash16_tag(0x031E, bytes(range(16))),
            ),
        ),
    ]
    for packet in packets:
        assert decode_packet(encode_packet(packet)) == packet


# ---------------------------------------------------------------- entrées hostiles


def test_decode_header_rejects_wrong_size_unknown_flags_and_oversized_length() -> None:
    with pytest.raises(EcProtocolError):
        decode_header(bytes.fromhex("0000002000"))  # 5 octets au lieu de 8
    # Flags refusés (DÉCISION 2) : UTF8_NUMBERS non négocié, bit 0x40 interdit, base absente.
    for flags_hex in ("00000022", "00000060", "00000000", "00000028"):
        with pytest.raises(EcProtocolError):
            decode_header(bytes.fromhex(flags_hex + "00000003"))
    # Plafond 16 Mio (ReadHeader, ECSocket.cpp:540) : 16 Mio + 1 → rejet net.
    with pytest.raises(EcProtocolError):
        decode_header(bytes.fromhex("00000020" "01000001"))  # fmt: skip
    # Exactement 16 Mio (0x01000000) : accepté (borne incluse).
    assert decode_header(bytes.fromhex("0000002001000000")) == (0x20, 16 * 1024 * 1024)


def test_decode_rejects_truncated_value_inside_a_tag() -> None:
    # Tag STRING annonçant TAGLEN=5 mais 1 seul octet présent ; length d'en-tête cohérente (11).
    # payload : 28 | 0001 | 0602 06 00000005 | 41  →  take(5) déborde → « paquet EC tronqué ».
    frame = bytes.fromhex("000000200000000b2800010602060000000541")
    with pytest.raises(EcProtocolError, match="tronqué"):
        decode_packet(frame)


def test_decode_rejects_lying_taglen_smaller_than_children() -> None:
    # Parent 0x0700 avec 1 enfant de 8 octets sérialisés mais TAGLEN=0 → valeur propre -8.
    # payload : 28 | 0001 | 0E01 02 00000000 0001 | 0614 02 00000001 05  (20 octets = 0x14)
    # fmt: off
    frame = bytes.fromhex(
        "00000020" "00000014" "28" "0001" "0e01" "02" "00000000" "0001" "0614" "02" "00000001" "05"
    )
    # fmt: on
    with pytest.raises(EcProtocolError, match="TAGLEN menteur"):
        decode_packet(frame)


def test_decode_rejects_trailing_garbage_after_last_tag() -> None:
    # Trame NOOP valide + 1 octet 0xFF compté dans length → « octets résiduels ».
    frame = bytes.fromhex("00000020" "00000004" "01" "0000" "ff")  # fmt: skip
    with pytest.raises(EcProtocolError, match="résiduels"):
        decode_packet(frame)


def test_decode_packet_rejects_frame_length_mismatch() -> None:
    with pytest.raises(EcProtocolError, match="incohérente"):
        decode_packet(_AUTH_REQ_FRAME[:-1])  # un octet manquant par rapport à l'en-tête


def test_decode_rejects_children_bit_set_with_zero_tagcount() -> None:
    # Bit enfants (TAGNAME & 1) posé mais TAGCOUNT=0 : l'encodeur aMule ne pose le bit que
    # s'il existe des sous-tags. Accepter cette forme ferait diverger TAGLEN de la
    # consommation wire réelle (2 octets de TAGCOUNT lus mais non recomptés sur l'arbre
    # normalisé) → le tag absorberait silencieusement 2 octets appartenant à la suite.
    # payload : 01 | 0001 | 0603 06 00000003 0000 | 414200  (15 octets = 0x0F)
    #   tag : TAGNAME 0x0603 (= 0x0301 NAME << 1 | bit enfants), STRING, TAGLEN=3,
    #         TAGCOUNT=0x0000 (interdit), valeur "AB\0"
    # fmt: off
    frame = bytes.fromhex(
        "00000020" "0000000f" "01" "0001" "0603" "06" "00000003" "0000" "414200"
    )
    # fmt: on
    with pytest.raises(EcProtocolError, match="bit enfants sans TAGCOUNT"):
        decode_packet(frame)
    # Variante de la sur-lecture démontrée : 2 octets de garbage (qui devaient mourir en
    # « octets résiduels ») étaient absorbés dans la valeur du tag.
    # payload : 01 | 0001 | 0603 06 00000002 0000 | ffff  (14 octets = 0x0E)
    # fmt: off
    absorbing = bytes.fromhex(
        "00000020" "0000000e" "01" "0001" "0603" "06" "00000002" "0000" "ffff"
    )
    # fmt: on
    with pytest.raises(EcProtocolError, match="bit enfants sans TAGCOUNT"):
        decode_packet(absorbing)


def _nested_empty_tags(levels: int) -> EcTag:
    tag = empty_tag(0x0999)
    for _ in range(levels - 1):
        tag = empty_tag(0x0999, (tag,))
    return tag


def test_decode_accepts_depth_32_and_rejects_depth_33() -> None:
    ok_frame = encode_packet(EcPacket(codes.EC_OP_NOOP, (_nested_empty_tags(32),)))
    assert decode_packet(ok_frame).tags[0].children  # 32 niveaux : décodé sans erreur
    bad_frame = encode_packet(EcPacket(codes.EC_OP_NOOP, (_nested_empty_tags(33),)))
    with pytest.raises(EcProtocolError, match="profonde"):
        decode_packet(bad_frame)


# ---------------------------------------------------------------- zlib borné


def _zlib_frame(payload: bytes) -> bytes:
    compressed = zlib.compress(payload)
    return bytes.fromhex("00000021") + len(compressed).to_bytes(4, "big") + compressed


def test_decode_inflates_a_valid_zlib_frame() -> None:
    # Le payload SEARCH_RESULTS (clair, déjà validé) compressé : même arbre à l'arrivée.
    assert decode_packet(_zlib_frame(_SEARCH_RESULT_FRAME[8:])) == _search_result_packet()


def test_decode_rejects_corrupt_zlib_stream() -> None:
    frame = _zlib_frame(_SEARCH_RESULT_FRAME[8:])
    corrupted = frame[:8] + b"\x00\x00" + frame[10:]  # écrase l'en-tête zlib
    with pytest.raises(EcProtocolError, match="corrompu"):
        decode_packet(corrupted)


def test_decode_rejects_truncated_zlib_stream() -> None:
    compressed = zlib.compress(_SEARCH_RESULT_FRAME[8:])[:-4]  # flux valide mais incomplet
    frame = bytes.fromhex("00000021") + len(compressed).to_bytes(4, "big") + compressed
    with pytest.raises(EcProtocolError, match="hors borne|tronqué"):
        decode_packet(frame)


def test_decode_rejects_zlib_bomb_beyond_the_decompression_bound() -> None:
    # 16 Mio + 1 de zéros compressés en ~16 Kio : la décompression BORNÉE refuse (DÉCISION 3).
    bomb = zlib.compress(b"\x00" * (16 * 1024 * 1024 + 1))
    frame = bytes.fromhex("00000021") + len(bomb).to_bytes(4, "big") + bomb
    with pytest.raises(EcProtocolError, match="hors borne|tronqué"):
        decode_packet(frame)


def test_decode_accepts_inflation_to_exactly_the_decompression_bound() -> None:
    # DÉCISION 3 : chaque borne est testée DES DEUX CÔTÉS. Un payload EC valide d'exactement
    # 16 Mio : opcode (1) + TAGCOUNT (2) + en-tête de tag (7) + valeur propre de 16 Mio - 10.
    own = 16 * 1024 * 1024 - 10
    # fmt: off
    payload = (
        bytes([codes.EC_OP_MISC_DATA]) + (1).to_bytes(2, "big")
        + (0x0999 << 1).to_bytes(2, "big") + bytes([codes.EC_TAGTYPE_CUSTOM])
        + own.to_bytes(4, "big") + b"\x00" * own
    )
    # fmt: on
    packet = decode_packet(_zlib_frame(payload))
    assert packet == EcPacket(
        codes.EC_OP_MISC_DATA, (EcTag(0x0999, codes.EC_TAGTYPE_CUSTOM, b"\x00" * own),)
    )
