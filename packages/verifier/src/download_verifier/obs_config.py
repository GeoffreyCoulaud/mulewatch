"""Verifier observability mini YAML loader (E-D2/E-D10). Imports NOTHING from ``mulewatch``
(package boundary). Reads ``observability.log_level`` (default ``INFO``); unknown level →
``ObsConfigError`` (fail-fast). ``AnalysisConfig`` (env) stays separate and unchanged."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ObsConfigError(Exception):
    """Invalid observability config → refusal to start."""


@dataclass(frozen=True)
class ObservabilityConfig:
    """Verifier observability settings (only ``log_level``; ``/metrics`` always exposed)."""

    log_level: str


def load_observability(path: Path) -> ObservabilityConfig:
    """Read ``path`` (YAML), extract ``observability.log_level`` (default INFO), validate."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = raw.get("observability", {}) if isinstance(raw, dict) else {}
    section = section if isinstance(section, dict) else {}
    log_level = section.get("log_level", "INFO")
    if log_level not in _LEVELS:
        raise ObsConfigError(
            f"observability.log_level: one of {sorted(_LEVELS)} expected, got {log_level!r}"
        )
    return ObservabilityConfig(log_level=log_level)
