"""Interpolation des patterns regex (cf. spec §8.2)."""

import re as _re

import re2

from catalog_matching.models import TargetSegment

# Détecte UNIQUEMENT des placeholders identifiants ``{nom}`` ; un quantificateur
# regex comme ``{2,4}`` ou ``{3}`` n'est pas un identifiant et est laissé intact.
_PLACEHOLDER = _re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class InterpolationError(Exception):
    """Erreur d'interpolation : placeholder inconnu."""


def interpolate(pattern: str, target: TargetSegment) -> str:
    """Substitue la whitelist ``{season} {seasonal_number} {absolute_number} {segment}
    {title} {mono_gate}``.

    Toutes les valeurs sont insérées ``re2.escape``-ées (littérales), SAUF
    ``{mono_gate}`` qui injecte un fragment regex **brut** (non échappé) : ``""`` si
    ``target.sole_segment`` (la cible n'a qu'un segment), sinon ``[^\\s\\S]`` (classe
    vide RE2, never-match — neutralise le token porteur pour les cibles bi-segment).
    Tout autre placeholder lève :class:`InterpolationError`.
    """

    def replace(match: "_re.Match[str]") -> str:
        name = match.group(1)
        if name == "season":
            return str(re2.escape(str(target.season)))
        if name == "seasonal_number":
            return str(re2.escape(str(target.seasonal_number)))
        if name == "absolute_number":
            return str(re2.escape(str(target.absolute_number)))
        if name == "segment":
            return str(re2.escape(target.segment.upper()))
        if name == "title":
            return str(re2.escape(target.title))
        if name == "mono_gate":
            return "" if target.sole_segment else r"[^\s\S]"
        raise InterpolationError(f"placeholder inconnu : {{{name}}}")

    return _PLACEHOLDER.sub(replace, pattern)
