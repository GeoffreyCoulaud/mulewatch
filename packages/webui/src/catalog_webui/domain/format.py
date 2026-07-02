"""Pure formatting for display (webui spec §4/§7). No I/O.

NB: building the eD2k link (with percent-encoding of the name) lives in
``catalog_matching.ed2k_link`` — a package shared by crawler+webui — to prevent
webui/crawler divergence on the canonical format (webui-security#0 regression: the
webui interpolated the raw filename, a hostile ``|`` broke the link framing)."""


def short_hash(ed2k_hash: str) -> str:
    """Truncated hash for display (first 8 characters + ellipsis)."""
    if len(ed2k_hash) <= 8:
        return ed2k_hash
    return f"{ed2k_hash[:8]}…"
