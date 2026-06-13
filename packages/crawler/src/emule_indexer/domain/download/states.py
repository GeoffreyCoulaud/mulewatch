"""États d'un téléchargement (PUR, spec download §7 — DÉCISION D7).

Domaine PUR : aucune I/O. ``DownloadState`` est l'enum FERMÉ du cycle de vie d'un download
côté crawler : ``queued`` (lien ajouté à amuled) → ``downloading`` (amuled le tire) →
``completed`` (octets complets côté amuled, encore en staging) → ``quarantined`` (sorti du
staging par un rename atomique, vérif enfilée) ; ``failed`` si amuled signale une erreur.

Le plafond disque APPLICATIF (spec §7) ne compte que les downloads ACTIFS : un état
terminal (``completed``/``quarantined``/``failed``) ne consomme plus de quota de download
en cours (un ``completed`` ne grandit plus et sera promu à la prochaine itération). C'est le
seul jugement métier porté ici ; le calcul de la somme vit dans l'adapter repo.
"""

from enum import StrEnum

# DÉCISION D7 : terminaux pour le plafond (ne consomment plus de quota actif).
_TERMINAL_STATES = frozenset({"completed", "quarantined", "failed"})


class DownloadState(StrEnum):
    """Cycle de vie d'un download côté crawler (enum fermé, spec §7)."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"
    FAILED = "failed"


def is_terminal(state: DownloadState) -> bool:
    """``True`` si l'état ne consomme plus de quota de download actif (spec §7)."""
    return state.value in _TERMINAL_STATES
