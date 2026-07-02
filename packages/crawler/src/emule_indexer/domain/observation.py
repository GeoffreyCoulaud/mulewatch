"""Observation of a file seen on the network (cf. spec EC-adapter §4; spec MVP §11).

PURE domain. ``FileObservation`` is aligned on the ``file_observations`` table (§11):
plan A will persist this object as-is; the DB adapter will add ``observed_at``/``node_id``
(same principle as ``MatchDecision``). ``raw_meta`` is the catch-all (JSON-friendly
``(name, value)`` pairs): we NEVER lose a metadata field, even an unknown one.
"""

from dataclasses import dataclass

from catalog_matching.models import FileCandidate

# DECISION 8: the "MB" shown by eMule clients are binary (MiB).
_BYTES_PER_MIB = 1024 * 1024


@dataclass(frozen=True)
class FileObservation:
    """A file observed during a search (content key = eD2k hash, never the person).

    Media fields are ``None`` if the network did not provide them (self-declared,
    unreliable metadata — spec MVP §10.1). ``keyword`` is the provenance (the search
    keyword that produced the observation).
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
        """Bridge to the matching engine: unit conversions (bytes → MiB, int → float)."""
        duration = float(self.media_length_sec) if self.media_length_sec is not None else None
        bitrate = float(self.bitrate_kbps) if self.bitrate_kbps is not None else None
        return FileCandidate(
            filename=self.filename,
            size_mb=self.size_bytes / _BYTES_PER_MIB,
            duration_sec=duration,
            bitrate_kbps=bitrate,
        )
