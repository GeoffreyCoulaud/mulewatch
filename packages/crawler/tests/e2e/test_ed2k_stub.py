"""Tests du stub eD2k (spec e2e §3.6) — codec PUR + dispatch asyncio sur streams mémoire.

Couvre TOUTES les branches du codec (round-trips, golden bytes, troncatures, limites) ET du
dispatch (les 3 opcodes reçus → les 3 émis, opcode inconnu, header tronqué, proto inattendu,
keyword absent). Le ``serve_forever`` réel (bind) est ``# pragma: no cover``.
"""

from __future__ import annotations

import asyncio
import socket
import struct

import pytest

from tests.e2e.ed2k_stub import (
    MAX_LOWID,
    OP_FOUNDSOURCES,
    OP_GETSOURCES,
    OP_IDCHANGE,
    OP_LOGINREQUEST,
    OP_SEARCHREQUEST,
    OP_SEARCHRESULT,
    OP_SERVERMESSAGE,
    OP_SERVERSTATUS,
    PROTO_EDONKEY,
    SO_STRING_TERM,
    TN_FILENAME,
    PlantedFile,
    PlantedSource,
    ProtocolError,
    build_foundsources,
    build_idchange,
    build_searchresult,
    build_servermessage,
    build_serverstatus,
    decode_header,
    encode_header,
    encode_string_tag,
    encode_uint64_tag,
    extract_keyword,
    handle_connection,
)

_FILE = PlantedFile(
    name="Keroro n°62 A.mp4", size=14345, file_hash="7d3ce5e6b6243999b4fed38bb7ae1c05"
)
_SOURCE = PlantedSource(ip="172.20.0.5", port=4662)


# --- Codec : header --------------------------------------------------------------------------


def test_encode_header_golden_bytes() -> None:
    frame = encode_header(0x40, b"\x01\x02\x03")
    # [0xE3][length=4 u32 LE][opcode 0x40][payload] ; length = 1 + 3 = 4.
    assert frame == bytes([0xE3, 0x04, 0x00, 0x00, 0x00, 0x40, 0x01, 0x02, 0x03])


def test_encode_decode_header_round_trip() -> None:
    payload = b"hello-payload"
    opcode, decoded = decode_header(encode_header(OP_SEARCHRESULT, payload))
    assert opcode == OP_SEARCHRESULT
    assert decoded == payload


def test_decode_header_rejects_short_frame() -> None:
    with pytest.raises(ProtocolError, match="trop courte"):
        decode_header(b"\xe3\x01\x00")


def test_decode_header_rejects_unexpected_proto() -> None:
    frame = bytearray(encode_header(OP_LOGINREQUEST, b"x"))
    frame[0] = 0xD4  # PROTO_PACKED
    with pytest.raises(ProtocolError, match="proto inattendu"):
        decode_header(bytes(frame))


def test_decode_header_rejects_inconsistent_length() -> None:
    frame = bytearray(encode_header(OP_LOGINREQUEST, b"abc"))
    frame[1] = 0xFF  # longueur annoncée gonflée, incohérente avec le payload
    with pytest.raises(ProtocolError, match="incohérente"):
        decode_header(bytes(frame))


# --- Codec : tags ----------------------------------------------------------------------------


def test_encode_string_tag_golden_bytes() -> None:
    tag = encode_string_tag(TN_FILENAME, "ab")
    # [type 0x02][name_len=1 u16 LE][name 0x01][str_len=2 u16 LE]['a','b']
    assert tag == bytes([0x02, 0x01, 0x00, 0x01, 0x02, 0x00, ord("a"), ord("b")])


def test_encode_string_tag_utf8_multibyte() -> None:
    tag = encode_string_tag(TN_FILENAME, "n°")  # ° = 2 octets UTF-8
    raw = "n°".encode()
    assert tag[-len(raw) :] == raw
    (str_len,) = struct.unpack_from("<H", tag, 4)
    assert str_len == len(raw)


def test_encode_uint64_tag_golden_bytes() -> None:
    tag = encode_uint64_tag(0x02, 0x0102030405060708)
    assert tag == bytes([0x0B, 0x01, 0x00, 0x02]) + struct.pack("<Q", 0x0102030405060708)


# --- Codec : build_idchange (HighID forcé) ---------------------------------------------------


def test_build_idchange_reflects_ip_bytes_in_network_order() -> None:
    frame = build_idchange("172.20.0.5", tcp_flags=0)
    opcode, payload = decode_header(frame)
    assert opcode == OP_IDCHANGE
    # id = octets de l'IP en ordre réseau (a,b,c,d), puis tcp_flags u32 LE = 0.
    assert payload[:4] == socket.inet_aton("172.20.0.5")
    assert payload[4:] == struct.pack("<I", 0)


def test_build_idchange_high_id_is_above_max_lowid_for_docker_ip() -> None:
    frame = build_idchange("172.20.0.5")
    _, payload = decode_header(frame)
    id_u32 = int.from_bytes(payload[:4], "little")
    assert id_u32 >= MAX_LOWID


def test_build_idchange_rejects_low_id_ip() -> None:
    # 0.0.0.5 → octets 00 00 00 05 → u32 LE = 0x05000000 ≥ MAX_LOWID (HighID, accepté).
    # 5.0.0.0 → octets 05 00 00 00 → u32 LE = 0x00000005 < MAX_LOWID (LowID, rejeté).
    with pytest.raises(ValueError, match="LowID"):
        build_idchange("5.0.0.0")


def test_build_idchange_custom_tcp_flags() -> None:
    _, payload = decode_header(build_idchange("10.0.0.1", tcp_flags=0x08))
    assert payload[4:] == struct.pack("<I", 0x08)


# --- Codec : SERVERMESSAGE / SERVERSTATUS (optionnels) ---------------------------------------


def test_build_servermessage_carries_length_prefixed_message() -> None:
    opcode, payload = decode_header(build_servermessage("hi"))
    assert opcode == OP_SERVERMESSAGE
    (msg_len,) = struct.unpack_from("<H", payload, 0)
    assert msg_len == 2
    assert payload[2:] == b"hi"


def test_build_serverstatus_carries_two_u32_counts() -> None:
    opcode, payload = decode_header(build_serverstatus(7, 9))
    assert opcode == OP_SERVERSTATUS
    assert struct.unpack("<II", payload) == (7, 9)


# --- Codec : SEARCHRESULT --------------------------------------------------------------------


def test_build_searchresult_structure_and_count() -> None:
    opcode, payload = decode_header(build_searchresult("file.mp4", 1234, _FILE.file_hash))
    assert opcode == OP_SEARCHRESULT
    (count,) = struct.unpack_from("<I", payload, 0)
    assert count == 1
    # hash16 + id4 + port2 + tag_count4
    body = payload[4:]
    assert body[:16] == bytes.fromhex(_FILE.file_hash)
    (id_, port) = struct.unpack_from("<IH", body, 16)
    assert (id_, port) == (0, 0)
    (tag_count,) = struct.unpack_from("<I", body, 22)
    assert tag_count == 2
    # Les deux tags suivent : FILENAME (string) puis FILESIZE (uint64).
    tags = body[26:]
    name_tag = encode_string_tag(TN_FILENAME, "file.mp4")
    assert tags[: len(name_tag)] == name_tag
    size_tag = encode_uint64_tag(0x02, 1234)
    assert tags[len(name_tag) :] == size_tag


def test_build_searchresult_rejects_bad_hash_length() -> None:
    with pytest.raises(ValueError, match="16 octets"):
        build_searchresult("x", 1, "abcd")


# --- Codec : FOUNDSOURCES --------------------------------------------------------------------


def test_build_foundsources_structure() -> None:
    opcode, payload = decode_header(
        build_foundsources(_FILE.file_hash, [("172.20.0.5", 4662), ("10.0.0.9", 5000)])
    )
    assert opcode == OP_FOUNDSOURCES
    assert payload[:16] == bytes.fromhex(_FILE.file_hash)
    assert payload[16] == 2  # count u8
    src = payload[17:]
    assert src[:4] == socket.inet_aton("172.20.0.5")
    assert struct.unpack_from("<H", src, 4)[0] == 4662
    assert src[6:10] == socket.inet_aton("10.0.0.9")
    assert struct.unpack_from("<H", src, 10)[0] == 5000


def test_build_foundsources_empty_source_list() -> None:
    _, payload = decode_header(build_foundsources(_FILE.file_hash, []))
    assert payload[16] == 0
    assert payload[17:] == b""


def test_build_foundsources_rejects_bad_hash_length() -> None:
    with pytest.raises(ValueError, match="16 octets"):
        build_foundsources("00", [])


# --- Codec : extract_keyword -----------------------------------------------------------------


def test_extract_keyword_finds_string_term() -> None:
    keyword = "keroro"
    raw = keyword.encode("utf-8")
    payload = bytes([SO_STRING_TERM]) + struct.pack("<H", len(raw)) + raw
    assert extract_keyword(payload) == keyword


def test_extract_keyword_finds_term_after_tree_prefix() -> None:
    # Préfixe d'arbre (SO_AND = 0x0000 u16) PUIS le terme : le scan saute jusqu'au 0x01.
    raw = b"titar"
    term = bytes([SO_STRING_TERM]) + struct.pack("<H", len(raw)) + raw
    payload = struct.pack("<H", 0x0000) + term
    assert extract_keyword(payload) == "titar"


def test_extract_keyword_returns_none_when_absent() -> None:
    assert extract_keyword(b"\x00\x02\x03\xff") is None


def test_extract_keyword_returns_none_on_truncated_length() -> None:
    # 0x01 trouvé mais < 3 octets restants pour lire la longueur → branche ``i+3 <= n`` fausse.
    assert extract_keyword(bytes([SO_STRING_TERM, 0x05])) is None


def test_extract_keyword_skips_term_with_length_exceeding_payload() -> None:
    # 0x01 + longueur annoncée (10) > octets restants (3) → branche interne fausse, scan continue
    # et n'aboutit pas → None.
    payload = bytes([SO_STRING_TERM]) + struct.pack("<H", 10) + b"abc"
    assert extract_keyword(payload) is None


def test_extract_keyword_falls_back_to_latin1_on_invalid_utf8() -> None:
    raw = b"\xff\xfe"  # invalide en UTF-8 → décodage latin-1
    payload = bytes([SO_STRING_TERM]) + struct.pack("<H", len(raw)) + raw
    assert extract_keyword(payload) == raw.decode("latin-1")


# --- Dispatch asyncio (streams mémoire) ------------------------------------------------------


class _CapturingWriter:
    """Faux StreamWriter capturant les octets écrits (pas de socket réel — sandbox-safe)."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, _name: str) -> None:  # pragma: no cover - non sollicité ici
        return None


def _reader_from(frames: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(frames)
    reader.feed_eof()
    return reader


def _split_frames(buffer: bytes) -> list[tuple[int, bytes]]:
    """Découpe un flux d'octets en trames (opcode, payload) — pour asserter les réponses."""
    frames: list[tuple[int, bytes]] = []
    i = 0
    while i < len(buffer):
        proto, length = struct.unpack_from("<BI", buffer, i)
        assert proto == PROTO_EDONKEY
        opcode = buffer[i + 5]
        payload = buffer[i + 6 : i + 5 + length]
        frames.append((opcode, payload))
        i += 5 + length
    return frames


async def _run_dispatch(received: bytes) -> _CapturingWriter:
    reader = _reader_from(received)
    writer = _CapturingWriter()
    await handle_connection(
        reader,
        writer,  # type: ignore[arg-type]
        peer_ip="172.20.0.5",
        planted_file=_FILE,
        planted_source=_SOURCE,
    )
    return writer


@pytest.mark.asyncio
async def test_dispatch_login_emits_message_status_and_highid() -> None:
    writer = await _run_dispatch(encode_header(OP_LOGINREQUEST, b"\x00" * 16))
    frames = _split_frames(bytes(writer.buffer))
    opcodes = [op for op, _ in frames]
    assert opcodes == [OP_SERVERMESSAGE, OP_SERVERSTATUS, OP_IDCHANGE]
    idchange_payload = frames[-1][1]
    assert idchange_payload[:4] == socket.inet_aton("172.20.0.5")
    assert writer.closed


@pytest.mark.asyncio
async def test_dispatch_search_emits_planted_searchresult() -> None:
    raw = b"keroro"
    search_payload = bytes([SO_STRING_TERM]) + struct.pack("<H", len(raw)) + raw
    writer = await _run_dispatch(encode_header(OP_SEARCHREQUEST, search_payload))
    frames = _split_frames(bytes(writer.buffer))
    assert len(frames) == 1
    assert frames[0][0] == OP_SEARCHRESULT
    # Le nom planté est présent dans le résultat.
    assert _FILE.name.encode("utf-8") in frames[0][1]


@pytest.mark.asyncio
async def test_dispatch_getsources_emits_planted_foundsources() -> None:
    hash_bytes = bytes.fromhex(_FILE.file_hash)
    writer = await _run_dispatch(encode_header(OP_GETSOURCES, hash_bytes))
    frames = _split_frames(bytes(writer.buffer))
    assert len(frames) == 1
    opcode, payload = frames[0]
    assert opcode == OP_FOUNDSOURCES
    assert payload[:16] == hash_bytes
    assert payload[16] == 1
    assert payload[17:21] == socket.inet_aton(_SOURCE.ip)


@pytest.mark.asyncio
async def test_dispatch_unknown_opcode_is_ignored() -> None:
    writer = await _run_dispatch(encode_header(0x99, b"junk"))
    assert bytes(writer.buffer) == b""
    assert writer.closed


@pytest.mark.asyncio
async def test_dispatch_clean_eof_ends_connection() -> None:
    writer = await _run_dispatch(b"")  # EOF immédiat
    assert bytes(writer.buffer) == b""
    assert writer.closed


@pytest.mark.asyncio
async def test_dispatch_rejects_packed_proto() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(bytes([0xD4, 0x01, 0x00, 0x00, 0x00, OP_LOGINREQUEST]))
    reader.feed_eof()
    writer = _CapturingWriter()
    with pytest.raises(ProtocolError, match="proto inattendu"):
        await handle_connection(
            reader,
            writer,  # type: ignore[arg-type]
            peer_ip="172.20.0.5",
            planted_file=_FILE,
            planted_source=_SOURCE,
        )


@pytest.mark.asyncio
async def test_dispatch_rejects_zero_length_frame() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(bytes([PROTO_EDONKEY, 0x00, 0x00, 0x00, 0x00]))
    reader.feed_eof()
    writer = _CapturingWriter()
    with pytest.raises(ProtocolError, match="nulle"):
        await handle_connection(
            reader,
            writer,  # type: ignore[arg-type]
            peer_ip="172.20.0.5",
            planted_file=_FILE,
            planted_source=_SOURCE,
        )


@pytest.mark.asyncio
async def test_dispatch_two_requests_in_sequence() -> None:
    raw = b"keroro"
    search_payload = bytes([SO_STRING_TERM]) + struct.pack("<H", len(raw)) + raw
    received = encode_header(OP_LOGINREQUEST, b"\x00" * 16) + encode_header(
        OP_SEARCHREQUEST, search_payload
    )
    writer = await _run_dispatch(received)
    opcodes = [op for op, _ in _split_frames(bytes(writer.buffer))]
    assert opcodes == [OP_SERVERMESSAGE, OP_SERVERSTATUS, OP_IDCHANGE, OP_SEARCHRESULT]
