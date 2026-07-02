"""Regex pattern interpolation (cf. spec §8.2)."""

import re as _re

import re2

from catalog_matching.models import TargetSegment

# Detects ONLY identifier placeholders ``{name}``; a regex quantifier like
# ``{2,4}`` or ``{3}`` is not an identifier and is left untouched.
_PLACEHOLDER = _re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class InterpolationError(Exception):
    """Interpolation error: unknown placeholder."""


def interpolate(pattern: str, target: TargetSegment) -> str:
    """Substitutes the whitelist ``{season} {seasonal_number} {absolute_number} {segment}
    {title} {mono_gate}``.

    All values are inserted ``re2.escape``-d (literal), EXCEPT ``{mono_gate}`` which
    injects a **raw** (unescaped) regex fragment: ``""`` if ``target.sole_segment`` (the
    target has a single segment), otherwise ``[^\\s\\S]`` (empty RE2 class, never-match —
    neutralizes the carrier token for bi-segment targets). Any other placeholder raises
    :class:`InterpolationError`.
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
        raise InterpolationError(f"unknown placeholder: {{{name}}}")

    return _PLACEHOLDER.sub(replace, pattern)
