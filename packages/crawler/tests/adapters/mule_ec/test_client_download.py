import pytest

from mulewatch.adapters.mule_ec import codes
from mulewatch.adapters.mule_ec.client import AmuleEcClient
from mulewatch.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    encode_packet,
    string_tag,
    uint_tag,
)
from mulewatch.adapters.mule_ec.errors import EcConnectError, EcFailureError
from mulewatch.ports.mule_download_client import DownloadEntry, SharedFileEntry
from tests.adapters.mule_ec.ec_fakes import FakeEcServer

_HASH = "a1b2c3d4e5f6071829303142535465f0"
_PASSWORD = "secret123"


def _auth_replies(salt: int) -> list[bytes]:
    """Pre-encoded auth handshake (same idiom as test_client.py)."""
    return [
        encode_packet(EcPacket(codes.EC_OP_AUTH_SALT, (uint_tag(codes.EC_TAG_PASSWD_SALT, salt),))),
        encode_packet(
            EcPacket(codes.EC_OP_AUTH_OK, (string_tag(codes.EC_TAG_SERVER_VERSION, "3.0.0"),))
        ),
    ]


class _ScriptedTransport:
    """Fake transport: returns SCRIPTED replies, captures sent packets."""

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
    client._transport = transport  # type: ignore[assignment]  # injected (already connected)
    return client


def _partfile_entry(hash_hex: str, *, done: int, full: int) -> EcTag:
    # REAL layout (verified against a real amuled): the OWN value of the EC_TAG_PARTFILE tag is
    # a UINT8 (internal index/status, e.g. 0x0d), NOT the hash; the hash is the dedicated child
    # EC_TAG_PARTFILE_HASH (0x031E, HASH16). size_full/size_done stay as children.
    return EcTag(
        codes.EC_TAG_PARTFILE,
        codes.EC_TAGTYPE_UINT8,
        bytes([1]),
        (
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(hash_hex), ()),
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
    failed = EcPacket(codes.EC_OP_FAILED, (string_tag(codes.EC_TAG_STRING, "invalid link"),))
    client = _connected_client(_ScriptedTransport([failed]))
    with pytest.raises(EcFailureError, match="invalid link"):
        await client.add_link("ed2k://bad")


@pytest.mark.asyncio
async def test_add_link_on_a_disconnected_client_raises_connect_error() -> None:
    client = AmuleEcClient("h", 4712, "pwd")  # never connected
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
    sent = transport.sent[0]  # typed reference → no ignore needed
    assert sent.opcode == codes.EC_OP_GET_DLOAD_QUEUE
    detail = sent.find(codes.EC_TAG_DETAIL_LEVEL)
    assert detail is not None and detail.int_value() == codes.EC_DETAIL_CMD


@pytest.mark.asyncio
async def test_download_queue_skips_entries_without_a_usable_hash() -> None:
    # an entry WITHOUT a usable EC_TAG_PARTFILE_HASH child is DISCARDED (tolerance toward
    # unknowns, like map_search_results) — never fatal to the batch. Here: UINT8 own value
    # (the internal index/status) BUT no 0x031E child → no hash → discarded.
    pourrie = EcTag(
        codes.EC_TAG_PARTFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (string_tag(codes.EC_TAG_PARTFILE_NAME, "orpheline.avi"),),
    )
    reply = EcPacket(codes.EC_OP_DLOAD_QUEUE, (pourrie, _partfile_entry(_HASH, done=1, full=2)))
    client = _connected_client(_ScriptedTransport([reply]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=1, size_full=2),)


@pytest.mark.asyncio
async def test_download_queue_skips_entries_with_a_wrong_length_hash_child() -> None:
    # a 0x031E child PRESENT but malformed (HASH16 declared, length ≠ 16) → discarded:
    # the hash is the ONLY stable identifier, without it the entry is unusable.
    bad_hash = EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, b"\x00" * 8, ())
    pourrie = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_UINT8, b"\x01", (bad_hash,))
    reply = EcPacket(codes.EC_OP_DLOAD_QUEUE, (pourrie, _partfile_entry(_HASH, done=1, full=2)))
    client = _connected_client(_ScriptedTransport([reply]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=1, size_full=2),)


@pytest.mark.asyncio
async def test_download_queue_skips_entries_with_a_wrong_type_hash_child() -> None:
    # a 0x031E child PRESENT but of a type that is NOT HASH16 (e.g. STRING) → discarded.
    wrong_type = string_tag(codes.EC_TAG_PARTFILE_HASH, "pas-un-hash")
    pourrie = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_UINT8, b"\x01", (wrong_type,))
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
    # a valid entry (0x031E hash child present) but WITHOUT size tags → done=0,
    # full=0 (absence = 0, ref. EC §3) → is_complete False, will never be promoted by mistake.
    hash_child = EcTag(
        codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), ()
    )
    entry = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_UINT8, bytes([1]), (hash_child,))
    client = _connected_client(_ScriptedTransport([EcPacket(codes.EC_OP_DLOAD_QUEUE, (entry,))]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=0, size_full=0),)


@pytest.mark.asyncio
async def test_download_queue_survives_a_real_codec_round_trip() -> None:
    # REAL round-trip (FakeEcServer + real asyncio streams + real codec, idiom of the rest of
    # the EC suite): proves that a PARTFILE entry with the REAL layout (UINT8 own value + HASH16
    # 0x031E child + name/size_* children) survives encode_packet → socket → decode_payload →
    # map. The other tests short-circuit the codec by injecting EcPacket/EcTag into _transport.
    entry = _partfile_entry(_HASH, done=4, full=9)
    reply = encode_packet(EcPacket(codes.EC_OP_DLOAD_QUEUE, (entry,)))
    async with FakeEcServer(_auth_replies(1) + [reply]) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        queue = await client.download_queue()
        await client.close()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=4, size_full=9),)
    request = server.received[2]  # [0]/[1] = handshake; [2] = the queue request
    assert request.opcode == codes.EC_OP_GET_DLOAD_QUEUE


@pytest.mark.asyncio
async def test_download_queue_treats_malformed_size_as_zero() -> None:
    # valid entry (0x031E hash child present) with a size tag PRESENT but malformed
    # (UINT32 declared, 1 byte) → int_value() raises EcProtocolError; _optional_partfile_int
    # swallows it → 0 (never fatal, ref. EC §3, pitfall 4). The entry is NOT discarded
    # (the hash is there).
    hash_child = EcTag(
        codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), ()
    )
    bad_full = EcTag(codes.EC_TAG_PARTFILE_SIZE_FULL, codes.EC_TAGTYPE_UINT32, b"\x01", ())
    entry = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_UINT8, bytes([1]), (hash_child, bad_full))
    client = _connected_client(_ScriptedTransport([EcPacket(codes.EC_OP_DLOAD_QUEUE, (entry,))]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=0, size_full=0),)


def _knownfile_entry(hash_hex: str, name: str) -> EcTag:
    # EC_TAG_KNOWNFILE (0x0400) container; own value = ECID (UINT, ignored). The hash is
    # the EC_TAG_PARTFILE_HASH child (HASH16), the name the EC_TAG_PARTFILE_NAME child (real
    # on-disk name on the amuled side). Same child tags as the partfile (confirmed upstream,
    # commit 5938915).
    return EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        bytes([1]),
        (
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(hash_hex), ()),
            string_tag(codes.EC_TAG_PARTFILE_NAME, name),
        ),
    )


def test_map_shared_file_extracts_hash_and_name() -> None:
    from mulewatch.adapters.mule_ec.client import _map_shared_file

    entry = _map_shared_file(_knownfile_entry(_HASH, "Keroro 62a.avi"))
    assert entry == SharedFileEntry(ed2k_hash=_HASH, name="Keroro 62a.avi")


def test_map_shared_file_without_hash_is_none() -> None:
    from mulewatch.adapters.mule_ec.client import _map_shared_file

    no_hash = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (string_tag(codes.EC_TAG_PARTFILE_NAME, "orpheline.avi"),),
    )
    assert _map_shared_file(no_hash) is None


def test_map_shared_file_without_name_is_none() -> None:
    from mulewatch.adapters.mule_ec.client import _map_shared_file

    no_name = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), ()),),
    )
    assert _map_shared_file(no_name) is None


def test_map_shared_file_with_wrong_length_hash_is_none() -> None:
    from mulewatch.adapters.mule_ec.client import _map_shared_file

    bad = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, b"\x00" * 8, ()),
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
        ),
    )
    assert _map_shared_file(bad) is None


def test_map_shared_file_with_invalid_name_tag_is_none() -> None:
    from mulewatch.adapters.mule_ec.client import _map_shared_file

    # name tag of STRING type but WITHOUT a terminating NUL → string_value() raises EcProtocolError.
    bad_name = EcTag(codes.EC_TAG_PARTFILE_NAME, codes.EC_TAGTYPE_STRING, b"no-nul", ())
    entry = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), ()),
            bad_name,
        ),
    )
    assert _map_shared_file(entry) is None


@pytest.mark.asyncio
async def test_shared_files_maps_entries() -> None:
    reply = EcPacket(
        codes.EC_OP_SHARED_FILES,
        (_knownfile_entry(_HASH, "A.avi"), _knownfile_entry("b" * 32, "B.avi")),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    shared = await client.shared_files()
    assert shared == (
        SharedFileEntry(ed2k_hash=_HASH, name="A.avi"),
        SharedFileEntry(ed2k_hash="b" * 32, name="B.avi"),
    )


@pytest.mark.asyncio
async def test_shared_files_requests_at_cmd_detail() -> None:
    transport = _ScriptedTransport([EcPacket(codes.EC_OP_SHARED_FILES)])
    client = _connected_client(transport)
    await client.shared_files()
    sent = transport.sent[0]
    assert sent.opcode == codes.EC_OP_GET_SHARED_FILES
    detail = sent.find(codes.EC_TAG_DETAIL_LEVEL)
    assert detail is not None and detail.int_value() == codes.EC_DETAIL_CMD


@pytest.mark.asyncio
async def test_shared_files_skips_non_knownfile_top_level_tags() -> None:
    reply = EcPacket(
        codes.EC_OP_SHARED_FILES,
        (string_tag(codes.EC_TAG_STRING, "bruit"), _knownfile_entry(_HASH, "A.avi")),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    shared = await client.shared_files()
    assert shared == (SharedFileEntry(ed2k_hash=_HASH, name="A.avi"),)


@pytest.mark.asyncio
async def test_shared_files_skips_unmappable_knownfile_entries() -> None:
    # an unusable EC_TAG_KNOWNFILE entry (no hash → _map_shared_file returns None) is
    # DISCARDED; the next one, valid, is kept (tolerance toward unknowns, like download_queue).
    no_hash = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (string_tag(codes.EC_TAG_PARTFILE_NAME, "orpheline.avi"),),
    )
    reply = EcPacket(codes.EC_OP_SHARED_FILES, (no_hash, _knownfile_entry(_HASH, "A.avi")))
    client = _connected_client(_ScriptedTransport([reply]))
    shared = await client.shared_files()
    assert shared == (SharedFileEntry(ed2k_hash=_HASH, name="A.avi"),)


@pytest.mark.asyncio
async def test_shared_files_empty_reply_is_empty_tuple() -> None:
    client = _connected_client(_ScriptedTransport([EcPacket(codes.EC_OP_SHARED_FILES)]))
    assert await client.shared_files() == ()
