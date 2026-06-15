"""Label réseau d'observabilité dérivé du canal de recherche (E-D6).

``SearchChannel.GLOBAL`` = serveurs eD2k → ``"ed2k"`` ; ``SearchChannel.KAD`` → ``"kad"``. La
source réseau d'une observation est connue PAR CONSTRUCTION (la recherche est lancée par canal),
sans toucher la persistance."""

from emule_indexer.ports.mule_client import SearchChannel

ED2K = "ed2k"
KAD = "kad"

_LABELS = {SearchChannel.GLOBAL: ED2K, SearchChannel.KAD: KAD}


def network_label(channel: SearchChannel) -> str:
    """``"ed2k"`` pour GLOBAL, ``"kad"`` pour KAD."""
    return _LABELS[channel]
