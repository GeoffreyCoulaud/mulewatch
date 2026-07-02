"""High-level EC client: auth, search, status (cf. EC-adapter spec §4-§6).

STRUCTURALLY implements the ``MuleClient`` port (without importing it — same structural
typing as the matchers against the ``Matcher`` Protocol). NO sleep, retry or
reconnection here: the adapter signals, the caller decides (spec §3/§6). One request at a
time, responses correlated by ORDER (strict FCFS, ref. §9 pitfall 14).
"""

import hashlib

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    empty_tag,
    hash16_tag,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcFailureError,
    EcProtocolError,
    EcTimeoutError,
)
from emule_indexer.adapters.mule_ec.mapping import map_search_results
from emule_indexer.adapters.mule_ec.transport import EcTransport, open_ec_transport
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
from emule_indexer.ports.mule_download_client import DownloadEntry, SharedFileEntry

_CLIENT_NAME = "emule-indexer"
_CLIENT_VERSION = "0.5.0"
_LOWID_THRESHOLD = 16777216  # HIGHEST_LOWID_ED2K_KAD (NetworkFunctions.h:123, ref. §6)
_MAX_PROGRESS_PERCENT = 100  # beyond that: 0xffff (local) / 0xfffe (Kad done), ref. §5

_CHANNEL_TO_SEARCH_TYPE = {
    SearchChannel.GLOBAL: codes.EC_SEARCH_GLOBAL,
    SearchChannel.KAD: codes.EC_SEARCH_KAD,
}


def salted_password_hash(password: str, salt: int) -> bytes:
    """EC auth hash, EXACT formula from ref. §4 (RemoteConnect.cpp:252-253).

    ``md5( lower(md5_hex(password)) + md5_hex(format("%X", salt)) )`` → 16 raw bytes.
    Pitfalls 4/5: the salt is a LOGICAL value (read at variable width) formatted as
    UPPERCASE hex without leading zeros; the two intermediate md5-hex are lowercase.
    """
    salt_str = format(salt, "X")
    salt_hash = hashlib.md5(salt_str.encode("ascii")).hexdigest()
    passwd_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()
    return hashlib.md5((passwd_md5 + salt_hash).encode("ascii")).digest()


def _failure_message(reply: EcPacket) -> str:
    """Message from an AUTH_FAIL/FAILED (EC_TAG_STRING), or a safe label if missing."""
    tag = reply.find(codes.EC_TAG_STRING)
    if tag is None:
        return "failure reported by amuled (no message)"
    return tag.string_value()


class AmuleEcClient:
    """Drives an ``amuled`` over EC. Three wired uses: auth, search, status (spec §3).

    ``skipped_entries_total`` accumulates result entries discarded by the mapper
    (future metric hook, plan E — DECISION 6). It is an EVENT counter: since the
    readout is cumulative, the same unusable entry re-seen on every readout counts
    each time — do not read it as "unique lost entries".
    """

    def __init__(self, host: str, port: int, password: str, *, timeout: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout
        self._transport: EcTransport | None = None
        self._current_keyword = ""
        self.skipped_entries_total = 0

    async def connect(self) -> None:
        """TCP + auth handshake (ref. §4). Failure → exception, NO retry (spec §5).

        IDEMPOTENT: a second call on an ALREADY-connected client is a no-op (no
        re-handshake, transport preserved). Essential to the pool (orchestration spec §3): the
        composition root connects at wiring time, then the worker re-calls ``connect()`` in
        its ``_ensure_connected()`` — this second call stays a safe no-op."""
        if self._transport is not None:
            return
        if not self._password:
            raise EcAuthError("empty EC password (refused, mirrors RemoteConnect.cpp:117)")
        transport = await open_ec_transport(self._host, self._port, timeout=self._timeout)
        try:
            await self._authenticate(transport)
        except Exception:
            await transport.close()
            raise
        self._transport = transport

    async def close(self) -> None:
        if self._transport is not None:
            await self._transport.close()
            self._transport = None

    async def start_search(self, keyword: str, channel: SearchChannel) -> None:
        """Starts a search (ref. §5). Clears the previous search's results (daemon-side)."""
        search_tag = uint_tag(
            codes.EC_TAG_SEARCH_TYPE,
            _CHANNEL_TO_SEARCH_TYPE[channel],
            (
                string_tag(codes.EC_TAG_SEARCH_NAME, keyword),
                string_tag(codes.EC_TAG_SEARCH_FILE_TYPE, ""),  # mandatory, "" = all types
            ),
        )
        await self._request(EcPacket(codes.EC_OP_SEARCH_START, (search_tag,)), codes.EC_OP_STRINGS)
        self._current_keyword = keyword  # provenance, set AFTER success

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        """CUMULATIVE snapshot of the results accumulated by the daemon (ref. §5).

        Called before any successful ``start_search``, the observations would carry
        ``keyword=""`` (and, against a real daemon, the results of any previous
        search); the caller is expected to start a search first.
        """
        reply = await self._request(
            EcPacket(codes.EC_OP_SEARCH_RESULTS), codes.EC_OP_SEARCH_RESULTS
        )
        observations, skipped = map_search_results(reply.tags, self._current_keyword)
        self.skipped_entries_total += skipped
        return observations

    async def fetch_results_raw(self) -> EcPacket:
        """RAW CUMULATIVE snapshot (the decoded ``EcPacket``, BEFORE mapping) — MEASUREMENT tool.

        Same EC request as ``fetch_results`` but WITHOUT the mapper: exposes ALL the result's
        tags (mapped, discarded, or unknown) to measure the empirical fill rate of each tag
        (``tools/ec_probe.py --all-tags``). Alters neither ``skipped_entries_total`` nor the
        client's state; never reads a file's bytes.
        """
        return await self._request(EcPacket(codes.EC_OP_SEARCH_RESULTS), codes.EC_OP_SEARCH_RESULTS)

    async def stop_search(self) -> None:
        await self._request(EcPacket(codes.EC_OP_SEARCH_STOP), codes.EC_OP_MISC_DATA)

    async def search_progress(self) -> int | None:
        """Percentage 0-100 if EC exposes it, else ``None`` (amulecmd convention, ref. §5)."""
        reply = await self._request(
            EcPacket(codes.EC_OP_SEARCH_PROGRESS), codes.EC_OP_SEARCH_PROGRESS
        )
        status = reply.find(codes.EC_TAG_SEARCH_STATUS)
        if status is None:
            return None
        value = status.int_value()
        if value > _MAX_PROGRESS_PERCENT:
            return None
        return value

    async def network_status(self) -> NetworkStatus:
        """Network status (ref. §6): EC_OP_GET_CONNSTATE at the CMD detail level."""
        request = EcPacket(
            codes.EC_OP_GET_CONNSTATE,
            (uint_tag(codes.EC_TAG_DETAIL_LEVEL, codes.EC_DETAIL_CMD),),
        )
        reply = await self._request(request, codes.EC_OP_MISC_DATA)
        connstate = reply.find(codes.EC_TAG_CONNSTATE)
        if connstate is None:
            raise EcProtocolError("GET_CONNSTATE reply without EC_TAG_CONNSTATE")
        return _parse_connstate(connstate)

    async def get_listen_port(self) -> int:
        """amuled's current eD2k TCP listen port (port-sync High-ID, design §2.3/§4.2).

        Emits ``EC_OP_GET_PREFERENCES`` + the selector ``EC_TAG_SELECT_PREFS=EC_PREFS_CONNECTIONS``.
        PITFALL R3 (settled): the REPLY carries opcode ``EC_OP_SET_PREFERENCES`` (0x40), NOT 0x3F
        (``CEC_Prefs_Packet`` reused as the reply, ECSpecialMuleTags.cpp:83) → ``expected=0x40``.
        Reads child ``EC_TAG_CONN_TCP_PORT`` under parent ``EC_TAG_PREFS_CONNECTIONS``; parent
        or child missing → ``EcProtocolError`` (non-conforming reply, caught by the loop as
        "EC unavailable" → backoff). NEVER reads a file byte.
        """
        request = EcPacket(
            codes.EC_OP_GET_PREFERENCES,
            (uint_tag(codes.EC_TAG_SELECT_PREFS, codes.EC_PREFS_CONNECTIONS),),
        )
        reply = await self._request(request, codes.EC_OP_SET_PREFERENCES)
        connections = reply.find(codes.EC_TAG_PREFS_CONNECTIONS)
        if connections is None:
            raise EcProtocolError("GET_PREFERENCES reply without EC_TAG_PREFS_CONNECTIONS")
        tcp_port = connections.find(codes.EC_TAG_CONN_TCP_PORT)
        if tcp_port is None:
            raise EcProtocolError("EC_TAG_PREFS_CONNECTIONS without EC_TAG_CONN_TCP_PORT")
        return tcp_port.int_value()

    async def set_listen_port(self, port: int) -> None:
        """Updates amuled's TCP/UDP listen port IN MEMORY (port-sync, design §4.2).

        Emits ``EC_OP_SET_PREFERENCES`` carrying the parent ``EC_TAG_PREFS_CONNECTIONS`` with two
        children ``EC_TAG_CONN_TCP_PORT``/``EC_TAG_CONN_UDP_PORT`` = ``port``. Success is signalled
        by ``EC_OP_NOOP`` (handler ExternalConn.cpp:2096). amuled persists the pref (``Save()``
        called as soon as ``Apply()`` runs); a container restart re-binds it. Does NOT re-bind hot.
        """
        request = EcPacket(
            codes.EC_OP_SET_PREFERENCES,
            (
                empty_tag(
                    codes.EC_TAG_PREFS_CONNECTIONS,
                    (
                        uint_tag(codes.EC_TAG_CONN_TCP_PORT, port),
                        uint_tag(codes.EC_TAG_CONN_UDP_PORT, port),
                    ),
                ),
            ),
        )
        await self._request(request, codes.EC_OP_NOOP)

    async def add_link(self, ed2k_link: str) -> None:
        """Adds an ed2k link to amuled's download queue (ref. EC, DECISION D1).

        Emits ``EC_OP_ADD_LINK`` with an ``EC_TAG_STRING`` carrying the link; success is
        signalled by ``EC_OP_NOOP`` (and NOT ``EC_OP_STRINGS`` — verified on ExternalConn.cpp).
        An application failure (``EC_OP_FAILED``) raises ``EcFailureError`` via ``_request``; a
        dead stream raises ``EcConnectError``/``EcTimeoutError`` (under ``MuleUnreachableError``).
        """
        await self._request(
            EcPacket(codes.EC_OP_ADD_LINK, (string_tag(codes.EC_TAG_STRING, ed2k_link),)),
            codes.EC_OP_NOOP,
        )

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        """Snapshot of the download queue (ref. EC, DECISION D1/D2). NEVER reads the bytes.

        Emits ``EC_OP_GET_DLOAD_QUEUE`` at CMD detail; the reply ``EC_OP_DLOAD_QUEUE``
        contains N children ``EC_TAG_PARTFILE`` whose sub-tags carry the hash (dedicated
        child ``EC_TAG_PARTFILE_HASH``, HASH16) and name/size_full/size_done/status. An entry
        without a usable hash is DISCARDED (tolerance to unknowns, like ``map_search_results``
        — never fatal).
        """
        request = EcPacket(
            codes.EC_OP_GET_DLOAD_QUEUE,
            (uint_tag(codes.EC_TAG_DETAIL_LEVEL, codes.EC_DETAIL_CMD),),
        )
        reply = await self._request(request, codes.EC_OP_DLOAD_QUEUE)
        entries: list[DownloadEntry] = []
        for tag in reply.tags:
            if tag.name != codes.EC_TAG_PARTFILE:
                continue
            entry = _map_partfile(tag)
            if entry is not None:
                entries.append(entry)
        return tuple(entries)

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        """Snapshot of amuled's SHARED files (ref. EC). NEVER reads the bytes.

        Emits ``EC_OP_GET_SHARED_FILES`` at CMD detail; the reply ``EC_OP_SHARED_FILES`` carries
        N children ``EC_TAG_KNOWNFILE`` (hash + true on-disk name). An entry without a usable
        hash/name is DISCARDED (tolerance to unknowns, like ``download_queue``).
        """
        request = EcPacket(
            codes.EC_OP_GET_SHARED_FILES,
            (uint_tag(codes.EC_TAG_DETAIL_LEVEL, codes.EC_DETAIL_CMD),),
        )
        reply = await self._request(request, codes.EC_OP_SHARED_FILES)
        entries: list[SharedFileEntry] = []
        for tag in reply.tags:
            if tag.name != codes.EC_TAG_KNOWNFILE:
                continue
            entry = _map_shared_file(tag)
            if entry is not None:
                entries.append(entry)
        return tuple(entries)

    async def _authenticate(self, transport: EcTransport) -> None:
        auth_req = EcPacket(
            codes.EC_OP_AUTH_REQ,
            (
                string_tag(codes.EC_TAG_CLIENT_NAME, _CLIENT_NAME),
                string_tag(codes.EC_TAG_CLIENT_VERSION, _CLIENT_VERSION),
                # Emitted as UINT16 (shortest form). NO CAN_* tag (DECISION 2), NO
                # EC_TAG_VERSION_ID (forbidden against a release, ref. §4).
                uint_tag(codes.EC_TAG_PROTOCOL_VERSION, codes.EC_CURRENT_PROTOCOL_VERSION),
            ),
        )
        await transport.send_packet(auth_req)
        salt_reply = await transport.receive_packet()
        if salt_reply.opcode == codes.EC_OP_AUTH_FAIL:
            raise EcAuthError(_failure_message(salt_reply))
        if salt_reply.opcode != codes.EC_OP_AUTH_SALT:
            raise EcProtocolError(f"unexpected opcode during auth: 0x{salt_reply.opcode:02X}")
        salt_tag = salt_reply.find(codes.EC_TAG_PASSWD_SALT)
        if salt_tag is None:
            raise EcProtocolError("EC_OP_AUTH_SALT without EC_TAG_PASSWD_SALT")
        salt = salt_tag.int_value()  # VARIABLE width (ref. §9 pitfall 4)
        passwd_packet = EcPacket(
            codes.EC_OP_AUTH_PASSWD,
            (hash16_tag(codes.EC_TAG_PASSWD_HASH, salted_password_hash(self._password, salt)),),
        )
        await transport.send_packet(passwd_packet)
        verdict = await transport.receive_packet()
        if verdict.opcode == codes.EC_OP_AUTH_FAIL:
            raise EcAuthError(_failure_message(verdict))
        if verdict.opcode != codes.EC_OP_AUTH_OK:
            raise EcProtocolError(f"unexpected opcode during auth: 0x{verdict.opcode:02X}")

    def _require_transport(self) -> EcTransport:
        if self._transport is None:
            raise EcConnectError("EC client not connected (call connect() first)")
        return self._transport

    async def _request(self, packet: EcPacket, expected_opcode: int) -> EcPacket:
        """One request → one reply (FCFS). FAILED → EcFailureError; other → EcProtocolError.

        On ``EcTimeoutError``/``EcConnectError``, the stream may be desynchronized
        (transport contract): the transport is DISCARDED (best-effort close) before
        re-signalling — the NEXT call fails fast and cleanly with "not connected".

        ``EcProtocolError`` raised BY ``transport.receive_packet()`` (unreadable header or
        payload, before a complete frame has been consumed) also desynchronizes the
        stream: the 8 header bytes were read, the payload was not. The same policy
        applies: transport DISCARDED, re-raise. Conversely, an ``EcProtocolError`` raised by
        the opcode/tag checks BELOW (complete frame already received) does not
        desynchronize the stream — only the try/except block covers the difference.
        """
        transport = self._require_transport()
        try:
            await transport.send_packet(packet)
            reply = await transport.receive_packet()
        except (EcTimeoutError, EcConnectError, EcProtocolError):
            await self.close()
            raise
        if reply.opcode == codes.EC_OP_FAILED:
            raise EcFailureError(_failure_message(reply))
        if reply.opcode != expected_opcode:
            raise EcProtocolError(
                f"unexpected opcode: 0x{reply.opcode:02X} (expected 0x{expected_opcode:02X})"
            )
        return reply


def _parse_connstate(connstate: EcTag) -> NetworkStatus:
    """Decodes the bitfield + sub-tags of EC_TAG_CONNSTATE (ref. §6)."""
    bits = connstate.int_value()
    if not bits & codes.CONNSTATE_KAD_RUNNING:
        kad = KadStatus.OFF
    elif not bits & codes.CONNSTATE_CONNECTED_KAD:
        kad = KadStatus.RUNNING
    elif bits & codes.CONNSTATE_KAD_FIREWALLED:
        kad = KadStatus.FIREWALLED
    else:
        kad = KadStatus.CONNECTED
    ed2k_id: int | None = None
    server_name: str | None = None
    server_addr: str | None = None
    if bits & codes.CONNSTATE_CONNECTED_ED2K:
        id_tag = connstate.find(codes.EC_TAG_ED2K_ID)
        if id_tag is not None:
            ed2k_id = id_tag.int_value()
        server = connstate.find(codes.EC_TAG_SERVER)
        if server is not None:
            server_addr = server.ipv4_value()
            name_tag = server.find(codes.EC_TAG_SERVER_NAME)
            if name_tag is not None:
                server_name = name_tag.string_value()
    ed2k_high = ed2k_id is not None and ed2k_id >= _LOWID_THRESHOLD
    return NetworkStatus(
        ed2k_id=ed2k_id,
        ed2k_high=ed2k_high,
        kad_status=kad,
        server_name=server_name,
        server_addr=server_addr,
    )


def _optional_partfile_int(entry: EcTag, name: int) -> int:
    """Optional integer of a partfile entry: absent or malformed → 0 (ref. EC §3)."""
    tag = entry.find(name)
    if tag is None:
        return 0
    try:
        return tag.int_value()
    except EcProtocolError:
        return 0


def _map_partfile(entry: EcTag) -> DownloadEntry | None:
    """An ``EC_TAG_PARTFILE`` entry → ``DownloadEntry``, or ``None`` if the hash is unusable.

    The hash is the dedicated child ``EC_TAG_PARTFILE_HASH`` (0x031E, HASH16, 16 bytes) — verified
    against a REAL amuled: the OWN value of ``EC_TAG_PARTFILE`` is a UINT8 (internal
    index/status, IGNORED here), NOT the hash. The sizes are children too. A hash child
    that is absent / of a type other than HASH16 / ≠ 16 bytes discards the entry (the hash is the
    ONLY stable identifier — without it, the entry is unusable, never persisted).
    """
    hash_tag = entry.find(codes.EC_TAG_PARTFILE_HASH)
    if (
        hash_tag is None
        or hash_tag.tag_type != codes.EC_TAGTYPE_HASH16
        or len(hash_tag.value) != 16
    ):
        return None
    return DownloadEntry(
        ed2k_hash=hash_tag.value.hex(),
        size_done=_optional_partfile_int(entry, codes.EC_TAG_PARTFILE_SIZE_DONE),
        size_full=_optional_partfile_int(entry, codes.EC_TAG_PARTFILE_SIZE_FULL),
    )


def _map_shared_file(entry: EcTag) -> SharedFileEntry | None:
    """An ``EC_TAG_KNOWNFILE`` entry → ``SharedFileEntry``, or ``None`` if unusable.

    Hash = dedicated child ``EC_TAG_PARTFILE_HASH`` (HASH16, 16 bytes); name = child
    ``EC_TAG_PARTFILE_NAME`` (TRUE on-disk name, ``GetFileName`` amuled-side, post-cleanup/dedup).
    No usable hash OR no name → discarded (tolerance to unknowns, like ``_map_partfile``).
    """
    hash_tag = entry.find(codes.EC_TAG_PARTFILE_HASH)
    if (
        hash_tag is None
        or hash_tag.tag_type != codes.EC_TAGTYPE_HASH16
        or len(hash_tag.value) != 16
    ):
        return None
    name_tag = entry.find(codes.EC_TAG_PARTFILE_NAME)
    if name_tag is None:
        return None
    try:
        name = name_tag.string_value()
    except EcProtocolError:
        return None
    return SharedFileEntry(ed2k_hash=hash_tag.value.hex(), name=name)
