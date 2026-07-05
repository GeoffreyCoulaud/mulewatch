"""Maps EC search results → ``FileObservation`` (spec §4/§6, capture-all).

Ref. §5: the EXHAUSTIVE list of metadata EC exposes on a result is: name,
size, MD4 hash, sources, complete sources, status, parent, (rating 3.0.0). NO media
tag (duration/bitrate/codec) transits — ``FileObservation``'s media fields stay
``None``; the capture-all ``raw_meta`` gathers every unmapped tag, known or unknown.
Unknown-tolerance: an unknown tag is NEVER an error; only an entry with no usable
hash/name/size is discarded — and COUNTED, never fatal to the batch (spec §6).
"""

from typing import Final

from mulewatch.adapters.mule_ec import codes
from mulewatch.adapters.mule_ec.codec import INT_WIDTHS, EcTag
from mulewatch.adapters.mule_ec.errors import EcProtocolError
from mulewatch.domain.observation import FileObservation

# Entry tags mapped to structured fields (hence EXCLUDED from raw_meta — the FIRST
# occurrence only, the one ``find()`` reads; a hostile duplicate falls into raw_meta).
_MAPPED_CHILD_TAGS = frozenset(
    {
        codes.EC_TAG_PARTFILE_NAME,
        codes.EC_TAG_PARTFILE_SIZE_FULL,
        codes.EC_TAG_PARTFILE_HASH,
        codes.EC_TAG_PARTFILE_SOURCE_COUNT,
        codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER,
    }
)

# Tags discarded from ALL output (ref. §9 pitfall 13): EC_TAG_SEARCH_PARENT points to the
# ECID of ANOTHER entry — a volatile SESSION identifier, like the entry's own ECID;
# persisting it would break plan A's cross-session dedup.
_DISCARDED_CHILD_TAGS: Final[frozenset[int]] = frozenset({codes.EC_TAG_SEARCH_PARENT})


def map_search_results(
    tags: tuple[EcTag, ...], keyword: str
) -> tuple[tuple[FileObservation, ...], int]:
    """Top-level tags of an EC_OP_SEARCH_RESULTS → ``(observations, skipped_count)``."""
    observations: list[FileObservation] = []
    skipped = 0
    for tag in tags:
        if tag.name != codes.EC_TAG_SEARCHFILE:
            continue  # unexpected top level: tolerated, ignored (not an entry)
        observation = _map_entry(tag, keyword)
        if observation is None:
            skipped += 1
        else:
            observations.append(observation)
    return tuple(observations), skipped


def _map_entry(entry: EcTag, keyword: str) -> FileObservation | None:
    """An entry (EC_TAG_SEARCHFILE sub-tree) → observation, or ``None`` if unusable.

    The ECID (the entry's own value) is NEVER kept: a volatile session
    identifier (ref. §9 pitfall 13); only the MD4 hash identifies the file.
    """
    hash_tag = entry.find(codes.EC_TAG_PARTFILE_HASH)
    name_tag = entry.find(codes.EC_TAG_PARTFILE_NAME)
    size_tag = entry.find(codes.EC_TAG_PARTFILE_SIZE_FULL)
    if hash_tag is None or name_tag is None or size_tag is None:
        return None
    try:
        ed2k_hash = _hash_hex(hash_tag)
        filename = name_tag.string_value()
        size_bytes = size_tag.int_value()
        source_count = _optional_int(entry, codes.EC_TAG_PARTFILE_SOURCE_COUNT)
        complete_source_count = _optional_int(entry, codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER)
    except EcProtocolError:
        return None  # rotten entry: discarded (the caller counts), never fatal
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=size_bytes,
        source_count=source_count,
        complete_source_count=complete_source_count,
        keyword=keyword,
        raw_meta=_raw_meta(entry),
    )


def _hash_hex(tag: EcTag) -> str:
    """MD4 hash → 32-character lowercase hex (16 HASH16 bytes required, ref. §3)."""
    if tag.tag_type != codes.EC_TAGTYPE_HASH16 or len(tag.value) != 16:
        raise EcProtocolError("hash eD2k inexploitable")
    return tag.value.hex()


def _optional_int(entry: EcTag, name: int) -> int:
    """Optional integer of an entry: absent = 0 (ref. §3: absent = null value).

    Present-but-malformed = absent = 0: a rotten counter NEVER costs the observation
    (only hash/name/size are disqualifying). The malformed bytes are deliberately
    not resurrected into raw_meta (simplicity; the hash identifies the file).
    """
    tag = entry.find(name)
    if tag is None:
        return 0
    try:
        return tag.int_value()
    except EcProtocolError:
        return 0


def _raw_meta(entry: EcTag) -> tuple[tuple[str, str], ...]:
    """Capture-all (DECISION 7): every unmapped tag → ``("0xNNNN", rendered_value)``.

    Only the FIRST occurrence of a mapped name is consumed (the one ``find()`` reads);
    a hostile duplicate stays visible in raw_meta. Each unmapped sub-tree is
    walked in FULL (depth-first, wire order): no grandchild is lost.
    """
    collected: list[tuple[str, str]] = []
    consumed: set[int] = set()
    for child in entry.children:
        if child.name in _MAPPED_CHILD_TAGS and child.name not in consumed:
            consumed.add(child.name)
            continue
        _collect_subtree(child, collected)
    return tuple(collected)


def _collect_subtree(tag: EcTag, collected: list[tuple[str, str]]) -> None:
    """An unmapped node → its pair, then its children recursively (depth bounded
    upstream by the codec, _MAX_TAG_DEPTH). Discarded tags (pitfall 13) NEVER leak out."""
    if tag.name in _DISCARDED_CHILD_TAGS:
        return
    collected.append((f"0x{tag.name:04X}", _render_value(tag)))
    for child in tag.children:
        _collect_subtree(child, collected)


def _render_value(tag: EcTag) -> str:
    """JSON-friendly rendering that NEVER raises: decimal integer, text, else raw hex."""
    if tag.tag_type in INT_WIDTHS and len(tag.value) == INT_WIDTHS[tag.tag_type]:
        return str(int.from_bytes(tag.value, "big"))
    if tag.tag_type == codes.EC_TAGTYPE_STRING and tag.value.endswith(b"\x00"):
        return tag.value[:-1].decode("utf-8", errors="replace")
    return tag.value.hex()
