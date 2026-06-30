"""Génération des mots-clés de recherche depuis les cibles (PUR, spec orchestration §4).

Domaine PUR : aucune I/O. Deux familles de mots-clés (spec MVP §6) : un mot-clé LARGE
(``keroro``) qui ratisse tout pour le catalogue, et des mots-clés CIBLÉS par segment
(``062a``, tokens du titre) pour la précision. ``generate_keywords`` est déterministe :
même table de cibles → même tuple, ORDONNÉ et DÉDUPLIQUÉ (premier vu gagne), pour que le
shuffle seedé du cycle (``cycle.py``) parte d'un ordre stable.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from catalog_matching.models import TargetSegment
from catalog_matching.normalization import tokenize

# Mot-clé large : la franchise. Ratisse tout pour le catalogue (spec MVP §6).
_BROAD_KEYWORD = "keroro"

# Tokens trop courts/communs pour cibler (le large les couvre déjà) : on ne génère pas
# un mot-clé d'un seul caractère ou d'un mot vide. La barre est volontairement basse.
_MIN_TARGETED_TOKEN_LENGTH = 2


@dataclass(frozen=True)
class SearchKeyword:
    """Un mot-clé à rechercher + sa provenance (``broad`` ou ``target_id``).

    ``text`` est le mot-clé envoyé à EC (déjà normalisé). ``origin`` documente d'où il
    vient (``"broad"`` pour le filet large, sinon le ``target_id`` du segment) : utile au
    logging structuré (§13 MVP) et à un futur scoring. GELÉ et hashable → déduplication
    par ``text`` triviale.
    """

    text: str
    origin: str


def _segment_id_keyword(target: TargetSegment) -> str:
    """Mot-clé d'identifiant de segment, ex. ``062a`` (numéro zéro-paddé sur 3 + lettre
    minuscule, comme les noms de fichiers source ``N°062A``, spec §7). Le ``°``/``n`` est
    laissé de côté : les serveurs eD2k tokenisent sur les non-alphanumériques, donc
    ``062a`` est le token précis qui distingue le segment."""
    return f"{target.absolute_number:03d}{target.segment.lower()}"


def generate_keywords(targets: Sequence[TargetSegment]) -> tuple[SearchKeyword, ...]:
    """Construit la liste ORDONNÉE et DÉDUPLIQUÉE des mots-clés (spec MVP §6).

    Ordre : le mot-clé LARGE d'abord, puis, par cible (dans l'ordre des cibles), son
    identifiant de segment puis les tokens significatifs de son titre. Déduplication par
    ``text`` (premier vu gagne) : deux titres partageant un mot ne le recherchent qu'une
    fois. Un token de longueur ``< 2`` est ignoré (le filet large le couvre déjà).
    """
    seen: set[str] = set()
    keywords: list[SearchKeyword] = []

    def add(text: str, origin: str) -> None:
        if text and text not in seen:
            seen.add(text)
            keywords.append(SearchKeyword(text=text, origin=origin))

    add(_BROAD_KEYWORD, "broad")
    for target in targets:
        add(_segment_id_keyword(target), target.target_id)
        for token in tokenize(target.title):
            if len(token) >= _MIN_TARGETED_TOKEN_LENGTH:
                add(token, target.target_id)
    return tuple(keywords)
