"""Adapter : lecture d'un fichier YAML en structures Python (cf. spec §4 frontière I/O).

SEUL module du projet qui importe ``yaml`` et touche le système de fichiers pour la
config. Ne valide PAS le fond (schéma/graphe/RE2) : c'est le rôle du domaine
(``domain.matching.validation``). Garde-fou minimal : la racine doit être un mapping.
"""

from pathlib import Path
from typing import Any

import yaml


class YamlLoadError(Exception):
    """Le fichier YAML est illisible ou sa racine n'est pas un mapping."""


def load_yaml(path: Path) -> dict[str, Any]:
    """Lit ``path`` et renvoie sa racine (un mapping) parsée par ``yaml.safe_load``.

    ``safe_load`` parse les dates ISO en ``datetime.date`` et n'instancie aucun objet
    Python arbitraire (pas de ``yaml.load`` non sûr). Frontière d'erreur de l'adapter :
    fichier illisible, YAML invalide, ou racine non-mapping (liste, scalaire, fichier vide
    → ``None``) lèvent tous :class:`YamlLoadError`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise YamlLoadError(f"fichier YAML illisible : {path} ({exc})") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise YamlLoadError(f"YAML invalide dans {path} : {exc}") from exc
    if not isinstance(raw, dict):
        raise YamlLoadError(f"racine YAML attendue = mapping, obtenu {type(raw).__name__} ({path})")
    return raw
