import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcError,
    EcFailureError,
    EcProtocolError,
    EcTimeoutError,
)


def test_protocol_version_and_flags_match_reference() -> None:
    # docs/reference/ec-protocol.md §7 (source: ECCodes.h 2.3.3).
    assert codes.EC_CURRENT_PROTOCOL_VERSION == 0x0204
    assert codes.EC_FLAG_BASE == 0x20
    assert codes.EC_FLAG_ZLIB == 0x00000001
    assert codes.EC_FLAG_UTF8_NUMBERS == 0x00000002
    assert codes.EC_FLAG_UNKNOWN_MASK == 0xFF7F7F08


def test_auth_opcodes_and_tags_match_reference() -> None:
    assert codes.EC_OP_AUTH_REQ == 0x02
    assert codes.EC_OP_AUTH_FAIL == 0x03
    assert codes.EC_OP_AUTH_OK == 0x04
    assert codes.EC_OP_AUTH_SALT == 0x4F
    assert codes.EC_OP_AUTH_PASSWD == 0x50
    assert codes.EC_TAG_STRING == 0x0000  # carries the error message (AUTH_FAIL/FAILED)
    assert codes.EC_TAG_PASSWD_HASH == 0x0001
    assert codes.EC_TAG_PROTOCOL_VERSION == 0x0002
    assert codes.EC_TAG_PASSWD_SALT == 0x000B
    assert codes.EC_TAG_CLIENT_NAME == 0x0100
    assert codes.EC_TAG_CLIENT_VERSION == 0x0101
    assert codes.EC_TAG_SERVER_VERSION == 0x050B


def test_search_opcodes_and_tags_match_reference() -> None:
    assert codes.EC_OP_SEARCH_START == 0x26
    assert codes.EC_OP_SEARCH_STOP == 0x27
    assert codes.EC_OP_SEARCH_RESULTS == 0x28
    assert codes.EC_OP_SEARCH_PROGRESS == 0x29
    assert codes.EC_OP_STRINGS == 0x06
    assert codes.EC_OP_FAILED == 0x05
    assert codes.EC_OP_MISC_DATA == 0x07
    assert codes.EC_SEARCH_GLOBAL == 0x01
    assert codes.EC_SEARCH_KAD == 0x02
    assert codes.EC_TAG_SEARCHFILE == 0x0700
    assert codes.EC_TAG_SEARCH_TYPE == 0x0701
    assert codes.EC_TAG_SEARCH_NAME == 0x0702
    assert codes.EC_TAG_SEARCH_FILE_TYPE == 0x0705
    assert codes.EC_TAG_SEARCH_STATUS == 0x0708


def test_partfile_result_tags_match_reference() -> None:
    assert codes.EC_TAG_PARTFILE_NAME == 0x0301
    assert codes.EC_TAG_PARTFILE_SIZE_FULL == 0x0303
    assert codes.EC_TAG_PARTFILE_HASH == 0x031E
    assert codes.EC_TAG_PARTFILE_SOURCE_COUNT == 0x030A
    assert codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER == 0x030D
    assert codes.EC_TAG_PARTFILE_STATUS == 0x0308


def test_connstate_tags_and_bits_match_reference() -> None:
    assert codes.EC_OP_GET_CONNSTATE == 0x0B
    assert codes.EC_TAG_CONNSTATE == 0x0005
    assert codes.EC_TAG_ED2K_ID == 0x0006
    assert codes.EC_TAG_CLIENT_ID == 0x000A
    assert codes.EC_TAG_SERVER == 0x0500
    assert codes.EC_TAG_SERVER_NAME == 0x0501
    assert codes.EC_TAG_KAD_ID == 0x0010
    assert codes.EC_TAG_DETAIL_LEVEL == 0x0004
    assert codes.EC_DETAIL_CMD == 0x00
    assert codes.CONNSTATE_CONNECTED_ED2K == 0x01
    assert codes.CONNSTATE_CONNECTING_ED2K == 0x02
    assert codes.CONNSTATE_CONNECTED_KAD == 0x04
    assert codes.CONNSTATE_KAD_FIREWALLED == 0x08
    assert codes.CONNSTATE_KAD_RUNNING == 0x10


def test_preferences_opcodes_and_tags_match_reference() -> None:
    # Port-sync (High-ID): GET/SET preferences + connection selector/tags.
    # Ref.: vendor/amule/src/libs/ec/cpp/ECCodes.h (lines cited in design §2).
    assert codes.EC_OP_GET_PREFERENCES == 0x3F  # ECCodes.h:102
    assert codes.EC_OP_SET_PREFERENCES == 0x40  # ECCodes.h:103 (= opcode of the GET RESPONSE)
    assert codes.EC_TAG_SELECT_PREFS == 0x1000  # ECCodes.h:310
    assert codes.EC_TAG_PREFS_CONNECTIONS == 0x1300  # ECCodes.h:323 (parent)
    assert codes.EC_TAG_CONN_TCP_PORT == 0x1306  # ECCodes.h:329 (child)
    assert codes.EC_TAG_CONN_UDP_PORT == 0x1307  # ECCodes.h:330 (child)
    assert codes.EC_PREFS_CONNECTIONS == 0x00000004  # ECCodes.h:462 (SELECT_PREFS bitmask)


def test_tag_types_match_reference() -> None:
    # Ref. §3 (ECTagTypes.h).
    assert codes.EC_TAGTYPE_UNKNOWN == 0x00  # never emitted; codec guard
    assert codes.EC_TAGTYPE_CUSTOM == 0x01
    assert codes.EC_TAGTYPE_UINT8 == 0x02
    assert codes.EC_TAGTYPE_UINT16 == 0x03
    assert codes.EC_TAGTYPE_UINT32 == 0x04
    assert codes.EC_TAGTYPE_UINT64 == 0x05
    assert codes.EC_TAGTYPE_STRING == 0x06
    assert codes.EC_TAGTYPE_DOUBLE == 0x07
    assert codes.EC_TAGTYPE_IPV4 == 0x08
    assert codes.EC_TAGTYPE_HASH16 == 0x09
    assert codes.EC_TAGTYPE_UINT128 == 0x0A


def test_error_hierarchy_matches_spec_section_6() -> None:
    for subtype in (EcConnectError, EcAuthError, EcProtocolError, EcTimeoutError, EcFailureError):
        assert issubclass(subtype, EcError)
        assert issubclass(subtype, Exception)
    # EcFailureError (application failure) is DISTINCT from EcProtocolError (unreadable frame).
    assert not issubclass(EcFailureError, EcProtocolError)
    with pytest.raises(EcError):
        raise EcAuthError("Invalid password")
