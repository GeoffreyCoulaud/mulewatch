"""Matching engine models (cf. spec §7, §8)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileCandidate:
    """A file observed on the network, candidate for matching.

    ``filename`` is the raw observed basename. The optional attributes are the
    metadata (self-declared, hence unreliable, cf. spec §10.1) used by
    ``attr_between``; ``None`` = missing.
    """

    filename: str
    size_mb: float | None = None
    duration_sec: float | None = None
    bitrate_kbps: float | None = None


@dataclass(frozen=True)
class TargetSegment:
    """A target episode segment (segment granularity, cf. spec §7).

    Provides ``{season} {seasonal_number} {absolute_number} {segment} {title}`` to
    the interpolation of regex patterns. ``sole_segment`` is NOT read from YAML: it is
    derived by ``parse_targets`` (``True`` iff the episode has a single segment) and
    drives the ``{mono_gate}`` placeholder (cf. interpolation.py).
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
        """Stable segment identifier, e.g. ``S2E062A``."""
        return f"S{self.season}E{self.absolute_number:03d}{self.segment.upper()}"
