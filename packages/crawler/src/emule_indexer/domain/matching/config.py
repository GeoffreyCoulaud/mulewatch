"""Modèle de config du moteur de matching — union étiquetée de dataclasses gelées.

Représente la grammaire §8.3 (tokens nommés feuilles + composites, opérandes au
point d'usage, règles). Domaine PUR : aucune I/O, aucun import de bibliothèque YAML.
La construction depuis du YAML parsé (dict/list) est faite par ``validation.py``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

# --- Défs de tokens FEUILLES (4 types, cf. spec §8.2) ---


@dataclass(frozen=True)
class KeywordDef:
    """``{ keyword: "mission titar" }`` — phrase recherchée comme sous-suite contiguë."""

    phrase: str


@dataclass(frozen=True)
class RegexDef:
    """``{ regex: "...", flags: "i" }`` — pattern RE2 interpolé puis compilé par cible."""

    pattern: str
    flags: str = "i"


@dataclass(frozen=True)
class CoverageDef:
    """``{ coverage: title, min: 0.6, fuzz: 0.85 }`` — couverture fuzzy des tokens.

    ``reference`` est le mot-clé de config (``title`` = titre de la cible) ; lié à
    ``target.title`` à la résolution. ``min``/``fuzz`` surchargeables au point d'usage.
    """

    reference: str
    min: float
    fuzz: float = 0.85


@dataclass(frozen=True)
class AttrBetweenDef:
    """``{ attr_between: size_mb, min: 30, max: 600 }`` — borne d'un attribut numérique."""

    attr: str
    min: float | None = None
    max: float | None = None


# --- Défs COMPOSITES / conditions (cf. spec §8.3 : all/any/not) ---
# Réutilisées dans trois contextes : token composite nommé, corps de règle,
# condition inline `{condition}`. Les opérandes mêlent noms nus, TokenRef et
# conditions inline.

# Alias de type PEP 695 (`type ...`), évalué paresseusement : référencer
# TokenRef/AllDef/AnyDef/NotDef — définis plus bas et mutuellement récursifs —
# est donc valide, et mypy --strict résout la récursion.
type Operand = str | TokenRef | AllDef | AnyDef | NotDef


@dataclass(frozen=True)
class AllDef:
    """``all: [operand, ...]`` — conjonction."""

    operands: tuple[Operand, ...]


@dataclass(frozen=True)
class AnyDef:
    """``any: [operand, ...]`` — disjonction."""

    operands: tuple[Operand, ...]


@dataclass(frozen=True)
class NotDef:
    """``not: operand`` — négation d'un unique opérande."""

    operand: Operand


@dataclass(frozen=True)
class TokenRef:
    """Opérande au point d'usage ``{ token: name, min?, fuzz? }`` (cf. EBNF §8.3).

    Référence un token nommé ; ``min``/``fuzz`` non nuls surchargent les paramètres
    d'un token ``coverage`` (et UNIQUEMENT coverage — validé au chargement).
    """

    name: str
    min: float | None = None
    fuzz: float | None = None


# Union des défs de tokens nommables dans la table `tokens`.
TokenDef = KeywordDef | RegexDef | CoverageDef | AttrBetweenDef | AllDef | AnyDef | NotDef

# Un corps de règle / opérande inline `{condition}` est une condition composite.
Condition = AllDef | AnyDef | NotDef

# Ensemble fermé des paliers (cf. spec §8.3 EBNF : tier).
TIERS: frozenset[str] = frozenset({"catalog", "notify", "download"})


@dataclass(frozen=True)
class Rule:
    """Règle ordonnée : ``{ name, tier, <condition> }`` (cf. spec §8.3)."""

    name: str
    tier: str
    condition: Condition


@dataclass(frozen=True)
class MatcherConfig:
    """Config matcher validée : table de tokens nommés + règles ordonnées."""

    tokens: Mapping[str, TokenDef] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()
