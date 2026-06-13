import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.client import AmuleEcClient, salted_password_hash
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    encode_packet,
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
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
from tests.adapters.mule_ec.ec_fakes import FakeEcServer

_PASSWORD = "secret123"


# ---------------------------------------------------------------- formule d'auth (pure)


def test_salted_password_hash_matches_the_reference_formula() -> None:
    # Réf. §4 : hash = md5( lower(md5_hex(pwd)) + md5_hex(format("%X", salt)) ), 16 octets bruts.
    # Vecteurs précalculés : md5("secret123")=5d7845ac6ee7cfffafc5fe5f35cf666d ;
    #   salt 0x6B5E8D3A12F0C4D7 → "6B5E8D3A12F0C4D7" → md5=cd35cbb9bcdce6dc1510a4ff66e2be9a.
    assert salted_password_hash(_PASSWORD, 0x6B5E8D3A12F0C4D7) == bytes.fromhex(
        "1fd30b937affac0994f651b1b4f3aaf4"
    )
    # Sel ÉTROIT (piège 4) : 0xAB → "AB" (majuscules, sans zéros de tête).
    assert salted_password_hash(_PASSWORD, 0xAB) == bytes.fromhex(
        "36f8e4902449fcaa91e76f7dc1d87e9e"
    )
    # Sel zéro : "%lX" de 0 → "0" (réf. §4).
    assert salted_password_hash(_PASSWORD, 0) == bytes.fromhex("29e6c939d92ec99adbe3a50970506102")


# ---------------------------------------------------------------- handshake


def _auth_replies(salt: int) -> list[bytes]:
    return [
        encode_packet(EcPacket(codes.EC_OP_AUTH_SALT, (uint_tag(codes.EC_TAG_PASSWD_SALT, salt),))),
        encode_packet(
            EcPacket(codes.EC_OP_AUTH_OK, (string_tag(codes.EC_TAG_SERVER_VERSION, "3.0.0"),))
        ),
    ]


@pytest.mark.asyncio
async def test_connect_performs_the_full_auth_handshake() -> None:
    async with FakeEcServer(_auth_replies(0x6B5E8D3A12F0C4D7)) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        await client.close()
        auth_req, auth_passwd = server.received
        # AUTH_REQ : nom + version + version de protocole, AUCUN tag CAN_* (DÉCISION 2).
        assert auth_req.opcode == codes.EC_OP_AUTH_REQ
        assert auth_req.find(codes.EC_TAG_CLIENT_NAME) is not None
        assert auth_req.find(codes.EC_TAG_CLIENT_VERSION) is not None
        protocol = auth_req.find(codes.EC_TAG_PROTOCOL_VERSION)
        assert protocol is not None
        assert protocol.int_value() == 0x0204
        assert auth_req.find(codes.EC_TAG_CAN_ZLIB) is None
        assert auth_req.find(codes.EC_TAG_CAN_UTF8_NUMBERS) is None
        # AUTH_PASSWD : le hash salé EXACT, en HASH16.
        assert auth_passwd.opcode == codes.EC_OP_AUTH_PASSWD
        passwd_hash = auth_passwd.find(codes.EC_TAG_PASSWD_HASH)
        assert passwd_hash is not None
        assert passwd_hash.tag_type == codes.EC_TAGTYPE_HASH16
        assert passwd_hash.value == bytes.fromhex("1fd30b937affac0994f651b1b4f3aaf4")


@pytest.mark.asyncio
async def test_connect_reads_a_narrow_salt_generically() -> None:
    # Réf. §9 piège 4 : le sel arrive UINT8 quand il est petit ; lecture générique exigée.
    async with FakeEcServer(_auth_replies(0xAB)) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        await client.close()
        passwd_hash = server.received[1].find(codes.EC_TAG_PASSWD_HASH)
        assert passwd_hash is not None
        assert passwd_hash.value == bytes.fromhex("36f8e4902449fcaa91e76f7dc1d87e9e")


@pytest.mark.asyncio
async def test_connect_raises_auth_error_with_daemon_message_on_auth_fail() -> None:
    replies = [
        encode_packet(EcPacket(codes.EC_OP_AUTH_SALT, (uint_tag(codes.EC_TAG_PASSWD_SALT, 1),))),
        encode_packet(
            EcPacket(
                codes.EC_OP_AUTH_FAIL,
                (string_tag(codes.EC_TAG_STRING, "Authentication failed."),),
            )
        ),
    ]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, "mauvais", timeout=2.0)
        with pytest.raises(EcAuthError, match="Authentication failed."):
            await client.connect()
        # Après l'échec, le client n'est PAS connecté.
        with pytest.raises(EcConnectError):
            await client.stop_search()


@pytest.mark.asyncio
async def test_connect_raises_auth_error_when_first_reply_is_auth_fail() -> None:
    # Réf. §4 : une version de protocole refusée répond AUTH_FAIL dès la 1re réponse.
    replies = [
        encode_packet(
            EcPacket(
                codes.EC_OP_AUTH_FAIL,
                (string_tag(codes.EC_TAG_STRING, "Invalid protocol version."),),
            )
        )
    ]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcAuthError, match="Invalid protocol version."):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_raises_auth_error_without_message_tag() -> None:
    replies = [encode_packet(EcPacket(codes.EC_OP_AUTH_FAIL))]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcAuthError, match="sans message"):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_raises_protocol_error_on_unexpected_opcode_at_salt_step() -> None:
    replies = [encode_packet(EcPacket(codes.EC_OP_NOOP))]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcProtocolError):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_raises_protocol_error_when_salt_tag_is_missing() -> None:
    replies = [encode_packet(EcPacket(codes.EC_OP_AUTH_SALT))]  # AUTH_SALT sans tag de sel
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcProtocolError, match="PASSWD_SALT"):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_raises_protocol_error_on_unexpected_verdict_opcode() -> None:
    replies = [
        encode_packet(EcPacket(codes.EC_OP_AUTH_SALT, (uint_tag(codes.EC_TAG_PASSWD_SALT, 1),))),
        encode_packet(EcPacket(codes.EC_OP_NOOP)),
    ]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcProtocolError):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_refuses_empty_password_before_any_io() -> None:
    # Miroir de RemoteConnect.cpp:117 (réf. §4) — aucun serveur nécessaire.
    client = AmuleEcClient("127.0.0.1", 1, "", timeout=2.0)
    with pytest.raises(EcAuthError, match="vide"):
        await client.connect()


@pytest.mark.asyncio
async def test_connect_twice_is_idempotent_and_keeps_the_first_connection_usable() -> None:
    # Un second connect() est un NO-OP IDEMPOTENT : il ne refait PAS le handshake (aucun
    # octet supplémentaire émis) et ne fuit pas le premier transport. Indispensable au pool :
    # le composition root connecte au montage, puis le travailleur rappelle connect() dans son
    # _ensure_connected() — ce second appel doit rester un no-op sûr (spec orchestration §3).
    stop_ok = encode_packet(EcPacket(codes.EC_OP_MISC_DATA))
    # Le serveur ne scripte QU'UN seul handshake (+ la réponse au stop_search). Si connect()
    # rejouait l'auth, il consommerait des octets non scriptés et lèverait → le no-op est prouvé.
    async with FakeEcServer(_auth_replies(1) + [stop_ok]) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        await client.connect()  # second appel : no-op idempotent (pas de re-handshake)
        await client.stop_search()  # la première connexion fonctionne toujours
        await client.close()


@pytest.mark.asyncio
async def test_connect_rehandshakes_after_the_transport_is_invalidated() -> None:
    # Reconnexion-après-coupure (spec orchestration §3) : l'idempotence de connect() est
    # indexée sur le transport (None → re-handshake ; non-None → no-op). Quand une panne
    # invalide le transport (_transport=None, comme dans les tests _invalidates_the_transport),
    # le travailleur RAPPELLE connect() — qui DOIT refaire le handshake, sinon la reconnexion
    # est silencieusement cassée. On le prouve par byte-accounting : un 2e handshake est scripté
    # ET CONSOMMÉ (deux paires auth dans server.received), et l'op post-reconnexion réussit.
    bad_flags_frame = (0xFF000000).to_bytes(4, "big") + (3).to_bytes(4, "big") + b"\x01\x00\x00"
    stop_ok = encode_packet(EcPacket(codes.EC_OP_MISC_DATA))
    # Connexion 1 : auth(1) puis une trame illisible (invalide le transport sur stop_search).
    # Connexion 2 : auth(2) — DOIT être consommée par le re-handshake — puis stop_ok.
    replies = _auth_replies(1) + [bad_flags_frame] + _auth_replies(2) + [stop_ok]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        with pytest.raises(EcProtocolError):
            await client.stop_search()  # trame illisible → transport JETÉ
        assert client._transport is None  # invalidé (mirroir des tests sibling)
        # connect() voit _transport=None → REFAIT le handshake (ne no-op PAS), sur une 2e
        # connexion TCP : le 2e auth scripté est consommé.
        await client.connect()
        assert client._transport is not None  # reconnecté
        await client.stop_search()  # l'op post-reconnexion réussit (stop_ok)
        await client.close()
    # Byte-accounting : 2 AUTH_REQ + 2 AUTH_PASSWD (deux handshakes) ont bien été reçus, preuve
    # que le 2e connect() a réellement rejoué l'auth (et n'a pas no-opé sur un transport None).
    auth_reqs = [packet for packet in server.received if packet.opcode == codes.EC_OP_AUTH_REQ]
    assert len(auth_reqs) == 2


@pytest.mark.asyncio
async def test_close_is_a_noop_when_never_connected_and_idempotent() -> None:
    client = AmuleEcClient("127.0.0.1", 1, _PASSWORD, timeout=2.0)
    await client.close()  # jamais connecté : no-op
    async with FakeEcServer(_auth_replies(7)) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        await client.close()
        await client.close()  # idempotent


# ---------------------------------------------------------------- cycle de recherche


def _search_ok_reply() -> bytes:
    return encode_packet(
        EcPacket(
            codes.EC_OP_STRINGS,
            (string_tag(codes.EC_TAG_STRING, "Search in progress. Refetch results in a moment!"),),
        )
    )


def _results_reply(entries: tuple[EcTag, ...]) -> bytes:
    return encode_packet(EcPacket(codes.EC_OP_SEARCH_RESULTS, entries))


def _result_entry(name: str, with_hash: bool) -> EcTag:
    children: list[EcTag] = [
        string_tag(codes.EC_TAG_PARTFILE_NAME, name),
        uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1000),
        uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT, 3),
    ]
    if with_hash:
        children.append(hash16_tag(codes.EC_TAG_PARTFILE_HASH, bytes(range(16))))
    return EcTag(codes.EC_TAG_SEARCHFILE, codes.EC_TAGTYPE_UINT8, b"\x01", tuple(children))


async def _connected(server: FakeEcServer) -> AmuleEcClient:
    client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
    await client.connect()
    return client


@pytest.mark.asyncio
async def test_start_search_sends_the_documented_tree_per_channel() -> None:
    replies = _auth_replies(1) + [_search_ok_reply(), _search_ok_reply()]
    async with FakeEcServer(replies) as server:
        client = await _connected(server)
        await client.start_search("keroro", SearchChannel.GLOBAL)
        await client.start_search("titar", SearchChannel.KAD)
        await client.close()
        global_req, kad_req = server.received[2], server.received[3]
        for request, search_type, keyword in (
            (global_req, codes.EC_SEARCH_GLOBAL, "keroro"),
            (kad_req, codes.EC_SEARCH_KAD, "titar"),
        ):
            assert request.opcode == codes.EC_OP_SEARCH_START
            search_tag = request.find(codes.EC_TAG_SEARCH_TYPE)
            assert search_tag is not None
            assert search_tag.int_value() == search_type  # valeur PROPRE = type (réf. §5)
            name = search_tag.find(codes.EC_TAG_SEARCH_NAME)
            assert name is not None
            assert name.string_value() == keyword
            file_type = search_tag.find(codes.EC_TAG_SEARCH_FILE_TYPE)
            assert file_type is not None
            assert file_type.string_value() == ""  # obligatoire, "" = tous (réf. §5)


@pytest.mark.asyncio
async def test_start_search_failure_raises_ec_failure_with_daemon_message() -> None:
    failed = encode_packet(
        EcPacket(codes.EC_OP_FAILED, (string_tag(codes.EC_TAG_STRING, "Kad is not running"),))
    )
    async with FakeEcServer(_auth_replies(1) + [failed]) as server:
        client = await _connected(server)
        with pytest.raises(EcFailureError, match="Kad is not running"):
            await client.start_search("keroro", SearchChannel.KAD)
        await client.close()


@pytest.mark.asyncio
async def test_fetch_results_maps_keyword_provenance_and_accumulates_skips() -> None:
    replies = _auth_replies(1) + [
        _search_ok_reply(),
        _results_reply((_result_entry("a.avi", True), _result_entry("sans-hash.avi", False))),
        _results_reply((_result_entry("sans-hash2.avi", False),)),
    ]
    async with FakeEcServer(replies) as server:
        client = await _connected(server)
        await client.start_search("keroro", SearchChannel.GLOBAL)
        first = await client.fetch_results()
        assert [observation.filename for observation in first] == ["a.avi"]
        assert first[0].keyword == "keroro"  # provenance posée par le client
        assert client.skipped_entries_total == 1
        second = await client.fetch_results()
        assert second == ()
        assert client.skipped_entries_total == 2  # compteur CUMULATIF (DÉCISION 6)
        await client.close()


@pytest.mark.asyncio
async def test_stop_search_expects_misc_data_reply() -> None:
    stop_ok = encode_packet(EcPacket(codes.EC_OP_MISC_DATA))
    async with FakeEcServer(_auth_replies(1) + [stop_ok]) as server:
        client = await _connected(server)
        await client.stop_search()  # réponse EC_OP_MISC_DATA (réf. §5) : pas d'exception
        assert server.received[2].opcode == codes.EC_OP_SEARCH_STOP
        await client.close()


@pytest.mark.asyncio
async def test_unexpected_reply_opcode_raises_protocol_error() -> None:
    noop_reply = encode_packet(EcPacket(codes.EC_OP_NOOP))
    async with FakeEcServer(_auth_replies(1) + [noop_reply]) as server:
        client = await _connected(server)
        with pytest.raises(EcProtocolError, match="attendu"):
            await client.stop_search()
        await client.close()


def _progress_reply(value: int) -> bytes:
    return encode_packet(
        EcPacket(codes.EC_OP_SEARCH_PROGRESS, (uint_tag(codes.EC_TAG_SEARCH_STATUS, value),))
    )


@pytest.mark.asyncio
async def test_search_progress_follows_the_amulecmd_convention() -> None:
    replies = _auth_replies(1) + [
        _progress_reply(42),  # globale : pourcentage
        _progress_reply(100),
        _progress_reply(0xFFFF),  # locale : pas de mesure → None (réf. §5)
        _progress_reply(0xFFFE),  # Kad fini → None
        encode_packet(EcPacket(codes.EC_OP_SEARCH_PROGRESS)),  # tag absent → None
    ]
    async with FakeEcServer(replies) as server:
        client = await _connected(server)
        assert await client.search_progress() == 42
        assert await client.search_progress() == 100
        assert await client.search_progress() is None
        assert await client.search_progress() is None
        assert await client.search_progress() is None
        await client.close()


@pytest.mark.asyncio
async def test_operations_without_connect_raise_connect_error() -> None:
    client = AmuleEcClient("127.0.0.1", 1, _PASSWORD, timeout=2.0)
    with pytest.raises(EcConnectError, match="non connecté"):
        await client.fetch_results()


@pytest.mark.asyncio
async def test_request_timeout_invalidates_the_transport() -> None:
    # Contrat du transport : après un timeout de lecture, le flux peut être désynchronisé
    # — le client doit JETER le transport, pas relire une trame périmée (FCFS cassé sinon).
    # Réponses épuisées après l'auth → le faux serveur se tait → timeout de lecture.
    async with FakeEcServer(_auth_replies(1)) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=0.2)
        await client.connect()
        with pytest.raises(EcTimeoutError):
            await client.stop_search()
        # L'appel SUIVANT échoue vite et proprement : le transport a été invalidé.
        with pytest.raises(EcConnectError, match="non connecté"):
            await client.stop_search()
        await client.close()  # toujours idempotent après invalidation


@pytest.mark.asyncio
async def test_protocol_error_from_receive_packet_invalidates_the_transport() -> None:
    # Réf. : flux potentiellement désynchronisé après une trame illisible (en-tête avec
    # flags inconnus → decode_header lève EcProtocolError AVANT d'avoir lu le payload).
    # Le client doit JETER le transport et signaler proprement — l'appel suivant doit
    # échouer avec EcConnectError("non connecté"), pas tenter de lire une trame périmée.
    bad_flags_frame = (0xFF000000).to_bytes(4, "big") + (3).to_bytes(4, "big") + b"\x01\x00\x00"
    async with FakeEcServer(_auth_replies(1) + [bad_flags_frame]) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        with pytest.raises(EcProtocolError):
            await client.stop_search()
        # Transport invalidé : l'appel suivant ne tente PAS de relire le flux désynchronisé.
        with pytest.raises(EcConnectError, match="non connecté"):
            await client.stop_search()
        await client.close()


# ---------------------------------------------------------------- statut réseau


def _connstate_reply(bits: int, children: tuple[EcTag, ...] = ()) -> bytes:
    return encode_packet(
        EcPacket(codes.EC_OP_MISC_DATA, (uint_tag(codes.EC_TAG_CONNSTATE, bits, children),))
    )


def _server_tag(with_name: bool) -> EcTag:
    children = (string_tag(codes.EC_TAG_SERVER_NAME, "TestServer"),) if with_name else ()
    return EcTag(
        codes.EC_TAG_SERVER,
        codes.EC_TAGTYPE_IPV4,
        bytes([1, 2, 3, 4]) + (4661).to_bytes(2, "big"),
        children,
    )


async def _status_for(bits: int, children: tuple[EcTag, ...] = ()) -> NetworkStatus:
    async with FakeEcServer(_auth_replies(1) + [_connstate_reply(bits, children)]) as server:
        client = await _connected(server)
        status = await client.network_status()
        # Le client a bien demandé le niveau de détail CMD (réf. §6).
        request = server.received[2]
        assert request.opcode == codes.EC_OP_GET_CONNSTATE
        detail = request.find(codes.EC_TAG_DETAIL_LEVEL)
        assert detail is not None
        assert detail.int_value() == codes.EC_DETAIL_CMD
        await client.close()
        return status


@pytest.mark.asyncio
async def test_network_status_connected_high_id_with_server() -> None:
    # bits 0x15 = eD2k connecté | Kad connecté | Kad lancé ; ID 0x02000001 ≥ 16777216 → High.
    status = await _status_for(
        0x15,
        (
            _server_tag(with_name=True),
            uint_tag(codes.EC_TAG_ED2K_ID, 0x02000001),
            uint_tag(codes.EC_TAG_CLIENT_ID, 0x02000001),
        ),
    )
    assert status.ed2k_id == 0x02000001
    assert status.ed2k_high is True
    assert status.kad_status is KadStatus.CONNECTED
    assert status.server_name == "TestServer"
    assert status.server_addr == "1.2.3.4:4661"


@pytest.mark.asyncio
async def test_network_status_low_id() -> None:
    # LowID si < 16777216 (HIGHEST_LOWID_ED2K_KAD, réf. §6).
    status = await _status_for(
        0x01, (_server_tag(with_name=True), uint_tag(codes.EC_TAG_ED2K_ID, 100))
    )
    assert status.ed2k_id == 100
    assert status.ed2k_high is False
    assert status.kad_status is KadStatus.OFF  # ni 0x10 → Kad arrêté


@pytest.mark.asyncio
async def test_network_status_kad_running_not_connected_and_no_ed2k() -> None:
    status = await _status_for(0x10)
    assert status.ed2k_id is None
    assert status.ed2k_high is False
    assert status.kad_status is KadStatus.RUNNING
    assert status.server_name is None
    assert status.server_addr is None


@pytest.mark.asyncio
async def test_network_status_kad_firewalled() -> None:
    # 0x10|0x04|0x08 = connecté mais firewalled (réf. §6).
    status = await _status_for(0x1C)
    assert status.kad_status is KadStatus.FIREWALLED


@pytest.mark.asyncio
async def test_network_status_tolerates_connected_ed2k_without_id_or_server_tags() -> None:
    # Défensif : bit eD2k posé mais sous-tags absents → None partout, pas d'exception.
    status = await _status_for(0x01)
    assert status.ed2k_id is None
    assert status.ed2k_high is False
    assert status.server_addr is None


@pytest.mark.asyncio
async def test_network_status_server_without_name_child() -> None:
    status = await _status_for(
        0x01, (_server_tag(with_name=False), uint_tag(codes.EC_TAG_ED2K_ID, 100))
    )
    assert status.server_addr == "1.2.3.4:4661"
    assert status.server_name is None


@pytest.mark.asyncio
async def test_network_status_without_connstate_tag_raises_protocol_error() -> None:
    empty_reply = encode_packet(EcPacket(codes.EC_OP_MISC_DATA))
    async with FakeEcServer(_auth_replies(1) + [empty_reply]) as server:
        client = await _connected(server)
        with pytest.raises(EcProtocolError, match="CONNSTATE"):
            await client.network_status()
        await client.close()
