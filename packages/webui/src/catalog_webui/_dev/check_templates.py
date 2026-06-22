"""Garde « templates Jinja sans logique » — approche par match de tokens.

Règle : un template ne doit contenir que des constructions passives.

Autorisé
--------
- Balises de structure : {% extends %} {% block %} {% endblock %} {% include %}
                         {% for %} {% else %} {% endfor %}
- Interpolation simple : {{ x }}  {{ x.attr }}  {{ x.attr.sub }}
  (identifiant + accès attribut via `.`, sans opérateur ni appel)

Rejeté
------
- Balises de logique     : {% if … %}  {% elif … %}  {% set … %}  {% macro … %}
- Expressions calculées  : toute expression {{ … }} contenant :
    · un opérateur arithmétique ou de comparaison : + - * / % == != < >
    · un filtre                                   : |
    · un appel de fonction                        : (

Regex utilisées
---------------
1. Balise interdite  : ``r"\\{%-?\\s*(if|elif|set|macro)\\b"``
   (détecte ``{% if``, ``{%- if``, ``{% set``, etc.)

2. Expression illégale : dans ``{{ … }}``, présence de l'un des chars ``+ - * / % = ! < > | (``
   (on ne distingue pas `!=` de `!` seul — suffisant, pas de `!` légitime en Jinja).
   Regex : ``r"\\{\\{[^}]*[+\\-*/%=!<>|(][^}]*\\}\\}"``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- Patterns interdits ---

# Balises {% if %} / {% elif %} / {% set %} / {% macro %}
_FORBIDDEN_TAG: re.Pattern[str] = re.compile(r"\{%-?\s*(if|elif|set|macro)\b")

# Expression {{ … }} contenant un opérateur ou un appel
_FORBIDDEN_EXPR: re.Pattern[str] = re.compile(r"\{\{[^}]*[+\-*/%=!<>|(][^}]*\}\}")


def _check_file(path: Path) -> list[str]:
    """Retourne les violations trouvées dans *path* (liste vide = conforme)."""
    text = path.read_text(encoding="utf-8")
    violations: list[str] = []

    for m in _FORBIDDEN_TAG.finditer(text):
        keyword = m.group(1)
        violations.append(f"{path.name}: balise interdite '{{% {keyword} %}}'")

    for m in _FORBIDDEN_EXPR.finditer(text):
        snippet = m.group(0)[:60]
        violations.append(f"{path.name}: expression calculée '{snippet}'")

    return violations


def find_logic_violations(directory: Path) -> list[str]:
    """Scanne récursivement *directory*, retourne toutes les violations trouvées.

    Chaque entrée est de la forme ``"<fichier>: <raison>"``.
    """
    violations: list[str] = []
    for html_file in sorted(directory.rglob("*.html")):
        violations.extend(_check_file(html_file))
    return violations


def main() -> None:
    """Point d'entrée CLI : ``python -m catalog_webui._dev.check_templates <directory>``."""
    directory = Path(sys.argv[1])
    violations = find_logic_violations(directory)
    if violations:
        for v in violations:
            print(v)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()  # pragma: no cover
