"""Codec EC PUR et SYNCHRONE : bytes ↔ arbre de tags (cf. docs/reference/ec-protocol.md §1-§3).

GÉNÉRIQUE : encode/décode N'IMPORTE QUEL paquet EC (format conteneur récursif). AUCUNE I/O.
Les noms de tags manipulés ici sont LOGIQUES ; le décalage wire ``(nom << 1) | enfants``
(réf. §2, piège 2) est enfermé dans l'encodage/décodage (Tasks 6-8).
"""

import zlib
from dataclasses import dataclass
from typing import Final

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.errors import EcProtocolError

# Largeur (octets) de chaque type entier — réf. §3. Ordre croissant : uint_tag prend le 1er
# qui suffit (« encodé au plus court », InitInt, ECTag.cpp:207-221).
INT_WIDTHS: Final[dict[int, int]] = {
    codes.EC_TAGTYPE_UINT8: 1,
    codes.EC_TAGTYPE_UINT16: 2,
    codes.EC_TAGTYPE_UINT32: 4,
    codes.EC_TAGTYPE_UINT64: 8,
}


@dataclass(frozen=True)
class EcTag:
    """Un tag EC : nom LOGIQUE (déjà ``>> 1``), type, valeur propre, sous-tags."""

    name: int
    tag_type: int
    value: bytes = b""
    children: tuple["EcTag", ...] = ()

    def find(self, name: int) -> "EcTag | None":
        """Premier enfant portant ce nom logique, ou ``None``."""
        for child in self.children:
            if child.name == name:
                return child
        return None

    def int_value(self) -> int:
        """Valeur entière à LARGEUR VARIABLE (réf. §9 piège 4 — équivalent ``GetInt()``)."""
        if self.tag_type not in INT_WIDTHS or len(self.value) != INT_WIDTHS[self.tag_type]:
            raise EcProtocolError(f"tag 0x{self.name:04X} : pas un entier EC valide")
        return int.from_bytes(self.value, "big")

    def string_value(self) -> str:
        """Valeur chaîne : UTF-8 + NUL final inclus dans TAGLEN (réf. §3, piège 10).

        Décodage ``errors="replace"`` : un nom de fichier hostile ne crashe jamais
        (les octets bruts restent disponibles dans ``value``).
        """
        if self.tag_type != codes.EC_TAGTYPE_STRING or not self.value.endswith(b"\x00"):
            raise EcProtocolError(f"tag 0x{self.name:04X} : pas une chaîne EC valide")
        return self.value[:-1].decode("utf-8", errors="replace")

    def ipv4_value(self) -> str:
        """Valeur IPV4 (réf. §3) : 4 octets d'IP + port uint16 big-endian → ``"a.b.c.d:port"``."""
        if self.tag_type != codes.EC_TAGTYPE_IPV4 or len(self.value) != 6:
            raise EcProtocolError(f"tag 0x{self.name:04X} : pas un IPv4 EC valide")
        ip = ".".join(str(byte) for byte in self.value[:4])
        port = int.from_bytes(self.value[4:6], "big")
        return f"{ip}:{port}"


@dataclass(frozen=True)
class EcPacket:
    """Un paquet EC : opcode + tags de premier niveau (le paquet est un pseudo-tag, réf. §2)."""

    opcode: int
    tags: tuple[EcTag, ...] = ()

    def find(self, name: int) -> EcTag | None:
        """Premier tag de premier niveau portant ce nom logique, ou ``None``."""
        for tag in self.tags:
            if tag.name == name:
                return tag
        return None


def uint_tag(name: int, value: int, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Tag entier encodé AU PLUS COURT (réf. §3 : InitInt)."""
    if value < 0:
        raise EcProtocolError(f"entier EC négatif : {value}")
    for tag_type, width in INT_WIDTHS.items():
        if value < 1 << (8 * width):
            return EcTag(name, tag_type, value.to_bytes(width, "big"), children)
    raise EcProtocolError(f"entier trop grand pour EC : {value}")


def string_tag(name: int, text: str, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Tag chaîne : UTF-8 + NUL final, INCLUS dans la longueur (réf. §3, piège 10)."""
    return EcTag(name, codes.EC_TAGTYPE_STRING, text.encode("utf-8") + b"\x00", children)


def hash16_tag(name: int, digest: bytes, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Tag hash : exactement 16 octets bruts, MSB first (réf. §3)."""
    if len(digest) != 16:
        raise EcProtocolError(f"hash EC : 16 octets attendus, reçu {len(digest)}")
    return EcTag(name, codes.EC_TAGTYPE_HASH16, digest, children)


def empty_tag(name: int, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Tag vide (CECEmptyTag, réf. §2) : type CUSTOM, TAGLEN 0 — forme des tags ``CAN_*``."""
    return EcTag(name, codes.EC_TAGTYPE_CUSTOM, b"", children)


_TAG_HEADER_SIZE = 7  # TAGNAME (2) + TAGTYPE (1) + TAGLEN (4) — réf. §2
_TAGCOUNT_SIZE = 2  # uint16, présent UNIQUEMENT si le bit 0 du TAGNAME est à 1


def _tag_len(tag: EcTag) -> int:
    """TAGLEN (réf. §2, GetTagLen, ECTag.cpp:553-561) : valeur propre + taille sérialisée
    COMPLÈTE de chaque enfant (son TAGLEN + ses 7 octets d'en-tête + ses 2 octets de
    TAGCOUNT s'il a lui-même des enfants). EXCLUT l'en-tête et le TAGCOUNT du tag lui-même."""
    return len(tag.value) + sum(_serialized_len(child) for child in tag.children)


def _serialized_len(tag: EcTag) -> int:
    """Taille sérialisée complète d'un tag (ce que son PARENT compte dans son TAGLEN)."""
    return _TAG_HEADER_SIZE + (_TAGCOUNT_SIZE if tag.children else 0) + _tag_len(tag)


def _encode_tag(tag: EcTag) -> bytes:
    """Sérialise un tag : TAGNAME décalé, type, TAGLEN, [TAGCOUNT + enfants], valeur (réf. §2)."""
    wire_name = (tag.name << 1) | (1 if tag.children else 0)
    out = wire_name.to_bytes(2, "big") + bytes([tag.tag_type]) + _tag_len(tag).to_bytes(4, "big")
    if tag.children:
        out += len(tag.children).to_bytes(2, "big")
        for child in tag.children:
            out += _encode_tag(child)
    return out + tag.value  # sous-tags AVANT la valeur propre (réf. §2)


def encode_packet(packet: EcPacket) -> bytes:
    """Trame complète : en-tête 8 octets (flags 0x20, length) + opcode + TAGCOUNT + tags.

    DÉCISION 2 : on n'annonce aucune capacité → on émet TOUJOURS ``flags = 0x20`` (ni zlib
    ni nombres UTF-8) ; l'opcode et les compteurs sont donc bruts (réf. §1).
    """
    payload = bytes([packet.opcode]) + len(packet.tags).to_bytes(2, "big")
    for tag in packet.tags:
        payload += _encode_tag(tag)
    return codes.EC_FLAG_BASE.to_bytes(4, "big") + len(payload).to_bytes(4, "big") + payload


_HEADER_SIZE = 8  # EC_HEADER_SIZE (ECSocket.h:72), réf. §1
_MAX_PACKET_PAYLOAD = 16 * 1024 * 1024  # plafond aMule (ReadHeader, ECSocket.cpp:540)
_MAX_DECOMPRESSED = 16 * 1024 * 1024  # borne défensive sur l'inflation zlib (DÉCISION 3)
_MAX_TAG_DEPTH = 32  # borne défensive d'imbrication (DÉCISION 3)
# DÉCISION 2 : seules deux combinaisons de flags sont acceptées en lecture.
_ACCEPTED_FLAGS = (codes.EC_FLAG_BASE, codes.EC_FLAG_BASE | codes.EC_FLAG_ZLIB)


class _Reader:
    """Curseur borné sur un payload : toute lecture au-delà → ``EcProtocolError``."""

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
                f"paquet EC tronqué : lecture de {count} octets à l'offset {self._pos}, "
                f"reste {len(self._data) - self._pos}"
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
    """En-tête fixe de 8 octets → ``(flags, length)``, validation STRICTE (réf. §1)."""
    if len(header) != _HEADER_SIZE:
        raise EcProtocolError(f"en-tête EC : 8 octets attendus, reçu {len(header)}")
    flags = int.from_bytes(header[:4], "big")
    length = int.from_bytes(header[4:], "big")
    if flags not in _ACCEPTED_FLAGS:
        raise EcProtocolError(f"flags EC refusés : 0x{flags:08X} (acceptés : 0x20, 0x21)")
    if length > _MAX_PACKET_PAYLOAD:
        raise EcProtocolError(f"longueur de paquet aberrante : {length}")
    return flags, length


def _inflate(data: bytes) -> bytes:
    """Décompression zlib BORNÉE (réf. §1 EC_FLAG_ZLIB ; spec §6 parsing défensif)."""
    decompressor = zlib.decompressobj()
    try:
        inflated = decompressor.decompress(data, _MAX_DECOMPRESSED)
    except zlib.error as exc:
        raise EcProtocolError(f"flux zlib corrompu : {exc}") from exc
    if decompressor.unconsumed_tail or not decompressor.eof:
        raise EcProtocolError("flux zlib hors borne ou tronqué")
    return inflated


def _decode_tag(reader: _Reader, depth: int) -> EcTag:
    """Décode un tag (réf. §2) : ``TAGNAME >> 1``, enfants AVANT la valeur propre,
    valeur propre = ``TAGLEN - Σ(taille sérialisée des enfants)`` (ECTag.cpp:436-438).

    La taille des enfants est mesurée par DELTA DE POSITION du curseur (consommation wire
    réelle), jamais recalculée sur l'arbre décodé : tout octet lu doit être compté."""
    if depth >= _MAX_TAG_DEPTH:
        raise EcProtocolError("imbrication de tags trop profonde")
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
            # L'encodeur aMule ne pose le bit enfants que s'il existe des sous-tags ;
            # accepter cette forme ferait absorber les 2 octets de TAGCOUNT à la valeur.
            raise EcProtocolError(f"tag 0x{name:04X} : bit enfants sans TAGCOUNT")
        pos_before = reader.position
        children = tuple(_decode_tag(reader, depth + 1) for _ in range(count))
        children_size = reader.position - pos_before
    own_len = tag_len - children_size
    if own_len < 0:
        raise EcProtocolError(f"TAGLEN menteur sur le tag 0x{name:04X}")
    return EcTag(name, tag_type, reader.take(own_len), children)


def decode_payload(flags: int, payload: bytes) -> EcPacket:
    """Payload (éventuellement zlib) → ``EcPacket``. Tout octet résiduel est une erreur."""
    if flags & codes.EC_FLAG_ZLIB:
        payload = _inflate(payload)
    reader = _Reader(payload)
    opcode = reader.read_u8()
    tag_count = reader.read_u16()
    tags = tuple(_decode_tag(reader, depth=0) for _ in range(tag_count))
    if not reader.exhausted:
        raise EcProtocolError("octets résiduels après le dernier tag")
    return EcPacket(opcode, tags)


def decode_packet(frame: bytes) -> EcPacket:
    """Trame complète (en-tête + payload) → ``EcPacket`` (convenance tests/faux serveur)."""
    flags, length = decode_header(frame[:_HEADER_SIZE])
    if len(frame) != _HEADER_SIZE + length:
        raise EcProtocolError("longueur de trame incohérente avec l'en-tête")
    return decode_payload(flags, frame[_HEADER_SIZE:])
