"""Async EC transport: frames ONE packet at a time over a StreamReader/Writer (spec §4).

Timeout on EVERY network read (+ the TCP establishment), configurable (spec §6).
NO policy here: no retry, no reconnection, no sleep — the adapter
signals, the caller decides (spec §3/§6). Strict FCFS: one response per request.
"""

import asyncio
import contextlib

from mulewatch.adapters.mule_ec.codec import (
    EcPacket,
    decode_header,
    decode_payload,
    encode_packet,
)
from mulewatch.adapters.mule_ec.errors import EcConnectError, EcTimeoutError

_HEADER_SIZE = 8  # ref. §1 (EC_HEADER_SIZE, ECSocket.h:72)


class EcTransport:
    """Frames sending/receiving one full EC packet over an established connection."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, timeout: float
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._timeout = timeout

    async def send_packet(self, packet: EcPacket) -> None:
        """Emits a full frame (DECISION 5: no write timeout)."""
        try:
            self._writer.write(encode_packet(packet))
            await self._writer.drain()
        except OSError as exc:
            raise EcConnectError(f"connection lost on write: {exc}") from exc

    async def receive_packet(self) -> EcPacket:
        """Reads EXACTLY one packet: 8-byte header, then ``length`` payload bytes.

        After an ``EcTimeoutError`` the stream may be desynchronized — throw the
        transport away and open a new one (no re-read).
        """
        header = await self._read_exactly(_HEADER_SIZE)
        flags, length = decode_header(header)
        payload = await self._read_exactly(length)
        return decode_payload(flags, payload)

    async def close(self) -> None:
        """Closes the connection. Best-effort cleanup: an error on an already-dead socket
        is swallowed (a DELIBERATE deviation from the letter of DECISION 5: "signal" applies
        to operations, not to cleanup — a raw OSError here would mask the
        original error inside a finally block)."""
        self._writer.close()
        with contextlib.suppress(OSError):
            await self._writer.wait_closed()

    async def _read_exactly(self, count: int) -> bytes:
        try:
            return await asyncio.wait_for(self._reader.readexactly(count), self._timeout)
        except TimeoutError as exc:
            raise EcTimeoutError("EC read timed out") from exc
        except (asyncio.IncompleteReadError, OSError) as exc:
            raise EcConnectError(f"EC connection lost: {exc}") from exc


async def open_ec_transport(host: str, port: int, *, timeout: float) -> EcTransport:
    """Establishes the TCP connection to ``host:port`` (ref. §0: default EC port 4712)."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except TimeoutError as exc:
        raise EcTimeoutError(f"connection to {host}:{port} timed out") from exc
    except OSError as exc:
        raise EcConnectError(f"cannot connect to {host}:{port}: {exc}") from exc
    return EcTransport(reader, writer, timeout=timeout)
