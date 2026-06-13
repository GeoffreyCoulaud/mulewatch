"""Politique d'auto-download PURE (spec download §6 — DÉCISION D4/D5).

Domaine PUR : aucune I/O, aucun repo, aucun ``NetworkStatus``. ``download_policy`` rend un
``DownloadVerdict`` (enum, pas bool → explicabilité + métrique future) depuis des PRIMITIFS :
le lookup ``target_id → status`` est fait par l'APPLICATION (depuis les ``targets`` chargées)
et passé en booléen/chaîne, exactement comme ``effective_coverage`` reçoit des booléens (le
domaine n'importe jamais un port).

Ordre des gardes (spec §6) : un non-``download`` est une garde conservatrice (DÉCISION D5 :
ne jamais télécharger — l'application ne devrait pas appeler la politique hors download, mais
on ne crashe pas) ; une cible ``complete`` n'a plus besoin du fichier ; un hash déjà
téléchargé est dédupliqué ; au-dessus du plafond disque applicatif on DIFFÈRE (la décision
reste dans le journal, retentée quand de la place se libère, spec §7) ; sinon on télécharge.
"""

from enum import StrEnum


class DownloadVerdict(StrEnum):
    """Verdict de la politique d'auto-download (enum fermé, spec §6)."""

    DOWNLOAD = "download"
    SKIP_COMPLETE = "skip_complete"
    SKIP_DEDUP = "skip_dedup"
    SKIP_DISK_CAP = "skip_disk_cap"


def download_policy(
    *,
    tier: str,
    target_status: str,
    already_downloaded: bool,
    committed_bytes: int,
    file_size: int,
    disk_cap: int,
) -> DownloadVerdict:
    """Décide du sort d'un candidat download (spec §6). Toutes branches testées.

    ``committed_bytes`` = somme des ``size_bytes`` des downloads ACTIFS (non terminaux) ;
    ``file_size`` = taille du candidat ; ``disk_cap`` = plafond applicatif config. Le plafond
    est un MAX inclusif : ``committed + file_size <= disk_cap`` est autorisé.
    """
    if tier != "download":
        return DownloadVerdict.SKIP_COMPLETE  # garde conservatrice (DÉCISION D5)
    if target_status == "complete":
        return DownloadVerdict.SKIP_COMPLETE
    if already_downloaded:
        return DownloadVerdict.SKIP_DEDUP
    if committed_bytes + file_size > disk_cap:
        return DownloadVerdict.SKIP_DISK_CAP
    return DownloadVerdict.DOWNLOAD
