"""EC protocol constants, transcribed from docs/reference/ec-protocol.md §7.

Upstream source: ``src/libs/ec/cpp/ECCodes.h`` + ``ECTagTypes.h`` (aMule tag 2.3.3;
identical in 3.0.0 unless marked ✦). Tag names are the LOGICAL names: on the
wire we send ``(name << 1) | children`` (ref. §2) — the shift lives in codec.py.
"""

from typing import Final

# --- Protocol & flags (ref. §1, §7) -----------------------------------------------------
EC_CURRENT_PROTOCOL_VERSION: Final[int] = 0x0204
EC_FLAG_BASE: Final[int] = 0x20  # base bit ALWAYS present (m_my_flags(0x20), ECSocket.cpp:275)
EC_FLAG_ZLIB: Final[int] = 0x00000001
EC_FLAG_UTF8_NUMBERS: Final[int] = 0x00000002
EC_FLAG_LARGE_TAG_COUNT: Final[int] = 0x00000010  # ✦ 3.0.0; never emitted nor accepted (DECISION 2)
EC_FLAG_UNKNOWN_MASK: Final[int] = 0xFF7F7F08

# --- Opcodes (ref. §7) ------------------------------------------------------------------
EC_OP_NOOP: Final[int] = 0x01
EC_OP_ADD_LINK: Final[int] = 0x09  # adds an ed2k link; NOOP response
EC_OP_GET_DLOAD_QUEUE: Final[int] = 0x0D  # requests the download queue (CMD detail)
EC_OP_DLOAD_QUEUE: Final[int] = 0x1F  # response: N EC_TAG_PARTFILE children
EC_OP_GET_SHARED_FILES: Final[int] = 0x10  # requests the shared-files list (CMD detail)
EC_OP_SHARED_FILES: Final[int] = 0x22  # response: N EC_TAG_KNOWNFILE children
EC_OP_AUTH_REQ: Final[int] = 0x02
EC_OP_AUTH_FAIL: Final[int] = 0x03
EC_OP_AUTH_OK: Final[int] = 0x04
EC_OP_FAILED: Final[int] = 0x05
EC_OP_STRINGS: Final[int] = 0x06
EC_OP_MISC_DATA: Final[int] = 0x07
EC_OP_STAT_REQ: Final[int] = 0x0A
EC_OP_GET_CONNSTATE: Final[int] = 0x0B
EC_OP_STATS: Final[int] = 0x0C
EC_OP_SEARCH_START: Final[int] = 0x26
EC_OP_SEARCH_STOP: Final[int] = 0x27
EC_OP_SEARCH_RESULTS: Final[int] = 0x28
EC_OP_SEARCH_PROGRESS: Final[int] = 0x29
EC_OP_DOWNLOAD_SEARCH_RESULT: Final[int] = 0x2A
EC_OP_SERVER_DISCONNECT: Final[int] = 0x2E
EC_OP_SERVER_CONNECT: Final[int] = 0x2F
EC_OP_GET_PREFERENCES: Final[int] = 0x3F  # ECCodes.h:102; port-sync (reads the listen port)
EC_OP_SET_PREFERENCES: Final[int] = 0x40  # ECCodes.h:103; ALSO the opcode of the GET RESPONSE
EC_OP_KAD_START: Final[int] = 0x48
EC_OP_KAD_STOP: Final[int] = 0x49
EC_OP_AUTH_SALT: Final[int] = 0x4F
EC_OP_AUTH_PASSWD: Final[int] = 0x50

# --- Detail levels & search types (ref. §5, §7) -----------------------------------------
EC_DETAIL_CMD: Final[int] = 0x00
EC_DETAIL_WEB: Final[int] = 0x01
EC_DETAIL_FULL: Final[int] = 0x02
EC_DETAIL_UPDATE: Final[int] = 0x03
EC_DETAIL_INC_UPDATE: Final[int] = 0x04
EC_SEARCH_LOCAL: Final[int] = 0x00
EC_SEARCH_GLOBAL: Final[int] = 0x01
EC_SEARCH_KAD: Final[int] = 0x02
EC_SEARCH_WEB: Final[int] = 0x03  # refused by the server (ref. §5)

# --- Tags (logical names, ref. §7) ------------------------------------------------------
EC_TAG_STRING: Final[int] = 0x0000
EC_TAG_PASSWD_HASH: Final[int] = 0x0001
EC_TAG_PROTOCOL_VERSION: Final[int] = 0x0002
EC_TAG_VERSION_ID: Final[int] = 0x0003  # SVN builds only; FORBIDDEN against a release
EC_TAG_DETAIL_LEVEL: Final[int] = 0x0004
EC_TAG_CONNSTATE: Final[int] = 0x0005
EC_TAG_ED2K_ID: Final[int] = 0x0006
EC_TAG_CLIENT_ID: Final[int] = 0x000A
EC_TAG_PASSWD_SALT: Final[int] = 0x000B
EC_TAG_CAN_ZLIB: Final[int] = 0x000C
EC_TAG_CAN_UTF8_NUMBERS: Final[int] = 0x000D
EC_TAG_CAN_NOTIFY: Final[int] = 0x000E
EC_TAG_KAD_ID: Final[int] = 0x0010
EC_TAG_CAN_LARGE_TAG_COUNT: Final[int] = 0x0011  # ✦ 3.0.0
EC_TAG_CAN_PARTIAL_UPDATE: Final[int] = 0x0012  # ✦ 3.0.0
EC_TAG_CLIENT_NAME: Final[int] = 0x0100
EC_TAG_CLIENT_VERSION: Final[int] = 0x0101
EC_TAG_STATS_UL_SPEED: Final[int] = 0x0200
EC_TAG_STATS_DL_SPEED: Final[int] = 0x0201
EC_TAG_STATS_UL_SPEED_LIMIT: Final[int] = 0x0202
EC_TAG_STATS_DL_SPEED_LIMIT: Final[int] = 0x0203
EC_TAG_STATS_TOTAL_SRC_COUNT: Final[int] = 0x0206
EC_TAG_STATS_UL_QUEUE_LEN: Final[int] = 0x0208
EC_TAG_STATS_ED2K_USERS: Final[int] = 0x0209
EC_TAG_STATS_KAD_USERS: Final[int] = 0x020A
EC_TAG_STATS_ED2K_FILES: Final[int] = 0x020B
EC_TAG_STATS_KAD_FILES: Final[int] = 0x020C
EC_TAG_PARTFILE: Final[int] = 0x0300
EC_TAG_PARTFILE_NAME: Final[int] = 0x0301
EC_TAG_PARTFILE_SIZE_FULL: Final[int] = 0x0303
EC_TAG_PARTFILE_SIZE_DONE: Final[int] = 0x0306  # bytes transferred (complete = done >= full)
EC_TAG_PARTFILE_ED2K_LINK: Final[int] = 0x030E  # reconstructed link (not used here)
EC_TAG_PARTFILE_STATUS: Final[int] = 0x0308
EC_TAG_PARTFILE_SOURCE_COUNT: Final[int] = 0x030A
EC_TAG_PARTFILE_SOURCE_COUNT_XFER: Final[int] = 0x030D  # = COMPLETE sources (ref. §9 pitfall 12)
EC_TAG_PARTFILE_CAT: Final[int] = 0x030F
EC_TAG_PARTFILE_HASH: Final[int] = 0x031E
EC_TAG_KNOWNFILE: Final[int] = 0x0400  # container of a shared/known file (SHARED_FILES response)
EC_TAG_KNOWNFILE_RATING: Final[int] = 0x040F  # ✦ 3.0.0
EC_TAG_SERVER: Final[int] = 0x0500
EC_TAG_SERVER_NAME: Final[int] = 0x0501
EC_TAG_SERVER_VERSION: Final[int] = 0x050B
EC_TAG_SEARCHFILE: Final[int] = 0x0700
EC_TAG_SEARCH_TYPE: Final[int] = 0x0701
EC_TAG_SEARCH_NAME: Final[int] = 0x0702
EC_TAG_SEARCH_MIN_SIZE: Final[int] = 0x0703
EC_TAG_SEARCH_MAX_SIZE: Final[int] = 0x0704
EC_TAG_SEARCH_FILE_TYPE: Final[int] = 0x0705
EC_TAG_SEARCH_EXTENSION: Final[int] = 0x0706
EC_TAG_SEARCH_AVAILABILITY: Final[int] = 0x0707
EC_TAG_SEARCH_STATUS: Final[int] = 0x0708
EC_TAG_SEARCH_PARENT: Final[int] = 0x0709

# --- Preferences (port-sync High-ID, ref. ECCodes.h; port-sync design §2) ---------------
EC_TAG_SELECT_PREFS: Final[int] = 0x1000  # ECCodes.h:310 — selector of prefs categories
EC_TAG_PREFS_CONNECTIONS: Final[int] = 0x1300  # ECCodes.h:323 — parent (CECEmptyTag)
EC_TAG_CONN_TCP_PORT: Final[int] = 0x1306  # ECCodes.h:329 — eD2k TCP listen port (child)
EC_TAG_CONN_UDP_PORT: Final[int] = 0x1307  # ECCodes.h:330 — UDP port (child)
EC_PREFS_CONNECTIONS: Final[int] = 0x00000004  # ECCodes.h:462 — bitmask of EC_TAG_SELECT_PREFS

# --- Value types (ref. §3, ECTagTypes.h) ------------------------------------------------
EC_TAGTYPE_UNKNOWN: Final[int] = 0x00  # never emitted
EC_TAGTYPE_CUSTOM: Final[int] = 0x01  # opaque bytes; also the type of empty tags
EC_TAGTYPE_UINT8: Final[int] = 0x02
EC_TAGTYPE_UINT16: Final[int] = 0x03
EC_TAGTYPE_UINT32: Final[int] = 0x04
EC_TAGTYPE_UINT64: Final[int] = 0x05
EC_TAGTYPE_STRING: Final[int] = 0x06  # UTF-8 + trailing NUL INCLUDED in TAGLEN
EC_TAGTYPE_DOUBLE: Final[int] = 0x07  # text representation + NUL
EC_TAGTYPE_IPV4: Final[int] = 0x08  # 4 IP bytes + uint16 big-endian port
EC_TAGTYPE_HASH16: Final[int] = 0x09  # 16 raw bytes MSB first (MD4/MD5)
EC_TAGTYPE_UINT128: Final[int] = 0x0A  # 16 big-endian bytes (Kad ID)

# --- EC_TAG_CONNSTATE bitfield (ref. §6) ------------------------------------------------
CONNSTATE_CONNECTED_ED2K: Final[int] = 0x01
CONNSTATE_CONNECTING_ED2K: Final[int] = 0x02
CONNSTATE_CONNECTED_KAD: Final[int] = 0x04
CONNSTATE_KAD_FIREWALLED: Final[int] = 0x08
CONNSTATE_KAD_RUNNING: Final[int] = 0x10
