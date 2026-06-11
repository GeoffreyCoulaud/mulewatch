"""Client EC haut niveau : auth, recherche, statut (cf. spec EC-adapter §4-§6).

Implémente STRUCTURELLEMENT le port ``MuleClient`` (sans l'importer — même typage
structurel que les matchers vis-à-vis du Protocol ``Matcher``). AUCUN sleep, retry ou
reconnexion ici : l'adapter signale, l'appelant décide (spec §3/§6). Une requête à la
fois, réponses corrélées par ORDRE (FCFS strict, réf. §9 piège 14).
"""

import hashlib

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    hash16_tag,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcFailureError,
    EcProtocolError,
)
from emule_indexer.adapters.mule_ec.mapping import map_search_results
from emule_indexer.adapters.mule_ec.transport import EcTransport, open_ec_transport
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel

_CLIENT_NAME = "emule-indexer"
_CLIENT_VERSION = "0.5.0"
_LOWID_THRESHOLD = 16777216  # HIGHEST_LOWID_ED2K_KAD (NetworkFunctions.h:123, réf. §6)
_MAX_PROGRESS_PERCENT = 100  # au-delà : 0xffff (locale) / 0xfffe (Kad fini), réf. §5

_CHANNEL_TO_SEARCH_TYPE = {
    SearchChannel.GLOBAL: codes.EC_SEARCH_GLOBAL,
    SearchChannel.KAD: codes.EC_SEARCH_KAD,
}


def salted_password_hash(password: str, salt: int) -> bytes:
    """Hash d'auth EC, formule EXACTE de la réf. §4 (RemoteConnect.cpp:252-253).

    ``md5( lower(md5_hex(password)) + md5_hex(format("%X", salt)) )`` → 16 octets bruts.
    Pièges 4/5 : le sel est une valeur LOGIQUE (lue à largeur variable) formatée en hex
    MAJUSCULE sans zéros de tête ; les deux md5-hex intermédiaires sont en minuscule.
    """
    salt_str = format(salt, "X")
    salt_hash = hashlib.md5(salt_str.encode("ascii")).hexdigest()
    passwd_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()
    return hashlib.md5((passwd_md5 + salt_hash).encode("ascii")).digest()


def _failure_message(reply: EcPacket) -> str:
    """Message d'un AUTH_FAIL/FAILED (EC_TAG_STRING), ou un libellé sûr s'il manque."""
    tag = reply.find(codes.EC_TAG_STRING)
    if tag is None:
        return "échec signalé par amuled (sans message)"
    return tag.string_value()


class AmuleEcClient:
    """Pilote un ``amuled`` via EC. Trois usages câblés : auth, recherche, statut (spec §3).

    ``skipped_entries_total`` accumule les entrées de résultats écartées par le mapper
    (futur brancheur de métrique, plan E — DÉCISION 6).
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
        """TCP + handshake d'auth (réf. §4). Échec → exception, SANS retry (spec §5)."""
        if not self._password:
            raise EcAuthError("mot de passe EC vide (refusé, miroir de RemoteConnect.cpp:117)")
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
        """Lance une recherche (réf. §5). Efface les résultats de la précédente (côté daemon)."""
        search_tag = uint_tag(
            codes.EC_TAG_SEARCH_TYPE,
            _CHANNEL_TO_SEARCH_TYPE[channel],
            (
                string_tag(codes.EC_TAG_SEARCH_NAME, keyword),
                string_tag(codes.EC_TAG_SEARCH_FILE_TYPE, ""),  # obligatoire, "" = tous types
            ),
        )
        await self._request(EcPacket(codes.EC_OP_SEARCH_START, (search_tag,)), codes.EC_OP_STRINGS)
        self._current_keyword = keyword  # provenance, posée APRÈS le succès

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        """Snapshot CUMULATIF des résultats accumulés par le daemon (réf. §5)."""
        reply = await self._request(
            EcPacket(codes.EC_OP_SEARCH_RESULTS), codes.EC_OP_SEARCH_RESULTS
        )
        observations, skipped = map_search_results(reply.tags, self._current_keyword)
        self.skipped_entries_total += skipped
        return observations

    async def stop_search(self) -> None:
        await self._request(EcPacket(codes.EC_OP_SEARCH_STOP), codes.EC_OP_MISC_DATA)

    async def search_progress(self) -> int | None:
        """Pourcentage 0-100 si EC l'expose, sinon ``None`` (convention amulecmd, réf. §5)."""
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
        """État réseau (réf. §6) : EC_OP_GET_CONNSTATE au niveau de détail CMD."""
        request = EcPacket(
            codes.EC_OP_GET_CONNSTATE,
            (uint_tag(codes.EC_TAG_DETAIL_LEVEL, codes.EC_DETAIL_CMD),),
        )
        reply = await self._request(request, codes.EC_OP_MISC_DATA)
        connstate = reply.find(codes.EC_TAG_CONNSTATE)
        if connstate is None:
            raise EcProtocolError("réponse GET_CONNSTATE sans EC_TAG_CONNSTATE")
        return _parse_connstate(connstate)

    async def _authenticate(self, transport: EcTransport) -> None:
        auth_req = EcPacket(
            codes.EC_OP_AUTH_REQ,
            (
                string_tag(codes.EC_TAG_CLIENT_NAME, _CLIENT_NAME),
                string_tag(codes.EC_TAG_CLIENT_VERSION, _CLIENT_VERSION),
                # Émis en UINT16 (au plus court). AUCUN tag CAN_* (DÉCISION 2), AUCUN
                # EC_TAG_VERSION_ID (interdit face à une release, réf. §4).
                uint_tag(codes.EC_TAG_PROTOCOL_VERSION, codes.EC_CURRENT_PROTOCOL_VERSION),
            ),
        )
        await transport.send_packet(auth_req)
        salt_reply = await transport.receive_packet()
        if salt_reply.opcode == codes.EC_OP_AUTH_FAIL:
            raise EcAuthError(_failure_message(salt_reply))
        if salt_reply.opcode != codes.EC_OP_AUTH_SALT:
            raise EcProtocolError(f"opcode inattendu pendant l'auth : 0x{salt_reply.opcode:02X}")
        salt_tag = salt_reply.find(codes.EC_TAG_PASSWD_SALT)
        if salt_tag is None:
            raise EcProtocolError("EC_OP_AUTH_SALT sans EC_TAG_PASSWD_SALT")
        salt = salt_tag.int_value()  # largeur VARIABLE (réf. §9 piège 4)
        passwd_packet = EcPacket(
            codes.EC_OP_AUTH_PASSWD,
            (hash16_tag(codes.EC_TAG_PASSWD_HASH, salted_password_hash(self._password, salt)),),
        )
        await transport.send_packet(passwd_packet)
        verdict = await transport.receive_packet()
        if verdict.opcode == codes.EC_OP_AUTH_FAIL:
            raise EcAuthError(_failure_message(verdict))
        if verdict.opcode != codes.EC_OP_AUTH_OK:
            raise EcProtocolError(f"opcode inattendu pendant l'auth : 0x{verdict.opcode:02X}")

    def _require_transport(self) -> EcTransport:
        if self._transport is None:
            raise EcConnectError("client EC non connecté (appeler connect() d'abord)")
        return self._transport

    async def _request(self, packet: EcPacket, expected_opcode: int) -> EcPacket:
        """Une requête → une réponse (FCFS). FAILED → EcFailureError ; autre → EcProtocolError."""
        transport = self._require_transport()
        await transport.send_packet(packet)
        reply = await transport.receive_packet()
        if reply.opcode == codes.EC_OP_FAILED:
            raise EcFailureError(_failure_message(reply))
        if reply.opcode != expected_opcode:
            raise EcProtocolError(
                f"opcode inattendu : 0x{reply.opcode:02X} (attendu 0x{expected_opcode:02X})"
            )
        return reply


def _parse_connstate(connstate: EcTag) -> NetworkStatus:
    """Décode le bitfield + sous-tags d'EC_TAG_CONNSTATE (réf. §6)."""
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
