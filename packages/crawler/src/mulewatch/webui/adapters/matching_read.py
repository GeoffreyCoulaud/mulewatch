"""Recompute a match explanation from the current config (spec W-D7 / Task 9).

``MatchingExplainer`` loads ``matcher.yaml`` + ``targets.yaml`` at construction time
(once) and exposes ``explain()`` to recompute a file's explanation against the CURRENT
config.

``size_bytes → size_mb`` (and the trivial ``int → float`` casts) reuse the crawler's ONE
canonical converter, ``mulewatch.domain.observation.candidate_from_fields`` (which encodes
DECISION 8: eMule "MB" are binary Mio) — the monolith consolidation removed the old boundary
that forced this module to reimplement it. The ONLY case that cannot go through the canonical
converter is ``size_bytes is None``: the converter requires an ``int`` (a persisted
observation always has a size), while ``explain()``'s contract still permits ``None``, so that
path builds the ``FileCandidate`` directly.
"""

from pathlib import Path

import yaml

from catalog_matching.engine import Explanation, MatchingEngine
from catalog_matching.models import FileCandidate, TargetSegment
from catalog_matching.validation import parse_matcher_config, parse_targets
from mulewatch.domain.observation import candidate_from_fields


class MatchingExplainer:
    """Build and cache a :class:`MatchingEngine` from the YAML files.

    The engine is resolved ONCE (matcher trees pre-compiled per target) at
    construction. Successive calls to ``explain()`` reuse the same engine.
    """

    def __init__(self, *, matcher_yaml: Path, targets_yaml: Path) -> None:
        matcher_raw = yaml.safe_load(matcher_yaml.read_text(encoding="utf-8"))
        targets_raw = yaml.safe_load(targets_yaml.read_text(encoding="utf-8"))
        matcher_config = parse_matcher_config(matcher_raw)
        targets: tuple[TargetSegment, ...] = parse_targets(targets_raw)
        self._engine = MatchingEngine(matcher_config, targets)

    def explain(
        self,
        filename: str,
        size_bytes: int | None,
        media_length_sec: int | None,
        bitrate_kbps: int | None,
        target_id: str,
    ) -> Explanation | None:
        """Recompute the explanation of ``filename`` against target ``target_id``.

        Delegates the unit conversion to the crawler's canonical ``candidate_from_fields``
        (binary-Mio ``size_bytes → size_mb`` + the ``int → float`` casts). Only the
        ``size_bytes is None`` case is built directly, since the canonical converter requires
        an ``int``.

        Return ``None`` if ``target_id`` is unknown to the current config.
        """
        if size_bytes is None:
            candidate = FileCandidate(
                filename=filename,
                size_mb=None,
                duration_sec=float(media_length_sec) if media_length_sec is not None else None,
                bitrate_kbps=float(bitrate_kbps) if bitrate_kbps is not None else None,
            )
        else:
            candidate = candidate_from_fields(filename, size_bytes, media_length_sec, bitrate_kbps)
        return self._engine.explain(candidate, target_id)
