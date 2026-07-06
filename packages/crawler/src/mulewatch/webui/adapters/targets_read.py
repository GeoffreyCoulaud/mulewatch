"""Load targets from a YAML file (spec W-D7 / Task 9).

``load_targets`` reads ``targets.yaml``, minimally validates its surface
structure (mapping expected at the root), then delegates full validation to
``catalog_matching.validation.parse_targets``.

The I/O (``yaml.safe_load``) is here; ``parse_targets`` is pure domain.
"""

from pathlib import Path

import yaml

from catalog_matching.models import TargetSegment
from catalog_matching.validation import ConfigError, parse_targets


def load_targets(path: Path) -> tuple[TargetSegment, ...]:
    """Read ``path`` (YAML) and return the tuple of validated :class:`TargetSegment`.

    Raises:
        OSError: if the file is unreadable or nonexistent.
        ConfigError: if the YAML root is not a mapping or if ``parse_targets``
            detects a schema/semantic error.
    """
    raw_text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text)
    if raw is None:
        raise ConfigError(f"{path}: empty YAML file, mapping expected")
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: invalid YAML root — mapping expected, got {type(raw).__name__}")
    return parse_targets(raw)
