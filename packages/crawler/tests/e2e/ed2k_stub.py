"""Stub serveur eD2k PUR (spec e2e §3) — outil de test SEUL.

Un faux serveur eD2k minimal, suffisant pour qu'un amuled s'y connecte, cherche, et que le
crawler observe un fichier planté. Il **force un HighID** (pas de port-check réel), sert **un
seul** résultat de recherche planté et **une seule** source plantée. Il ne télécharge rien (le
download réel est la couche B / Docker).

Frontière hexagonale : ce module est un **outil de test** — il n'importe RIEN de
``emule_indexer`` et n'est dans aucun import de prod. Le **codec** (encode/decode header + tags,
``build_*``, ``extract_keyword``) est PUR et testé à 100 % branche ; le **dispatch** asyncio est
testé via une paire StreamReader/Writer en mémoire. Le ``serve_forever`` réel (bind) est
``# pragma: no cover`` (exercé seulement sous Docker, lancé par Geoffrey).

Ancrages (vendor/ed2kd, cités fichier:ligne) : framing ``ed2k_proto.h:36-90`` ; tags
``ed2k_proto.h:163-172`` + ``packet.c:170-193`` (forme longue émise par ed2kd) ; IDCHANGE
``packet.c:14-25`` (id = IP en ordre réseau, écrite telle quelle) ; SEARCHRESULT
``packet.c:146-228`` ; FOUNDSOURCES ``packet.c:131-144`` ; terme de recherche
``server.c:289-296`` (``[0x01][len u16 LE][str]``).
"""

from __future__ import annotations

import asyncio
import socket
import struct
from dataclasses import dataclass

# --- Constantes du protocole eD2k (ed2k_proto.h) ---------------------------------------------

PROTO_EDONKEY = 0xE3  # paquet non compressé (ed2k_proto.h:37)
PROTO_PACKED = 0xD4  # paquet zlib (ed2k_proto.h:39) — rejeté proprement par le stub

# Opcodes (ed2k_proto.h:42-80)
OP_LOGINREQUEST = 0x01
OP_SEARCHREQUEST = 0x16
OP_GETSOURCES = 0x19
OP_SEARCHRESULT = 0x33
OP_SERVERSTATUS = 0x34
OP_SERVERMESSAGE = 0x38
OP_IDCHANGE = 0x40
OP_FOUNDSOURCES = 0x42

# Types de tags (ed2k_proto.h:180-210)
TT_STRING = 0x02
TT_UINT64 = 0x0B

# Noms de tags (ed2k_proto.h:212-233)
TN_FILENAME = 0x01
TN_FILESIZE = 0x02

# Terme de recherche chaîne (ed2k_proto.h:266 ; server.c:289-296)
SO_STRING_TERM = 0x01

# Limite LowID/HighID : un id < MAX_LOWID est interprété LowID par aMule (ed2k_proto.h:10).
MAX_LOWID = 0x1000000

ED2K_HASH_SIZE = 16


class ProtocolError(ValueError):
    """Trame eD2k malformée (header tronqué, proto inattendu, longueur incohérente)."""


# --- Codec PUR : header ----------------------------------------------------------------------


def encode_header(opcode: int, payload: bytes) -> bytes:
    """Trame eD2k complète : ``[proto 0xE3][length u32 LE][opcode u8][payload]``.

    ``length`` = ``1 (opcode) + len(payload)`` (ed2k_proto.h:87-90 — header packed sans padding).
    """
    length = 1 + len(payload)
    return struct.pack("<BIB", PROTO_EDONKEY, length, opcode) + payload


def decode_header(frame: bytes) -> tuple[int, bytes]:
    """Décode une trame complète → ``(opcode, payload)``.

    Lève ``ProtocolError`` si : trop courte pour le header, proto ≠ 0xE3, ou longueur annoncée
    incohérente avec les octets disponibles.
    """
    if len(frame) < 6:
        raise ProtocolError("trame trop courte pour le header eD2k")
    proto, length = struct.unpack_from("<BI", frame, 0)
    if proto != PROTO_EDONKEY:
        raise ProtocolError(f"proto inattendu : {proto:#04x}")
    opcode = frame[5]
    payload = frame[6:]
    if length != 1 + len(payload):
        raise ProtocolError("longueur annoncée incohérente avec le payload")
    return opcode, payload


# --- Codec PUR : tags ------------------------------------------------------------------------


def encode_string_tag(name: int, value: str) -> bytes:
    """Tag chaîne forme LONGUE (telle qu'ed2kd l'émet, packet.c:170-183).

    ``[type=0x02][name_len=1 u16 LE][name u8][str_len u16 LE][str…]`` (UTF-8).
    """
    raw = value.encode("utf-8")
    return struct.pack("<BHB", TT_STRING, 1, name) + struct.pack("<H", len(raw)) + raw


def encode_uint64_tag(name: int, value: int) -> bytes:
    """Tag entier 64 bits forme LONGUE (taille fichier, packet.c:185-193).

    ``[type=0x0B][name_len=1 u16 LE][name u8][value u64 LE]``.
    """
    return struct.pack("<BHB", TT_UINT64, 1, name) + struct.pack("<Q", value)


def _ip_to_id_bytes(ip: str) -> bytes:
    """Octets de l'IP en ordre réseau (a,b,c,d), tels qu'ed2kd les place dans IDCHANGE.

    ed2kd écrit ``clnt->id = clnt->ip`` (= ``sin_addr.s_addr``, ordre réseau) sans htonl à
    l'émission (client.c:188 ; packet.c:21) → sur le fil, le champ id = les 4 octets de l'IP
    dans l'ordre a.b.c.d. On reproduit cet ordre exact.
    """
    return socket.inet_aton(ip)


def build_idchange(ip: str, tcp_flags: int = 0) -> bytes:
    """Trame IDCHANGE forçant un HighID = IP du peer (spec e2e §3.3 ; packet.c:14-25).

    Lève ``ValueError`` si l'IP, interprétée comme l'id u32 le serait par aMule (lecture u32 LE
    du champ sur le fil), tomberait sous ``MAX_LOWID`` (donc serait vue LowID) — p.ex. une IP
    ``0.0.0.x``. Les IP Docker (``172.x``, ``10.x``…) restent HighID.
    """
    id_bytes = _ip_to_id_bytes(ip)
    id_u32 = int.from_bytes(id_bytes, "little")
    if id_u32 < MAX_LOWID:
        raise ValueError(f"IP {ip} donnerait un id LowID ({id_u32:#010x} < {MAX_LOWID:#010x})")
    payload = id_bytes + struct.pack("<I", tcp_flags)
    return encode_header(OP_IDCHANGE, payload)


def build_servermessage(message: str) -> bytes:
    """Trame SERVERMESSAGE optionnelle : ``[msg_len u16 LE][message]`` (packet.c:27-38)."""
    raw = message.encode("utf-8")
    return encode_header(OP_SERVERMESSAGE, struct.pack("<H", len(raw)) + raw)


def build_serverstatus(user_count: int, file_count: int) -> bytes:
    """Trame SERVERSTATUS optionnelle : ``[user_count u32][file_count u32]`` LE (packet.c:40-51)."""
    return encode_header(OP_SERVERSTATUS, struct.pack("<II", user_count, file_count))


def build_searchresult(name: str, size: int, file_hash: str) -> bytes:
    """Trame SEARCHRESULT à UN seul fichier planté (spec e2e §3.3 ; packet.c:146-228).

    Payload : ``[count u32 LE = 1]`` puis pour le fichier ``[hash16][id u32][port u16]
    [tag_count u32 = 2][tag FILENAME][tag FILESIZE]``. ``file_hash`` = hex 32. L'id/port de
    source ne servent pas en couche A (le crawler n'observe que le nom).
    """
    hash_bytes = bytes.fromhex(file_hash)
    if len(hash_bytes) != ED2K_HASH_SIZE:
        raise ValueError("hash ed2k attendu sur 16 octets (32 hex)")
    entry = hash_bytes + struct.pack("<IH", 0, 0) + struct.pack("<I", 2)
    entry += encode_string_tag(TN_FILENAME, name)
    entry += encode_uint64_tag(TN_FILESIZE, size)
    payload = struct.pack("<I", 1) + entry
    return encode_header(OP_SEARCHRESULT, payload)


def build_foundsources(file_hash: str, sources: list[tuple[str, int]]) -> bytes:
    """Trame FOUNDSOURCES (spec e2e §3.3 ; packet.c:131-144).

    Payload : ``[hash16][count u8]`` puis pour chaque source ``[ip4][port u16]`` — ``ip4`` en
    ordre réseau (a,b,c,d), comme la source l'a annoncée.
    """
    hash_bytes = bytes.fromhex(file_hash)
    if len(hash_bytes) != ED2K_HASH_SIZE:
        raise ValueError("hash ed2k attendu sur 16 octets (32 hex)")
    payload = hash_bytes + struct.pack("<B", len(sources))
    for ip, port in sources:
        payload += socket.inet_aton(ip) + struct.pack("<H", port)
    return encode_header(OP_FOUNDSOURCES, payload)


def extract_keyword(search_payload: bytes) -> str | None:
    """Cherche le PREMIER terme-chaîne nom dans un payload SEARCHREQUEST (spec e2e §3.4).

    On NE parse PAS l'arbre (AND/OR/NOT, contraintes) : on scanne le premier octet
    ``SO_STRING_TERM 0x01`` suivi d'une longueur u16 LE cohérente avec les octets restants, et on
    décode la chaîne (best-effort). Renvoie ``None`` si aucun terme exploitable n'est trouvé (le
    stub répond quand même son résultat planté). Couvre les deux branches (trouvé / absent).
    """
    i = 0
    n = len(search_payload)
    while i < n:
        if search_payload[i] == SO_STRING_TERM and i + 3 <= n:
            (str_len,) = struct.unpack_from("<H", search_payload, i + 1)
            if i + 3 + str_len <= n:
                raw = search_payload[i + 3 : i + 3 + str_len]
                try:
                    return raw.decode("utf-8")
                except UnicodeDecodeError:
                    return raw.decode("latin-1")
        i += 1
    return None


# --- Dispatch asyncio ------------------------------------------------------------------------


@dataclass(frozen=True)
class PlantedFile:
    """Le fichier planté servi par le stub : nom, taille, hash ed2k (hex 32)."""

    name: str
    size: int
    file_hash: str


@dataclass(frozen=True)
class PlantedSource:
    """La source plantée renvoyée à GETSOURCES : IP (ordre réseau) + port eD2k."""

    ip: str
    port: int


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Lit UNE trame eD2k complète depuis le flux → ``(opcode, payload)``.

    Lève ``asyncio.IncompleteReadError`` à l'EOF (propre ou au milieu d'une trame) — l'appelant
    le capte pour finir la connexion. Lève ``ProtocolError`` sur proto inattendu (y compris
    ``PROTO_PACKED`` que le stub rejette).
    """
    head = await reader.readexactly(5)
    proto, length = struct.unpack("<BI", head)
    if proto != PROTO_EDONKEY:
        raise ProtocolError(f"proto inattendu : {proto:#04x}")
    if length < 1:
        raise ProtocolError("longueur de trame nulle")
    body = await reader.readexactly(length)
    opcode = body[0]
    return opcode, body[1:]


async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    peer_ip: str,
    planted_file: PlantedFile,
    planted_source: PlantedSource,
) -> None:
    """Boucle de dispatch d'UNE connexion eD2k (spec e2e §3.3).

    Reçus → émis : LOGINREQUEST → SERVERMESSAGE + SERVERSTATUS + IDCHANGE (HighID forcé) ;
    SEARCHREQUEST → SEARCHRESULT (planté) ; GETSOURCES → FOUNDSOURCES (planté). Opcode inconnu
    → ignoré silencieusement. EOF / ``IncompleteReadError`` → fin propre de la connexion.
    """
    try:
        while True:
            try:
                opcode, payload = await _read_frame(reader)
            except asyncio.IncompleteReadError:
                break

            if opcode == OP_LOGINREQUEST:
                writer.write(build_servermessage("ed2k stub — welcome"))
                writer.write(build_serverstatus(1, 1))
                writer.write(build_idchange(peer_ip))
                await writer.drain()
            elif opcode == OP_SEARCHREQUEST:
                extract_keyword(payload)  # tolérant : on répond toujours le résultat planté
                writer.write(
                    build_searchresult(planted_file.name, planted_file.size, planted_file.file_hash)
                )
                await writer.drain()
            elif opcode == OP_GETSOURCES:
                file_hash = payload[:ED2K_HASH_SIZE].hex()
                writer.write(
                    build_foundsources(file_hash, [(planted_source.ip, planted_source.port)])
                )
                await writer.drain()
            else:
                # Opcode non géré (OFFERFILES, DISCONNECT, etc.) : le stub l'ignore.
                continue
    finally:
        writer.close()


async def serve_forever(  # pragma: no cover - bind réel, exercé sous Docker (lancé par Geoffrey)
    host: str,
    port: int,
    planted_file: PlantedFile,
    planted_source: PlantedSource,
) -> None:
    """Serveur eD2k réel (bind). Exercé UNIQUEMENT sous Docker (couche B)."""

    async def _on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername")
        peer_ip = peername[0] if peername else "127.0.0.1"
        await handle_connection(
            reader,
            writer,
            peer_ip=peer_ip,
            planted_file=planted_file,
            planted_source=planted_source,
        )

    server = await asyncio.start_server(_on_client, host, port)
    async with server:
        await server.serve_forever()
