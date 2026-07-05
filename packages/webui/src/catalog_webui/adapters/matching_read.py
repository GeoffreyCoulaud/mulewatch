"""Recompute a match explanation from the current config (spec W-D7 / Task 9).

``MatchingExplainer`` loads ``matcher.yaml`` + ``targets.yaml`` at construction time
(once) and exposes ``explain()`` to recompute a file's explanation against the CURRENT
config.

``size_bytes → size_mb`` conversion: reproduces exactly the logic of
``mulewatch.domain.observation.FileObservation.to_candidate`` without importing the
crawler. Source: ``packages/crawler/src/mulewatch/domain/observation.py``:

    _BYTES_PER_MIB = 1024 * 1024
    size_mb=self.size_bytes / _BYTES_PER_MIB

The ``duration_sec`` and ``bitrate_kbps`` fields are converted ``int → float | None``
the same way (``float(x) if x is not None else None``).
"""

from pathlib import Path

import yaml

from catalog_matching.engine import Explanation, MatchingEngine
from catalog_matching.models import FileCandidate, TargetSegment
from catalog_matching.validation import parse_matcher_config, parse_targets

# Crawler DECISION 8: eMule "MB" are Mio (binary).
_BYTES_PER_MIB = 1024 * 1024


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

        Reproduces the crawler's unit conversion:
        - ``size_bytes / (1024 * 1024)`` → ``size_mb`` (binary Mio, always provided
          if ``size_bytes`` is non-``None``).
        - ``float(media_length_sec)`` → ``duration_sec`` (``None`` if missing).
        - ``float(bitrate_kbps)`` → the ``FileCandidate``'s ``bitrate_kbps`` (``None`` if
          missing).

        Return ``None`` if ``target_id`` is unknown to the current config.
        """
        size_mb = size_bytes / _BYTES_PER_MIB if size_bytes is not None else None
        duration = float(media_length_sec) if media_length_sec is not None else None
        bitrate = float(bitrate_kbps) if bitrate_kbps is not None else None
        candidate = FileCandidate(
            filename=filename,
            size_mb=size_mb,
            duration_sec=duration,
            bitrate_kbps=bitrate,
        )
        return self._engine.explain(candidate, target_id)
