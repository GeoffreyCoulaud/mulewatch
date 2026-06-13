"""Observation d'un fichier vu sur le réseau (cf. spec EC-adapter §4 ; spec MVP §11).

Domaine PUR. ``FileObservation`` est aligné sur la table ``file_observations`` (§11) :
le plan A persistera cet objet tel quel ; l'adapter DB ajoutera ``observed_at``/``node_id``
(même principe que ``MatchDecision``). ``raw_meta`` est le capture-all (paires
``(nom, valeur)`` JSON-friendly) : on ne perd JAMAIS une métadonnée, même inconnue.
"""

from dataclasses import dataclass

from emule_indexer.domain.matching.models import FileCandidate

# DÉCISION 8 : les « MB » affichés par les clients eMule sont binaires (Mio).
_BYTES_PER_MIB = 1024 * 1024


@dataclass(frozen=True)
class FileObservation:
    """Un fichier observé lors d'une recherche (clé contenu = hash eD2k, jamais la personne).

    Les champs média sont ``None`` si le réseau ne les a pas fournis (métadonnées
    auto-déclarées, non fiables — spec MVP §10.1). ``keyword`` est la provenance
    (le mot-clé de la recherche qui a produit l'observation).
    """

    ed2k_hash: str
    filename: str
    size_bytes: int
    source_count: int
    complete_source_count: int
    keyword: str
    media_length_sec: int | None = None
    bitrate_kbps: int | None = None
    codec: str | None = None
    file_type: str | None = None
    raw_meta: tuple[tuple[str, str], ...] = ()

    def to_candidate(self) -> FileCandidate:
        """Pont vers le moteur de matching : conversions d'unités (octets → Mio, int → float)."""
        duration = float(self.media_length_sec) if self.media_length_sec is not None else None
        bitrate = float(self.bitrate_kbps) if self.bitrate_kbps is not None else None
        return FileCandidate(
            filename=self.filename,
            size_mb=self.size_bytes / _BYTES_PER_MIB,
            duration_sec=duration,
            bitrate_kbps=bitrate,
        )
