"""Mini-loader YAML d'observabilité du verifier (E-D2/E-D10). N'importe RIEN de ``emule_indexer``
(frontière de paquet). Lit ``observability.log_level`` (défaut ``INFO``) ; niveau inconnu →
``ObsConfigError`` (fail-fast). ``AnalysisConfig`` (env) reste séparé et inchangé."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ObsConfigError(Exception):
    """Config d'observabilité invalide → refus de démarrer."""


@dataclass(frozen=True)
class ObservabilityConfig:
    """Réglages d'observabilité du verifier (seul ``log_level`` ; ``/metrics`` toujours exposé)."""

    log_level: str


def load_observability(path: Path) -> ObservabilityConfig:
    """Lit ``path`` (YAML), extrait ``observability.log_level`` (défaut INFO), valide."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = raw.get("observability", {}) if isinstance(raw, dict) else {}
    section = section if isinstance(section, dict) else {}
    log_level = section.get("log_level", "INFO")
    if log_level not in _LEVELS:
        raise ObsConfigError(
            f"observability.log_level : un de {sorted(_LEVELS)} attendu, obtenu {log_level!r}"
        )
    return ObservabilityConfig(log_level=log_level)
