"""Maps the amuleapi JSON payloads onto the ports' DTOs (spec amuleapi §4.2, D4/D5).

Capture-all (D5): typed JSON does not preserve the invariant on its own, so every key of a
result object that no structured field consumes is rendered into ``raw_meta``, exactly as the
EC adapter did with its unmapped tags. Unknown-tolerance is the same too: an unknown key is
never an error, and only an entry with no usable hash/name/size is discarded and COUNTED.

The volatile identifiers stay out: ``alternate_names[].ecid`` is amuled's session-local id for
a result row, the REST heir of the ECID the EC mapper already refused to persist.
"""

import json
from typing import Any

from mulewatch.domain.observation import FileObservation
from mulewatch.ports.mule_client import KadStatus, NetworkStatus
from mulewatch.ports.mule_download_client import DownloadEntry, SharedFileEntry

_HASH_LENGTH = 32

# Result keys consumed by a structured field, hence EXCLUDED from raw_meta.
_MAPPED_KEYS = frozenset(
    {"hash", "name", "size_bytes", "sources", "media", "file_type", "alternate_names"}
)
_MAPPED_MEDIA_KEYS = frozenset({"duration_seconds", "bitrate_kilobits_per_second", "codec"})

# Kad states with an equivalent in the port's closed enum; anything else is OFF (§7.6).
_KAD_STATES = {"connected": KadStatus.CONNECTED, "connecting": KadStatus.RUNNING}


def map_search_results(results: object, keyword: str) -> tuple[tuple[FileObservation, ...], int]:
    """``results[]`` of a GET /search/{id}/results → ``(observations, skipped_count)``."""
    observations: list[FileObservation] = []
    skipped = 0
    if not isinstance(results, list):
        return (), 0  # unexpected envelope: tolerated, ignored (not a result set)
    for result in results:
        mapped, result_skipped = _map_result(result, keyword)
        observations.extend(mapped)
        skipped += result_skipped
    return tuple(observations), skipped


def map_download_entry(row: object) -> DownloadEntry | None:
    """A GET /downloads row → ``DownloadEntry``, or ``None`` if the hash is unusable.

    ``completed_bytes`` is what is on disk, which is what the disk-cap rule needs;
    ``transferred_bytes`` counts what came off the wire, corruption included.
    """
    if not isinstance(row, dict):
        return None
    ed2k_hash = _hash_hex(row.get("hash"))
    if ed2k_hash is None:
        return None
    return DownloadEntry(
        ed2k_hash=ed2k_hash,
        size_done=_int(row.get("completed_bytes")),
        size_full=_int(row.get("size_bytes")),
    )


def map_shared_entry(row: object) -> SharedFileEntry | None:
    """A GET /shared row → ``SharedFileEntry`` (hash only), or ``None`` if unusable."""
    if not isinstance(row, dict):
        return None
    ed2k_hash = _hash_hex(row.get("hash"))
    return None if ed2k_hash is None else SharedFileEntry(ed2k_hash=ed2k_hash)


def map_network_status(payload: object) -> NetworkStatus:
    """A GET /status body → ``NetworkStatus``. Never raises: a missing field degrades."""
    body = _object(payload)
    ed2k = _object(body.get("ed2k"))
    kad = _object(body.get("kad"))
    connected = ed2k.get("state") == "connected"
    user_id = ed2k.get("user_id")
    has_id = connected and isinstance(user_id, int) and not isinstance(user_id, bool)
    return NetworkStatus(
        # While disconnected the daemon reports user_id 0 and high_id false, so both are read
        # together with the state: "no id yet" must not read as a LowID (spec §6).
        ed2k_id=user_id if has_id else None,
        ed2k_high=connected and ed2k.get("high_id") is True,
        kad_status=_kad_status(kad),
        server_name=_string(ed2k.get("server_name")),
        server_addr=_server_addr(ed2k),
    )


def _map_result(result: object, keyword: str) -> tuple[list[FileObservation], int]:
    """One result row → one observation PER FILENAME (D4), plus the skipped count."""
    if not isinstance(result, dict):
        return [], 1
    ed2k_hash = _hash_hex(result.get("hash"))
    size_bytes = result.get("size_bytes")
    if ed2k_hash is None or not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        return [], 1
    media = _object(result.get("media"))
    raw_meta = _raw_meta(result, media)
    alternates = result.get("alternate_names")
    entries: list[object] = [result, *(alternates if isinstance(alternates, list) else [])]
    observations: list[FileObservation] = []
    skipped = 0
    for entry in entries:
        fields = _object(entry)
        name = _string(fields.get("name"))
        if name is None:
            skipped += 1
            continue
        total, complete = _sources(fields.get("sources"))
        observations.append(
            FileObservation(
                ed2k_hash=ed2k_hash,
                filename=name,
                size_bytes=size_bytes,
                source_count=total,
                complete_source_count=complete,
                keyword=keyword,
                media_length_sec=_optional_int(media.get("duration_seconds")),
                bitrate_kbps=_optional_int(media.get("bitrate_kilobits_per_second")),
                codec=_string(media.get("codec")),
                file_type=_string(result.get("file_type")),
                raw_meta=raw_meta,
            )
        )
    return observations, skipped


def _raw_meta(result: dict[str, Any], media: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Capture-all (D5): every unmapped key → ``(key, rendered_value)``, in wire order."""
    collected = [(key, _render(value)) for key, value in result.items() if key not in _MAPPED_KEYS]
    collected += [
        (f"media.{key}", _render(value))
        for key, value in media.items()
        if key not in _MAPPED_MEDIA_KEYS
    ]
    return tuple(collected)


def _render(value: object) -> str:
    """JSON-friendly rendering that NEVER raises: text as-is, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _object(value: object) -> dict[str, Any]:
    """A JSON object, or an empty one: an absent sub-object reads like an empty one."""
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str | None:
    """A non-empty string, or ``None`` (a blank name is as unusable as a missing one)."""
    return value if isinstance(value, str) and value else None


def _hash_hex(value: object) -> str | None:
    """A 32-character MD4 hash, lowercased, or ``None`` if unusable (the ONLY identifier)."""
    if not isinstance(value, str) or len(value) != _HASH_LENGTH:
        return None
    lowered = value.lower()
    return lowered if all(character in "0123456789abcdef" for character in lowered) else None


def _int(value: object) -> int:
    """An integer counter: absent or malformed reads as 0 (``bool`` is not a count)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _optional_int(value: object) -> int | None:
    """An integer that keeps the "not reported" state distinct from zero."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sources(value: object) -> tuple[int, int]:
    """``{total, complete}`` → the two counters, each 0 when absent or malformed."""
    sources = _object(value)
    return _int(sources.get("total")), _int(sources.get("complete"))


def _kad_status(kad: dict[str, Any]) -> KadStatus:
    """``kad.state`` + ``firewalled_tcp`` → the port's closed enum (§7.6: unknown → OFF)."""
    reported = kad.get("state")
    state = _KAD_STATES.get(reported) if isinstance(reported, str) else None
    if state is None:
        return KadStatus.OFF
    if state is KadStatus.CONNECTED and kad.get("firewalled_tcp") is True:
        return KadStatus.FIREWALLED
    return state


def _server_addr(ed2k: dict[str, Any]) -> str | None:
    """``"a.b.c.d:port"``, the shape the EC adapter reported, or ``None`` if incomplete."""
    ip = _string(ed2k.get("server_ip"))
    port = _optional_int(ed2k.get("server_port"))
    return None if ip is None or port is None else f"{ip}:{port}"
