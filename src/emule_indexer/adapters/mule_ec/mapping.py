"""Mapping des résultats de recherche EC → ``FileObservation`` (spec §4/§6, capture-all).

Réf. §5 : la liste EXHAUSTIVE des métadonnées qu'EC expose sur un résultat est : nom,
taille, hash MD4, sources, sources complètes, statut, parent, (rating 3.0.0). AUCUN tag
média (durée/bitrate/codec) ne transite — les champs média de ``FileObservation`` restent
``None`` ; le capture-all ``raw_meta`` ramasse tout tag non mappé, connu ou inconnu.
Tolérance aux inconnus : un tag inconnu n'est JAMAIS une erreur ; seule une entrée sans
hash/nom/taille exploitables est écartée — et COMPTÉE, jamais fatale au lot (spec §6).
"""

from typing import Final

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import INT_WIDTHS, EcTag
from emule_indexer.adapters.mule_ec.errors import EcProtocolError
from emule_indexer.domain.observation import FileObservation

# Tags d'entrée mappés vers des champs structurés (donc EXCLUS de raw_meta — la PREMIÈRE
# occurrence seulement, celle que ``find()`` lit ; un doublon hostile tombe dans raw_meta).
_MAPPED_CHILD_TAGS = frozenset(
    {
        codes.EC_TAG_PARTFILE_NAME,
        codes.EC_TAG_PARTFILE_SIZE_FULL,
        codes.EC_TAG_PARTFILE_HASH,
        codes.EC_TAG_PARTFILE_SOURCE_COUNT,
        codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER,
    }
)

# Tags écartés de TOUTE sortie (réf. §9 piège 13) : EC_TAG_SEARCH_PARENT pointe l'ECID
# d'une AUTRE entrée — identifiant de SESSION volatil, comme l'ECID propre de l'entrée ;
# le persister casserait la dédup inter-sessions du plan A.
_DISCARDED_CHILD_TAGS: Final[frozenset[int]] = frozenset({codes.EC_TAG_SEARCH_PARENT})


def map_search_results(
    tags: tuple[EcTag, ...], keyword: str
) -> tuple[tuple[FileObservation, ...], int]:
    """Tags de premier niveau d'un EC_OP_SEARCH_RESULTS → ``(observations, nb_écartés)``."""
    observations: list[FileObservation] = []
    skipped = 0
    for tag in tags:
        if tag.name != codes.EC_TAG_SEARCHFILE:
            continue  # premier niveau inattendu : toléré, ignoré (pas une entrée)
        observation = _map_entry(tag, keyword)
        if observation is None:
            skipped += 1
        else:
            observations.append(observation)
    return tuple(observations), skipped


def _map_entry(entry: EcTag, keyword: str) -> FileObservation | None:
    """Une entrée (sous-arbre EC_TAG_SEARCHFILE) → observation, ou ``None`` si inexploitable.

    L'ECID (valeur propre de l'entrée) n'est JAMAIS conservé : identifiant de session
    volatil (réf. §9 piège 13) ; seul le hash MD4 identifie le fichier.
    """
    hash_tag = entry.find(codes.EC_TAG_PARTFILE_HASH)
    name_tag = entry.find(codes.EC_TAG_PARTFILE_NAME)
    size_tag = entry.find(codes.EC_TAG_PARTFILE_SIZE_FULL)
    if hash_tag is None or name_tag is None or size_tag is None:
        return None
    try:
        ed2k_hash = _hash_hex(hash_tag)
        filename = name_tag.string_value()
        size_bytes = size_tag.int_value()
        source_count = _optional_int(entry, codes.EC_TAG_PARTFILE_SOURCE_COUNT)
        complete_source_count = _optional_int(entry, codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER)
    except EcProtocolError:
        return None  # entrée pourrie : écartée (l'appelant compte), jamais fatale
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=size_bytes,
        source_count=source_count,
        complete_source_count=complete_source_count,
        keyword=keyword,
        raw_meta=_raw_meta(entry),
    )


def _hash_hex(tag: EcTag) -> str:
    """Hash MD4 → hex minuscule 32 caractères (16 octets HASH16 exigés, réf. §3)."""
    if tag.tag_type != codes.EC_TAGTYPE_HASH16 or len(tag.value) != 16:
        raise EcProtocolError("hash eD2k inexploitable")
    return tag.value.hex()


def _optional_int(entry: EcTag, name: int) -> int:
    """Entier optionnel d'une entrée : absence = 0 (réf. §3 : absence = valeur nulle).

    Présent-mais-malformé = absent = 0 : un compteur pourri ne coûte JAMAIS l'observation
    (seuls hash/nom/taille sont éliminatoires). Les octets malformés ne sont délibérément
    pas ressuscités dans raw_meta (simplicité ; le hash identifie le fichier).
    """
    tag = entry.find(name)
    if tag is None:
        return 0
    try:
        return tag.int_value()
    except EcProtocolError:
        return 0


def _raw_meta(entry: EcTag) -> tuple[tuple[str, str], ...]:
    """Capture-all (DÉCISION 7) : tout tag non mappé → ``("0xNNNN", valeur_rendue)``.

    Seule la PREMIÈRE occurrence d'un nom mappé est consommée (celle que ``find()`` lit) ;
    un doublon hostile reste visible dans raw_meta. Chaque sous-arbre non mappé est
    parcouru en ENTIER (profondeur d'abord, ordre wire) : aucun petit-fils n'est perdu.
    """
    collected: list[tuple[str, str]] = []
    consumed: set[int] = set()
    for child in entry.children:
        if child.name in _MAPPED_CHILD_TAGS and child.name not in consumed:
            consumed.add(child.name)
            continue
        _collect_subtree(child, collected)
    return tuple(collected)


def _collect_subtree(tag: EcTag, collected: list[tuple[str, str]]) -> None:
    """Un nœud non mappé → sa paire, puis ses enfants récursivement (profondeur bornée
    en amont par le codec, _MAX_TAG_DEPTH). Les tags écartés (piège 13) ne sortent JAMAIS."""
    if tag.name in _DISCARDED_CHILD_TAGS:
        return
    collected.append((f"0x{tag.name:04X}", _render_value(tag)))
    for child in tag.children:
        _collect_subtree(child, collected)


def _render_value(tag: EcTag) -> str:
    """Rendu JSON-friendly qui ne lève JAMAIS : entier décimal, texte, sinon hex brut."""
    if tag.tag_type in INT_WIDTHS and len(tag.value) == INT_WIDTHS[tag.tag_type]:
        return str(int.from_bytes(tag.value, "big"))
    if tag.tag_type == codes.EC_TAGTYPE_STRING and tag.value.endswith(b"\x00"):
        return tag.value[:-1].decode("utf-8", errors="replace")
    return tag.value.hex()
