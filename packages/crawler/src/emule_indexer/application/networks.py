"""Observability network label derived from the search channel (E-D6).

``SearchChannel.GLOBAL`` = eD2k servers → ``"ed2k"``; ``SearchChannel.KAD`` → ``"kad"``. The
network source of an observation is known BY CONSTRUCTION (the search is launched per channel),
without touching persistence."""

from emule_indexer.ports.mule_client import SearchChannel

ED2K = "ed2k"
KAD = "kad"

_LABELS = {SearchChannel.GLOBAL: ED2K, SearchChannel.KAD: KAD}


def network_label(channel: SearchChannel) -> str:
    """``"ed2k"`` for GLOBAL, ``"kad"`` for KAD."""
    return _LABELS[channel]
