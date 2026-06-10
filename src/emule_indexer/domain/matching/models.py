"""Modèles du moteur de matching (cf. spec §7, §8)."""

import datetime
from dataclasses import dataclass, field


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

    Fournit ``{number}``, ``{segment}``, ``{title}`` et ``{date_alt}`` (via
    ``broadcast_date``) à l'interpolation des patterns regex.
    """

    season: int
    number: int
    segment: str
    title: str
    broadcast_date: datetime.date | None = None
    status: str = "lost"
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def target_id(self) -> str:
        """Identifiant stable du segment, ex. ``S2E062A``."""
        return f"S{self.season}E{self.number:03d}{self.segment.upper()}"
