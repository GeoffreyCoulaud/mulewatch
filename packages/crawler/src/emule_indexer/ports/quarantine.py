"""Port ``Quarantine`` : remettre un fichier complété en quarantaine (spec download §8/§10).

Le crawler NE LIT JAMAIS le contenu d'un fichier téléchargé (§10.3 MVP : le sujet du
catalogue est le fichier, jamais la personne ; on ne vérifie/lit qu'après une mise en
quarantaine sûre). ``promote`` est une opération de MÉTADONNÉE seule : déplacer (rename) le
fichier du staging vers ``quarantine/<hash>``, sans jamais l'ouvrir ni le rendre exécutable.
Le verifier (D-verify) lira le fichier en quarantaine — pas le crawler. Le stub du Protocol
tient sur UNE ligne (le ``def`` est couvert à la création de la classe).
"""

from pathlib import Path
from typing import Protocol


class Quarantine(Protocol):
    """Contrat de mise en quarantaine (spec §8). ``promote`` ne lève qu'en cas d'échec FS."""

    def promote(self, staging_path: Path, ed2k_hash: str) -> None: ...
