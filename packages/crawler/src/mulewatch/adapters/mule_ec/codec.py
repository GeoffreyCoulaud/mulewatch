"""PURE, SYNCHRONOUS EC codec: bytes ↔ tag tree (cf. docs/reference/ec-protocol.md §1-§3).

GENERIC: encodes/decodes ANY EC packet (recursive container format). NO I/O.
The tag names handled here are LOGICAL; the wire shift ``(name << 1) | children``
(ref. §2, pitfall 2) is confined to the encode/decode (Tasks 6-8).
"""

import zlib
from dataclasses import dataclass
from typing import Final

from mulewatch.adapters.mule_ec import codes
from mulewatch.adapters.mule_ec.errors import EcProtocolError

# Width (bytes) of each integer type — ref. §3. Ascending order: uint_tag takes the first
# that fits ("encoded as short as possible", InitInt, ECTag.cpp:207-221).
INT_WIDTHS: Final[dict[int, int]] = {
    codes.EC_TAGTYPE_UINT8: 1,
    codes.EC_TAGTYPE_UINT16: 2,
    codes.EC_TAGTYPE_UINT32: 4,
    codes.EC_TAGTYPE_UINT64: 8,
}


@dataclass(frozen=True)
class EcTag:
    """An EC tag: LOGICAL name (already ``>> 1``), type, own value, sub-tags."""

    name: int
    tag_type: int
    value: bytes = b""
    children: tuple["EcTag", ...] = ()

    def find(self, name: int) -> "EcTag | None":
        """First child with this logical name, or ``None``."""
        for child in self.children:
            if child.name == name:
                return child
        return None

    def int_value(self) -> int:
        """VARIABLE-WIDTH integer value (ref. §9 pitfall 4 — equivalent to ``GetInt()``)."""
        if self.tag_type not in INT_WIDTHS or len(self.value) != INT_WIDTHS[self.tag_type]:
            raise EcProtocolError(f"tag 0x{self.name:04X}: not a valid EC integer")
        return int.from_bytes(self.value, "big")

    def string_value(self) -> str:
        """String value: UTF-8 + trailing NUL included in TAGLEN (ref. §3, pitfall 10).

        ``errors="replace"`` decoding: a hostile filename never crashes
        (the raw bytes stay available in ``value``).
        """
        if self.tag_type != codes.EC_TAGTYPE_STRING or not self.value.endswith(b"\x00"):
            raise EcProtocolError(f"tag 0x{self.name:04X}: not a valid EC string")
        return self.value[:-1].decode("utf-8", errors="replace")

    def ipv4_value(self) -> str:
        """IPV4 value (ref. §3): 4 IP bytes + uint16 big-endian port → ``"a.b.c.d:port"``."""
        if self.tag_type != codes.EC_TAGTYPE_IPV4 or len(self.value) != 6:
            raise EcProtocolError(f"tag 0x{self.name:04X}: not a valid EC IPv4")
        ip = ".".join(str(byte) for byte in self.value[:4])
        port = int.from_bytes(self.value[4:6], "big")
        return f"{ip}:{port}"


@dataclass(frozen=True)
class EcPacket:
    """An EC packet: opcode + top-level tags (the packet is a pseudo-tag, ref. §2)."""

    opcode: int
    tags: tuple[EcTag, ...] = ()

    def find(self, name: int) -> EcTag | None:
        """First top-level tag with this logical name, or ``None``."""
        for tag in self.tags:
            if tag.name == name:
                return tag
        return None


def uint_tag(name: int, value: int, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Integer tag encoded AS SHORT AS POSSIBLE (ref. §3: InitInt)."""
    if value < 0:
        raise EcProtocolError(f"negative EC integer: {value}")
    for tag_type, width in INT_WIDTHS.items():
        if value < 1 << (8 * width):
            return EcTag(name, tag_type, value.to_bytes(width, "big"), children)
    raise EcProtocolError(f"integer too large for EC: {value}")


def string_tag(name: int, text: str, children: tuple[EcTag, ...] = ()) -> EcTag:
    """String tag: UTF-8 + trailing NUL, INCLUDED in the length (ref. §3, pitfall 10)."""
    return EcTag(name, codes.EC_TAGTYPE_STRING, text.encode("utf-8") + b"\x00", children)


def hash16_tag(name: int, digest: bytes, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Hash tag: exactly 16 raw bytes, MSB first (ref. §3)."""
    if len(digest) != 16:
        raise EcProtocolError(f"EC hash: 16 bytes expected, got {len(digest)}")
    return EcTag(name, codes.EC_TAGTYPE_HASH16, digest, children)


def empty_tag(name: int, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Empty tag (CECEmptyTag, ref. §2): CUSTOM type, TAGLEN 0 — shape of the ``CAN_*`` tags."""
    return EcTag(name, codes.EC_TAGTYPE_CUSTOM, b"", children)


_TAG_HEADER_SIZE = 7  # TAGNAME (2) + TAGTYPE (1) + TAGLEN (4) — ref. §2
_TAGCOUNT_SIZE = 2  # uint16, present ONLY if bit 0 of TAGNAME is set


def _tag_len(tag: EcTag) -> int:
    """TAGLEN (ref. §2, GetTagLen, ECTag.cpp:553-561): own value + FULL serialized
    size of each child (its TAGLEN + its 7 header bytes + its 2 TAGCOUNT bytes if it
    itself has children). EXCLUDES the tag's own header and TAGCOUNT."""
    return len(tag.value) + sum(_serialized_len(child) for child in tag.children)


def _serialized_len(tag: EcTag) -> int:
    """Full serialized size of a tag (what its PARENT counts in its TAGLEN)."""
    return _TAG_HEADER_SIZE + (_TAGCOUNT_SIZE if tag.children else 0) + _tag_len(tag)


def _encode_tag(tag: EcTag) -> bytes:
    """Serializes a tag: shifted TAGNAME, type, TAGLEN, [TAGCOUNT + children], value (ref. §2)."""
    wire_name = (tag.name << 1) | (1 if tag.children else 0)
    out = wire_name.to_bytes(2, "big") + bytes([tag.tag_type]) + _tag_len(tag).to_bytes(4, "big")
    if tag.children:
        out += len(tag.children).to_bytes(2, "big")
        for child in tag.children:
            out += _encode_tag(child)
    return out + tag.value  # sub-tags BEFORE the own value (ref. §2)


def encode_packet(packet: EcPacket) -> bytes:
    """Full frame: 8-byte header (flags 0x20, length) + opcode + TAGCOUNT + tags.

    DECISION 2: we announce no capability → we ALWAYS emit ``flags = 0x20`` (neither zlib
    nor UTF-8 numbers); the opcode and counters are therefore raw (ref. §1).
    """
    payload = bytes([packet.opcode]) + len(packet.tags).to_bytes(2, "big")
    for tag in packet.tags:
        payload += _encode_tag(tag)
    return codes.EC_FLAG_BASE.to_bytes(4, "big") + len(payload).to_bytes(4, "big") + payload


_HEADER_SIZE = 8  # EC_HEADER_SIZE (ECSocket.h:72), ref. §1
_MAX_PACKET_PAYLOAD = 16 * 1024 * 1024  # aMule cap (ReadHeader, ECSocket.cpp:540)
_MAX_DECOMPRESSED = 16 * 1024 * 1024  # defensive bound on zlib inflation (DECISION 3)
_MAX_TAG_DEPTH = 32  # defensive nesting bound (DECISION 3)
# DECISION 2: only two flag combinations are accepted on read.
_ACCEPTED_FLAGS = (codes.EC_FLAG_BASE, codes.EC_FLAG_BASE | codes.EC_FLAG_ZLIB)


class _Reader:
    """Bounded cursor over a payload: any read past the end → ``EcProtocolError``."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    @property
    def exhausted(self) -> bool:
        return self._pos == len(self._data)

    @property
    def position(self) -> int:
        return self._pos

    def take(self, count: int) -> bytes:
        if self._pos + count > len(self._data):
            raise EcProtocolError(
                f"truncated EC packet: reading {count} bytes at offset {self._pos}, "
                f"{len(self._data) - self._pos} left"
            )
        chunk = self._data[self._pos : self._pos + count]
        self._pos += count
        return chunk

    def read_u8(self) -> int:
        return self.take(1)[0]

    def read_u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def read_u32(self) -> int:
        return int.from_bytes(self.take(4), "big")


def decode_header(header: bytes) -> tuple[int, int]:
    """Fixed 8-byte header → ``(flags, length)``, STRICT validation (ref. §1)."""
    if len(header) != _HEADER_SIZE:
        raise EcProtocolError(f"EC header: 8 bytes expected, got {len(header)}")
    flags = int.from_bytes(header[:4], "big")
    length = int.from_bytes(header[4:], "big")
    if flags not in _ACCEPTED_FLAGS:
        raise EcProtocolError(f"EC flags rejected: 0x{flags:08X} (accepted: 0x20, 0x21)")
    if length > _MAX_PACKET_PAYLOAD:
        raise EcProtocolError(f"aberrant packet length: {length}")
    return flags, length


def _inflate(data: bytes) -> bytes:
    """BOUNDED zlib decompression (ref. §1 EC_FLAG_ZLIB; spec §6 defensive parsing)."""
    decompressor = zlib.decompressobj()
    try:
        inflated = decompressor.decompress(data, _MAX_DECOMPRESSED)
    except zlib.error as exc:
        raise EcProtocolError(f"corrupt zlib stream: {exc}") from exc
    if decompressor.unconsumed_tail or not decompressor.eof:
        raise EcProtocolError("zlib stream out of bounds or truncated")
    return inflated


def _decode_tag(reader: _Reader, depth: int) -> EcTag:
    """Decodes a tag (ref. §2): ``TAGNAME >> 1``, children BEFORE the own value,
    own value = ``TAGLEN - Σ(serialized size of children)`` (ECTag.cpp:436-438).

    The children's size is measured by the cursor's POSITION DELTA (actual wire
    consumption), never recomputed on the decoded tree: every byte read must be counted."""
    if depth >= _MAX_TAG_DEPTH:
        raise EcProtocolError("tag nesting too deep")
    wire_name = reader.read_u16()
    name = wire_name >> 1
    has_children = bool(wire_name & 0x01)
    tag_type = reader.read_u8()
    tag_len = reader.read_u32()
    children: tuple[EcTag, ...] = ()
    children_size = 0
    if has_children:
        count = reader.read_u16()
        if count == 0:
            # The aMule encoder only sets the children bit when sub-tags exist;
            # accepting this shape would let the value absorb the 2 TAGCOUNT bytes.
            raise EcProtocolError(f"tag 0x{name:04X}: children bit without TAGCOUNT")
        pos_before = reader.position
        children = tuple(_decode_tag(reader, depth + 1) for _ in range(count))
        children_size = reader.position - pos_before
    own_len = tag_len - children_size
    if own_len < 0:
        raise EcProtocolError(f"lying TAGLEN on tag 0x{name:04X}")
    return EcTag(name, tag_type, reader.take(own_len), children)


def decode_payload(flags: int, payload: bytes) -> EcPacket:
    """Payload (possibly zlib) → ``EcPacket``. Any leftover byte is an error."""
    if flags & codes.EC_FLAG_ZLIB:
        payload = _inflate(payload)
    reader = _Reader(payload)
    opcode = reader.read_u8()
    tag_count = reader.read_u16()
    tags = tuple(_decode_tag(reader, depth=0) for _ in range(tag_count))
    if not reader.exhausted:
        raise EcProtocolError("leftover bytes after the last tag")
    return EcPacket(opcode, tags)


def decode_packet(frame: bytes) -> EcPacket:
    """Full frame (header + payload) → ``EcPacket`` (convenience for tests/fake server)."""
    flags, length = decode_header(frame[:_HEADER_SIZE])
    if len(frame) != _HEADER_SIZE + length:
        raise EcProtocolError("frame length inconsistent with the header")
    return decode_payload(flags, frame[_HEADER_SIZE:])
