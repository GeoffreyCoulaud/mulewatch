"""Interpolation des patterns regex et alternance de dates (cf. spec §8.2)."""

import datetime
import re as _re

import re2

from emule_indexer.domain.matching.models import TargetSegment

# Noms de mois français SANS accent (déjà repliés) : les patterns sont matchés
# contre fold(raw), qui retire les diacritiques. Ainsi "fevrier" matche "février".
FRENCH_MONTHS: dict[int, str] = {
    1: "janvier",
    2: "fevrier",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "aout",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "decembre",
}


def date_alternation_pattern(d: datetime.date) -> str:
    r"""Fragment RE2 ``(?:…)`` couvrant les formes usuelles d'une date.

    Couvre ``21 septembre 2008`` (jour mois-replié année), ``21/09/2008``
    (jour/mois/année) et ``2008-09-21`` (année/mois/jour). ``0*`` tolère un
    préfixe de zéros (un ou plusieurs) sur jour et mois.

    Bords numériques gardés par ``(?:^|[^0-9])`` / ``(?:[^0-9]|$)`` : un
    chiffre voisin est rejeté (le jour ``5`` ne matche pas dans
    ``15/09/2008``), mais un séparateur de release courant (``_``, lettre,
    ``.``) ou un bord de chaîne reste accepté (``keroro_21/09/2008`` matche).
    RE2 n'ayant pas de lookaround, on consomme un caractère voisin — sans
    effet pour une recherche booléenne ``search()``.

    Les séparateurs internes ``[/.\-]`` peuvent différer entre eux (filet
    large voulu ; RE2 sans backreference ne peut les contraindre identiques).
    """
    day = d.day
    month = d.month
    year = d.year
    month_name = FRENCH_MONTHS[month]
    head = r"(?:^|[^0-9])"
    tail = r"(?:[^0-9]|$)"
    literal = rf"{head}0*{day}\s+{month_name}\s+{year}{tail}"
    dmy = rf"{head}0*{day}[/.\-]0*{month}[/.\-]{year}{tail}"
    ymd = rf"{head}{year}[/.\-]0*{month}[/.\-]0*{day}{tail}"
    return rf"(?:{literal}|{dmy}|{ymd})"


# Détecte UNIQUEMENT des placeholders identifiants ``{nom}`` ; un quantificateur
# regex comme ``{2,4}`` ou ``{3}`` n'est pas un identifiant et est laissé intact.
_PLACEHOLDER = _re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class InterpolationError(Exception):
    """Erreur d'interpolation : placeholder inconnu ou ``{date_alt}`` sans date."""


def interpolate(pattern: str, target: TargetSegment) -> str:
    """Substitue la whitelist ``{number} {segment} {title} {date_alt}`` (cf. spec §8.2).

    ``{number}``/``{segment}``/``{title}`` sont insérés ``re2.escape``-és (littéraux) ;
    ``{date_alt}`` est inséré comme fragment regex BRUT (``date_alternation_pattern``).
    Tout autre placeholder lève :class:`InterpolationError`. ``{date_alt}`` alors que
    ``target.broadcast_date is None`` lève aussi :class:`InterpolationError`.
    """

    def replace(match: "_re.Match[str]") -> str:
        name = match.group(1)
        if name == "number":
            return str(re2.escape(str(target.number)))
        if name == "segment":
            return str(re2.escape(target.segment.upper()))
        if name == "title":
            return str(re2.escape(target.title))
        if name == "date_alt":
            if target.broadcast_date is None:
                raise InterpolationError(
                    "placeholder {date_alt} requiert un broadcast_date non nul"
                )
            return date_alternation_pattern(target.broadcast_date)
        raise InterpolationError(f"placeholder inconnu : {{{name}}}")

    return _PLACEHOLDER.sub(replace, pattern)
