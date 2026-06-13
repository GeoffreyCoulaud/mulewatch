"""Adapter ``Quarantine`` sur le système de fichiers (spec download §8 — DÉCISION D10).

``promote`` fait un ``os.replace`` (rename ATOMIQUE même-FS) du fichier de staging vers
``quarantine_dir / <hash>`` : opération de métadonnée seule, le contenu n'est JAMAIS ouvert,
lu, ni rendu exécutable (le rename ne touche pas les permissions). Un échec (source absente,
FS plein, cross-device → ``OSError``) PROPAGE : la boucle de download laisse alors le
download en ``completed`` et retentera (idempotent, spec §9). Le staging et la quarantaine
DOIVENT être sur le même système de fichiers (sinon ``os.replace`` lève — c'est une
contrainte de déploiement, vérifiée au câblage de D-verify).
"""

import os
from pathlib import Path


class FilesystemQuarantine:
    """Mise en quarantaine par rename atomique (satisfaction STRUCTURELLE du port)."""

    def __init__(self, quarantine_dir: Path) -> None:
        self._quarantine_dir = quarantine_dir

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        """Rename atomique ``staging_path`` → ``quarantine_dir/<hash>`` (spec §8).

        ``os.replace`` est atomique sur le même FS ; il écrase une cible existante (un
        re-promote idempotent du même hash est sûr) et ne modifie pas les permissions (jamais
        +x). Une source absente lève ``FileNotFoundError`` ; la boucle retentera.
        """
        # ed2k_hash : toujours 32 caractères [0-9a-f] (garanti en amont — _map_partfile .hex(),
        # _CANONICAL_HASH_RE, et la contrainte CHECK SQLite) → aucun '/'/'..' possible, pas de
        # traversée de chemin hors quarantine_dir.
        os.replace(staging_path, self._quarantine_dir / ed2k_hash)
