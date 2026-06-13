import asyncio
import socket

import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import EcPacket, encode_packet, string_tag
from emule_indexer.adapters.mule_ec.errors import (
    EcConnectError,
    EcProtocolError,
    EcTimeoutError,
)
from emule_indexer.adapters.mule_ec.transport import open_ec_transport
from tests.adapters.mule_ec.ec_fakes import FakeEcServer

_NOOP = EcPacket(codes.EC_OP_NOOP)
_REPLY = EcPacket(codes.EC_OP_STRINGS, (string_tag(codes.EC_TAG_STRING, "ok"),))


@pytest.mark.asyncio
async def test_send_then_receive_one_packet_fcfs() -> None:
    async with FakeEcServer([encode_packet(_REPLY)]) as server:
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        await transport.send_packet(_NOOP)
        assert await transport.receive_packet() == _REPLY
        assert server.received == [_NOOP]
        await transport.close()


@pytest.mark.asyncio
async def test_receive_times_out_when_server_stays_silent() -> None:
    async with FakeEcServer([]) as server:  # lit la requête puis se tait
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=0.2)
        await transport.send_packet(_NOOP)
        with pytest.raises(EcTimeoutError):
            await transport.receive_packet()
        await transport.close()


@pytest.mark.asyncio
async def test_receive_raises_connect_error_on_eof() -> None:
    async with FakeEcServer([], close_after=0) as server:  # ferme dès l'accept
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        with pytest.raises(EcConnectError):
            await transport.receive_packet()
        await transport.close()


@pytest.mark.asyncio
async def test_send_raises_connect_error_on_lost_connection() -> None:
    async with FakeEcServer([]) as server:
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        await transport.close()  # drain() sur connexion fermée → ConnectionResetError
        with pytest.raises(EcConnectError):
            await transport.send_packet(_NOOP)


@pytest.mark.asyncio
async def test_receive_propagates_protocol_error_on_malformed_header() -> None:
    async with FakeEcServer([bytes.fromhex("00000060" "00000003" "010000")]) as server:  # fmt: skip
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        await transport.send_packet(_NOOP)
        with pytest.raises(EcProtocolError):
            await transport.receive_packet()
        await transport.close()


@pytest.mark.asyncio
async def test_close_after_connection_reset_does_not_raise() -> None:
    async with FakeEcServer([], abort=True) as server:  # RST après la requête lue
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        await transport.send_packet(_NOOP)
        with pytest.raises(EcConnectError):
            await transport.receive_packet()
        await transport.close()  # ne doit PAS re-lever le ConnectionResetError stocké
        await transport.close()  # double close : idempotent, ne lève pas non plus


@pytest.mark.asyncio
async def test_connect_refused_raises_connect_error() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))  # lié mais PAS en écoute → RST déterministe (Linux)
    refused_port = probe.getsockname()[1]
    try:
        with pytest.raises(EcConnectError):
            await open_ec_transport("127.0.0.1", refused_port, timeout=2.0)
    finally:
        probe.close()


@pytest.mark.asyncio
async def test_connect_timeout_raises_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _hang(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", _hang)
    with pytest.raises(EcTimeoutError):
        await open_ec_transport("127.0.0.1", 4712, timeout=0.05)
