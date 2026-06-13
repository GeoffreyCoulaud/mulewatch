import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    encode_packet,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import EcConnectError, EcFailureError
from emule_indexer.ports.mule_download_client import DownloadEntry
from tests.adapters.mule_ec.ec_fakes import FakeEcServer

_HASH = "a1b2c3d4e5f6071829303142535465f0"
_PASSWORD = "secret123"


def _auth_replies(salt: int) -> list[bytes]:
    """Handshake d'auth pré-encodé (même idiome que test_client.py)."""
    return [
        encode_packet(EcPacket(codes.EC_OP_AUTH_SALT, (uint_tag(codes.EC_TAG_PASSWD_SALT, salt),))),
        encode_packet(
            EcPacket(codes.EC_OP_AUTH_OK, (string_tag(codes.EC_TAG_SERVER_VERSION, "3.0.0"),))
        ),
    ]


class _ScriptedTransport:
    """Faux transport : rend des réponses SCRIPTÉES, capture les paquets envoyés."""

    def __init__(self, replies: list[EcPacket]) -> None:
        self._replies = replies
        self.sent: list[EcPacket] = []
        self.closed = False

    async def send_packet(self, packet: EcPacket) -> None:
        self.sent.append(packet)

    async def receive_packet(self) -> EcPacket:
        return self._replies.pop(0)

    async def close(self) -> None:
        self.closed = True


def _connected_client(transport: _ScriptedTransport) -> AmuleEcClient:
    client = AmuleEcClient("h", 4712, "pwd")
    client._transport = transport  # type: ignore[assignment]  # injecté (déjà connecté)
    return client


def _partfile_entry(hash_hex: str, *, done: int, full: int) -> EcTag:
    return EcTag(
        codes.EC_TAG_PARTFILE,
        codes.EC_TAGTYPE_HASH16,
        bytes.fromhex(hash_hex),
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, full),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_DONE, done),
            uint_tag(codes.EC_TAG_PARTFILE_STATUS, 0),
        ),
    )


@pytest.mark.asyncio
async def test_add_link_sends_the_link_and_accepts_noop() -> None:
    transport = _ScriptedTransport([EcPacket(codes.EC_OP_NOOP)])
    client = _connected_client(transport)
    link = "ed2k://|file|x.avi|10|" + _HASH + "|/"
    await client.add_link(link)
    sent = transport.sent[0]
    assert sent.opcode == codes.EC_OP_ADD_LINK
    assert sent.find(codes.EC_TAG_STRING) is not None
    assert sent.find(codes.EC_TAG_STRING).string_value() == link  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_add_link_failure_raises_ec_failure() -> None:
    failed = EcPacket(codes.EC_OP_FAILED, (string_tag(codes.EC_TAG_STRING, "lien invalide"),))
    client = _connected_client(_ScriptedTransport([failed]))
    with pytest.raises(EcFailureError, match="lien invalide"):
        await client.add_link("ed2k://bad")


@pytest.mark.asyncio
async def test_add_link_on_a_disconnected_client_raises_connect_error() -> None:
    client = AmuleEcClient("h", 4712, "pwd")  # jamais connecté
    with pytest.raises(EcConnectError):
        await client.add_link("ed2k://x")


@pytest.mark.asyncio
async def test_download_queue_maps_entries_to_download_entries() -> None:
    reply = EcPacket(
        codes.EC_OP_DLOAD_QUEUE,
        (
            _partfile_entry(_HASH, done=10, full=10),
            _partfile_entry("b" * 32, done=3, full=10),
        ),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    queue = await client.download_queue()
    assert queue == (
        DownloadEntry(ed2k_hash=_HASH, size_done=10, size_full=10),
        DownloadEntry(ed2k_hash="b" * 32, size_done=3, size_full=10),
    )


@pytest.mark.asyncio
async def test_download_queue_requests_at_cmd_detail() -> None:
    transport = _ScriptedTransport([EcPacket(codes.EC_OP_DLOAD_QUEUE)])
    client = _connected_client(transport)
    await client.download_queue()
    sent = transport.sent[0]  # référence typée → aucun ignore nécessaire
    assert sent.opcode == codes.EC_OP_GET_DLOAD_QUEUE
    detail = sent.find(codes.EC_TAG_DETAIL_LEVEL)
    assert detail is not None and detail.int_value() == codes.EC_DETAIL_CMD


@pytest.mark.asyncio
async def test_download_queue_skips_entries_without_a_usable_hash() -> None:
    # une entrée dont la valeur propre n'est PAS un HASH16 de 16 octets est ÉCARTÉE
    # (tolérance aux inconnus, comme map_search_results) — jamais fatale au lot.
    pourrie = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_UINT8, b"\x01", ())
    reply = EcPacket(codes.EC_OP_DLOAD_QUEUE, (pourrie, _partfile_entry(_HASH, done=1, full=2)))
    client = _connected_client(_ScriptedTransport([reply]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=1, size_full=2),)


@pytest.mark.asyncio
async def test_download_queue_skips_non_partfile_toplevel_tags() -> None:
    reply = EcPacket(
        codes.EC_OP_DLOAD_QUEUE,
        (uint_tag(codes.EC_TAG_DETAIL_LEVEL, 0), _partfile_entry(_HASH, done=1, full=2)),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=1, size_full=2),)


@pytest.mark.asyncio
async def test_download_queue_treats_missing_size_as_zero() -> None:
    # une entrée valide (hash) mais sans tags de taille → done=0, full=0 (absence = 0,
    # réf. EC §3) → is_complete False, ne sera jamais promue par erreur.
    entry = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), ())
    client = _connected_client(_ScriptedTransport([EcPacket(codes.EC_OP_DLOAD_QUEUE, (entry,))]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=0, size_full=0),)


@pytest.mark.asyncio
async def test_download_queue_survives_a_real_codec_round_trip() -> None:
    # Round-trip RÉEL (FakeEcServer + vrais streams asyncio + vrai codec, idiome du reste du
    # suite EC) : prouve qu'une entrée PARTFILE (valeur PROPRE HASH16 + enfants name/size_*)
    # survit à encode_packet → socket → decode_payload → map. Les autres tests court-circuitent
    # le codec en injectant des EcPacket/EcTag directement dans _transport.
    entry = _partfile_entry(_HASH, done=4, full=9)
    reply = encode_packet(EcPacket(codes.EC_OP_DLOAD_QUEUE, (entry,)))
    async with FakeEcServer(_auth_replies(1) + [reply]) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        queue = await client.download_queue()
        await client.close()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=4, size_full=9),)
    request = server.received[2]  # [0]/[1] = handshake ; [2] = la requête de file
    assert request.opcode == codes.EC_OP_GET_DLOAD_QUEUE


@pytest.mark.asyncio
async def test_download_queue_treats_malformed_size_as_zero() -> None:
    # un tag de taille PRÉSENT mais malformé (UINT32 déclaré, 1 octet) lève EcProtocolError à
    # int_value() ; _optional_partfile_int l'avale → 0 (jamais fatal, réf. EC §3, piège 4).
    bad_full = EcTag(codes.EC_TAG_PARTFILE_SIZE_FULL, codes.EC_TAGTYPE_UINT32, b"\x01", ())
    entry = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), (bad_full,))
    client = _connected_client(_ScriptedTransport([EcPacket(codes.EC_OP_DLOAD_QUEUE, (entry,))]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=0, size_full=0),)
