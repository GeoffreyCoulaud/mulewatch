"""Tests for ``get_listen_port`` / ``set_listen_port`` (High-ID port-sync, design §2.3/§4.2).

Idiom of the rest of the EC suite: scripted fake transport (short-circuits the network) for
assertions on the SENT packets + a REAL round-trip via ``FakeEcServer`` (real asyncio streams
+ real codec) to prove the (de)serialization of nested parent/child tags.
"""

import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    empty_tag,
    encode_packet,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import EcConnectError, EcProtocolError
from tests.adapters.mule_ec.ec_fakes import FakeEcServer

_PASSWORD = "secret123"


def _auth_replies(salt: int) -> list[bytes]:
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


def _prefs_reply(tcp_port: int) -> EcPacket:
    """The GET_PREFERENCES RESPONSE: opcode SET_PREFERENCES (0x40, PITFALL R3) + parent
    CONNECTIONS carrying the TCP_PORT child (and a UDP_PORT, present upstream)."""
    return EcPacket(
        codes.EC_OP_SET_PREFERENCES,
        (
            empty_tag(
                codes.EC_TAG_PREFS_CONNECTIONS,
                (
                    uint_tag(codes.EC_TAG_CONN_TCP_PORT, tcp_port),
                    uint_tag(codes.EC_TAG_CONN_UDP_PORT, tcp_port + 1),
                ),
            ),
        ),
    )


# ---------------------------------------------------------------- get_listen_port


@pytest.mark.asyncio
async def test_get_listen_port_reads_tcp_port_child() -> None:
    # The reply carries the SET_PREFERENCES opcode (PITFALL R3, NOT GET_PREFERENCES); the client
    # reads the EC_TAG_CONN_TCP_PORT child under the EC_TAG_PREFS_CONNECTIONS parent.
    transport = _ScriptedTransport([_prefs_reply(4662)])
    client = _connected_client(transport)
    port = await client.get_listen_port()
    assert port == 4662
    # The emitted request: GET_PREFERENCES with the EC_PREFS_CONNECTIONS selector.
    sent = transport.sent[0]
    assert sent.opcode == codes.EC_OP_GET_PREFERENCES
    selector = sent.find(codes.EC_TAG_SELECT_PREFS)
    assert selector is not None
    assert selector.int_value() == codes.EC_PREFS_CONNECTIONS


@pytest.mark.asyncio
async def test_get_listen_port_raises_when_connections_parent_is_missing() -> None:
    # Reply conforming to the opcode (0x40) but WITHOUT the EC_TAG_PREFS_CONNECTIONS parent →
    # non-conforming reply → EcProtocolError (the loop catches it as "EC unavailable" → backoff).
    reply = EcPacket(codes.EC_OP_SET_PREFERENCES, ())
    client = _connected_client(_ScriptedTransport([reply]))
    with pytest.raises(EcProtocolError, match="CONNECTIONS"):
        await client.get_listen_port()


@pytest.mark.asyncio
async def test_get_listen_port_raises_when_tcp_port_child_is_missing() -> None:
    # Parent present BUT without the EC_TAG_CONN_TCP_PORT child → EcProtocolError (2nd branch).
    reply = EcPacket(
        codes.EC_OP_SET_PREFERENCES,
        (empty_tag(codes.EC_TAG_PREFS_CONNECTIONS, ()),),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    with pytest.raises(EcProtocolError, match="TCP_PORT"):
        await client.get_listen_port()


@pytest.mark.asyncio
async def test_get_listen_port_unexpected_opcode_raises_protocol_error() -> None:
    # An UNEXPECTED opcode (not 0x40) → EcProtocolError via _request (R3: if the real observation
    # differs, this is WHERE the expected would be adjusted).
    reply = EcPacket(codes.EC_OP_NOOP, ())
    client = _connected_client(_ScriptedTransport([reply]))
    with pytest.raises(EcProtocolError, match="expected"):
        await client.get_listen_port()


@pytest.mark.asyncio
async def test_get_listen_port_on_disconnected_client_raises_connect_error() -> None:
    client = AmuleEcClient("h", 4712, "pwd")  # never connected
    with pytest.raises(EcConnectError):
        await client.get_listen_port()


@pytest.mark.asyncio
async def test_get_listen_port_survives_a_real_codec_round_trip() -> None:
    # REAL round-trip (FakeEcServer + real streams + real codec): proves that the
    # EC_TAG_PREFS_CONNECTIONS parent and its children survive encode → socket → decode → read.
    reply = encode_packet(_prefs_reply(51820))
    async with FakeEcServer(_auth_replies(1) + [reply]) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        port = await client.get_listen_port()
        await client.close()
    assert port == 51820
    request = server.received[2]  # [0]/[1] = handshake; [2] = GET_PREFERENCES
    assert request.opcode == codes.EC_OP_GET_PREFERENCES


# ---------------------------------------------------------------- set_listen_port


@pytest.mark.asyncio
async def test_set_listen_port_emits_connections_parent_with_tcp_and_udp() -> None:
    transport = _ScriptedTransport([EcPacket(codes.EC_OP_NOOP)])
    client = _connected_client(transport)
    await client.set_listen_port(51820)
    sent = transport.sent[0]
    assert sent.opcode == codes.EC_OP_SET_PREFERENCES
    parent = sent.find(codes.EC_TAG_PREFS_CONNECTIONS)
    assert parent is not None
    tcp = parent.find(codes.EC_TAG_CONN_TCP_PORT)
    udp = parent.find(codes.EC_TAG_CONN_UDP_PORT)
    assert tcp is not None and tcp.int_value() == 51820
    assert udp is not None and udp.int_value() == 51820  # TCP=UDP=N (design §4.2)


@pytest.mark.asyncio
async def test_set_listen_port_accepts_noop_reply() -> None:
    transport = _ScriptedTransport([EcPacket(codes.EC_OP_NOOP)])
    client = _connected_client(transport)
    await client.set_listen_port(4662)  # EC_OP_NOOP reply → no exception


@pytest.mark.asyncio
async def test_set_listen_port_unexpected_reply_opcode_raises_protocol_error() -> None:
    reply = EcPacket(codes.EC_OP_MISC_DATA, ())  # not NOOP
    client = _connected_client(_ScriptedTransport([reply]))
    with pytest.raises(EcProtocolError, match="expected"):
        await client.set_listen_port(4662)


@pytest.mark.asyncio
async def test_set_listen_port_on_disconnected_client_raises_connect_error() -> None:
    client = AmuleEcClient("h", 4712, "pwd")  # never connected
    with pytest.raises(EcConnectError):
        await client.set_listen_port(4662)


@pytest.mark.asyncio
async def test_set_listen_port_survives_a_real_codec_round_trip() -> None:
    # REAL round-trip: the CONNECTIONS parent carrying TCP+UDP is encoded, transmitted, and the
    # server receives and decodes it (proof that the parent/children wire offset holds).
    reply = encode_packet(EcPacket(codes.EC_OP_NOOP))
    async with FakeEcServer(_auth_replies(1) + [reply]) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        await client.set_listen_port(51820)
        await client.close()
    request = server.received[2]
    assert request.opcode == codes.EC_OP_SET_PREFERENCES
    parent = request.find(codes.EC_TAG_PREFS_CONNECTIONS)
    assert parent is not None
    tcp = parent.find(codes.EC_TAG_CONN_TCP_PORT)
    assert tcp is not None and tcp.int_value() == 51820
    received_child = parent.find(codes.EC_TAG_CONN_UDP_PORT)
    assert isinstance(received_child, EcTag)  # UDP child indeed present in the received frame
