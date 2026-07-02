"""In-memory fake EC server (asyncio streams) for the transport/client tests.

Replays PRE-ENCODED replies, one per received request (strict FCFS, ref. §9 pitfall 14).
``replies`` exhausted → the server GOES SILENT (useful for testing the read timeout).
``close_after=N`` → closes the connection after N requests read (0 = right at accept).
``abort=True`` → RST after the first request read (SO_LINGER(1,0) + close: this is the
only deterministic way to emit a RST when the receive buffer is already drained —
``transport.abort()`` would only send a FIN in that case).
"""

import asyncio
import contextlib
import socket
import struct
from collections.abc import Sequence
from types import TracebackType

from emule_indexer.adapters.mule_ec.codec import EcPacket, decode_header, decode_payload


class FakeEcServer:
    def __init__(
        self,
        replies: Sequence[bytes] = (),
        *,
        close_after: int | None = None,
        abort: bool = False,
    ) -> None:
        self.replies = list(replies)
        self.received: list[EcPacket] = []
        self.port = 0
        self._close_after = close_after
        self._abort = abort
        self._release = asyncio.Event()
        self._server: asyncio.Server | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            count = 0
            while self._close_after is None or count < self._close_after:
                header = await reader.readexactly(8)
                flags, length = decode_header(header)
                payload = await reader.readexactly(length)
                self.received.append(decode_payload(flags, payload))
                count += 1
                if self._abort:
                    sock = writer.get_extra_info("socket")
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    writer.close()  # RST → the client sees ConnectionResetError
                    return
                if not self.replies:
                    await self._release.wait()  # stay silent until teardown
                    break
                writer.write(self.replies.pop(0))
                await writer.drain()
        writer.close()

    async def __aenter__(self) -> "FakeEcServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = int(self._server.sockets[0].getsockname()[1])
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release.set()
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
