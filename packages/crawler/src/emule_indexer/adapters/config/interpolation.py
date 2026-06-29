"""Interpolation ${NAME} (sous-chaîne, fail-fast) — I/O env, donc dans l'adapter (spec D1/D3)."""

import re
from collections.abc import Mapping

from emule_indexer.adapters.config.errors import ConfigError

_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def interpolate(value: str, env: Mapping[str, str], what: str) -> str:
    """Substitue chaque ``${NAME}`` de ``value`` par ``env[NAME]``.

    ``what`` nomme le champ pour l'erreur. Variable absente ⇒ ``ConfigError`` (spec D1).
    Aucun motif ⇒ ``value`` renvoyé tel quel (les ``$`` isolés ne sont pas touchés).
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in env:
            raise ConfigError(f"{what} : variable d'environnement {name!r} référencée mais absente")
        return env[name]

    return _PATTERN.sub(_replace, value)
