"""Modèles du moteur de matching (cf. spec §7, §8)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileCandidate:
    """Un fichier observé sur le réseau, candidat au matching.

    ``filename`` est le basename brut observé. Les attributs optionnels sont les
    métadonnées (auto-déclarées, donc non fiables, cf. spec §10.1) utilisées par
    ``attr_between`` ; ``None`` = absent.
    """

    filename: str
    size_mb: float | None = None
    duration_sec: float | None = None
    bitrate_kbps: float | None = None


@dataclass(frozen=True)
class TargetSegment:
    """Un segment d'épisode cible (granularité segment, cf. spec §7).

    Fournit ``{season} {seasonal_number} {absolute_number} {segment} {title}`` à
    l'interpolation des patterns regex. ``sole_segment`` n'est PAS lu du YAML : il est
    dérivé par ``parse_targets`` (``True`` ssi l'épisode n'a qu'un seul segment) et
    pilote le placeholder ``{mono_gate}`` (cf. interpolation.py).
    """

    season: int
    seasonal_number: int
    absolute_number: int
    segment: str
    title: str
    status: str = "lost"
    sole_segment: bool = False

    @property
    def target_id(self) -> str:
        """Identifiant stable du segment, ex. ``S2E062A``."""
        return f"S{self.season}E{self.absolute_number:03d}{self.segment.upper()}"
