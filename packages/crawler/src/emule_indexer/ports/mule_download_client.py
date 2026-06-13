"""Port ``MuleDownloadClient`` : les opérations de DOWNLOAD attendues d'un client eMule.

SÉPARÉ de ``MuleClient`` (ISP, spec download §2.4/§4 — DÉCISION D3) : la recherche ne dépend
pas des méthodes de download et inversement. La MÊME classe adapter (``AmuleEcClient``) peut
implémenter les deux Protocols STRUCTURELLEMENT ; en exploitation, la connexion download est
une instance DISTINCTE (sa propre connexion EC, spec §2.2). Le port n'importe QUE le domaine
et le DTO réseau partagé ``NetworkStatus`` (déjà dans ``ports/mule_client.py`` — réutilisé,
pas dupliqué : HighID requis pour télécharger en mode full).

``DownloadEntry`` est le DTO de port (frozen) : le crawler NE LIT JAMAIS les octets (spec
§4) ; ``download_queue`` ne renvoie que des MÉTADONNÉES EC. La complétion se déduit de
``size_done``/``size_full`` (DÉCISION D2 : EC n'expose pas de chemin staging portable, donc
le DTO n'en porte pas — la localisation pour la quarantaine est dérivée d'un staging
configuré par l'appelant). Le contrat d'ERREUR est celui du Plan C : un flux mort lève
``MuleUnreachableError`` (``ports/mule_client.py``) — l'application le tolère (spec §9).
"""

from dataclasses import dataclass
from typing import Protocol

from emule_indexer.ports.mule_client import NetworkStatus


@dataclass(frozen=True)
class DownloadEntry:
    """Une entrée de la file de download d'amuled (métadonnées EC SEULES, spec §4).

    ``ed2k_hash`` = clé contenu (hex minuscule 32). ``size_done``/``size_full`` = octets
    transférés / taille totale. ``is_complete`` est vrai SEULEMENT si la taille totale est
    connue (> 0) ET atteinte — un ``size_full == 0`` (entrée naissante) n'est jamais complet.
    """

    ed2k_hash: str
    size_done: int
    size_full: int

    @property
    def is_complete(self) -> bool:
        """``True`` si le fichier est entièrement transféré côté amuled (spec §5)."""
        return self.size_full > 0 and self.size_done >= self.size_full


class MuleDownloadClient(Protocol):
    """Contrat async des opérations de download (spec §4). Actions UNITAIRES : aucun sleep/retry.

    ``add_link`` ajoute un lien ed2k à la file de download d'amuled. ``download_queue`` rend un
    snapshot de la file (hash + avancement). ``network_status`` est réutilisé (HighID requis
    pour télécharger en mode full).
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def add_link(self, ed2k_link: str) -> None: ...

    async def download_queue(self) -> tuple[DownloadEntry, ...]: ...

    async def network_status(self) -> NetworkStatus: ...
