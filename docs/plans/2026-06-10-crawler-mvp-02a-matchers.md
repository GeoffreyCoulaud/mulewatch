# Crawler MVP — Plan 2a : Matchers & modèle cible — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implémenter le **domaine pur** du moteur de matching : raffiner la normalisation (casefold + ligatures, ajout de `fold()`), poser le modèle cible (`FileCandidate`, `TargetSegment`), l'interpolation regex (placeholders whitelistés + alternance de dates FR), et les **4 matchers feuilles** (`KeywordMatcher`, `RegexMatcher` RE2, `CoverageMatcher` rapidfuzz, `AttrBetweenMatcher`). Aucune I/O, aucune config, aucune grammaire de règles.

**Architecture:** Couche `domain/` pure (Clean/Hexagonal) — aucune dépendance vers `application`/`ports`/`adapters`. Le moteur de matching est le « joyau » du projet : il est testé en TDD strict avec gate de coverage branch à 100 %. Les 4 matchers exposent une interface homogène `matches(candidate) -> bool` (et `value(candidate) -> float` pour `coverage`). RE2 garantit un matching regex en temps linéaire sur des noms de fichiers (input hostile) ; rapidfuzz fournit un `ratio` Levenshtein déterministe.

**Tech Stack:** Python ≥ 3.12, `uv` (projet/paquets), `ruff` (lint+format), `mypy --strict`, `pytest` + `pytest-cov` (coverage branch, seuil 100 % imposé), `google-re2` (RE2), `rapidfuzz` (fuzzy ratio).

> **Référence spec :** `docs/superpowers/specs/2026-06-10-crawler-mvp-design.md` — §8.1 (normalisation **raffinée** : NFKD → casefold → ligatures `{œ→oe, æ→ae}` ; `fold` vs `norm`), §8.2 (les 4 types de tokens + interpolation regex `{number} {segment} {title} {date_alt}`), §7 (modèle cible : `target_id`, `{number}/{segment}/{title}/{date_alt}`). **§8.3 (grammaire des tokens nommés & règles), §8.4 (DAG/validation), §8.5 (évaluation/engine) appartiennent au Plan 2b et ne sont PAS implémentés ici.** Ce plan suppose le Plan 1 terminé (toolchain, `normalize()`/`tokenize()`, gate coverage).

---

## File Structure

- `pyproject.toml` — **Modify** : `dependencies = ["google-re2", "rapidfuzz"]` (runtime, ajoutées par `uv add` en Task 1).
- `src/emule_indexer/domain/normalization.py` — **Modify** : casefold + ligatures, ajout de `_LIGATURES`, `_common_fold`, `fold`. `normalize`/`tokenize` conservent leur signature.
- `tests/domain/test_normalization.py` — **Modify** : cas `ß→ss`, `œ→oe`, `æ→ae`, tests dédiés de `fold` (ponctuation/casse préservées, accents repliés).
- `src/emule_indexer/domain/matching/__init__.py` — **Create** : docstring du sous-paquet matching.
- `src/emule_indexer/domain/matching/models.py` — **Create** : `FileCandidate`, `TargetSegment` (dataclasses gelées) + `TargetSegment.target_id`.
- `src/emule_indexer/domain/matching/interpolation.py` — **Create** : `FRENCH_MONTHS`, `date_alternation_pattern()`, `interpolate()`, `InterpolationError`. *(Choix : un seul module `interpolation.py` plutôt que deux fichiers `dates.py` + `interpolation.py` — `date_alternation_pattern` n'est utilisé que par `interpolate` via `{date_alt}` ; les garder ensemble évite un import circulaire trivial et respecte YAGNI. Le module reste petit, cohésion forte.)*
- `src/emule_indexer/domain/matching/matchers.py` — **Create** : les **4 matchers feuilles** `KeywordMatcher`, `RegexMatcher`, `CoverageMatcher`, `AttrBetweenMatcher` + `STOPWORDS_FR`, `ATTR_NAMES`. *(Un seul module pour 4 petites classes : cohésion acceptable, pas de sur-découpage. Si la liste grandissait, on scinderait par type.)*
- `tests/domain/matching/__init__.py` — **Create** : paquet de tests du sous-domaine matching.
- `tests/domain/matching/test_models.py` — **Create** : tests `FileCandidate`/`TargetSegment`.
- `tests/domain/matching/test_interpolation.py` — **Create** : tests `date_alternation_pattern`/`interpolate`.
- `tests/domain/matching/test_matchers.py` — **Create** : tests des 4 matchers.

> **Note couverture (gate 100 % branch) :** chaque conditionnel introduit DOIT être exercé des deux côtés par les tests (`if "i" in flags` vrai/faux, `broadcast_date is None` vrai/faux, bornes `min`/`max` None vs définies, `attr` connu/inconnu, référence vide/non vide). Les tâches ci-dessous incluent explicitement ces cas.

> **Note typage (`mypy --strict`) :** annotations complètes partout, **y compris dans les tests** (chaque fonction de test annotée `-> None`, paramètres typés).

> **Décision de design — scanner de placeholders (Task 5) :** les placeholders sont détectés par une **regex stdlib `re`** `\{([a-zA-Z_][a-zA-Z0-9_]*)\}` (et NON `string.Formatter`). Raison : `string.Formatter` interprète un quantificateur regex `{2,4}` comme un champ nommé `2,4`, ce qui casserait l'interpolation de patterns RE2 légitimes. Le scanner regex n'attrape que des identifiants `[a-z_]…`, donc `\d{2,4}` et `a{3}` sont laissés intacts ; seuls `{number}/{segment}/{title}/{date_alt}` sont substitués et tout autre `{identifiant}` lève `InterpolationError`.

---

## Task 1: Dépendances runtime (`google-re2`, `rapidfuzz`)

**Files:**
- Modify: `pyproject.toml` (section `dependencies` — édité automatiquement par `uv add`)
- Modify: `uv.lock` (régénéré par `uv add`)

> **Si `google-re2` ne build/installe pas sur l'hôte** (toolchain C++/abseil manquante au moment du build de la wheel) : **rapporter BLOCKED** avec la sortie d'erreur de `uv add`, ne pas continuer le plan. Le moteur regex RE2 est non négociable (§8.5 : temps linéaire, anti-ReDoS sur input hostile) ; aucun fallback `re` stdlib n'est autorisé.

- [ ] **Step 1: Ajouter les dépendances runtime**

Run: `uv add google-re2 rapidfuzz`
Expected: ajoute `google-re2` et `rapidfuzz` à `[project].dependencies` dans `pyproject.toml`, résout et installe les wheels, met à jour `uv.lock`, code de sortie 0.

- [ ] **Step 2: Vérifier l'import des deux paquets**

Run: `uv run python -c "import re2, rapidfuzz; print('ok')"`
Expected: affiche `ok`, code de sortie 0. *(Le paquet `google-re2` s'importe sous le nom `re2`.)*

- [ ] **Step 3: Vérifier que lint + types + tests existants passent encore**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q`
Expected: aucune erreur ; pytest vert, coverage 100 % (rien de neuf en code de prod, donc le gate reste satisfait).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add google-re2 and rapidfuzz"
```

---

## Task 2: Raffiner la normalisation (casefold + ligatures) + `fold()`

**Files:**
- Modify: `src/emule_indexer/domain/normalization.py`
- Modify: `tests/domain/test_normalization.py`

- [ ] **Step 1: Écrire les tests qui échouent (cas casefold/ligatures + tests de `fold`)**

Remplacer **entièrement** le contenu de `tests/domain/test_normalization.py` par :
```python
import pytest

from emule_indexer.domain.normalization import fold, normalize, tokenize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Télétoon", "teletoon"),  # diacritiques retirés (NFKD)
        ("KERORO", "keroro"),  # casse repliée
        ("café", "cafe"),  # accent composé décomposé puis retiré
        ("N°062A", "n 062a"),  # ° non-alphanumérique -> espace
        ("« Les demoiselles »", "les demoiselles"),  # guillemets -> espaces
        ("a__b  c", "a b c"),  # ponctuation + espaces multiples compactés
        ("  trim  ", "trim"),  # trim des bords
        ("", ""),  # chaîne vide
        ("Straße", "strasse"),  # casefold: ß -> ss
        ("Sœur", "soeur"),  # ligature œ -> oe
        ("Cæsar", "caesar"),  # ligature æ -> ae
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("N°062A.AVI", "n°062a.avi"),  # ponctuation/chiffres préservés, casse repliée
        ("Télétoon", "teletoon"),  # accents repliés sans toucher à la structure
        ("Straße", "strasse"),  # ß -> ss
        ("Sœur", "soeur"),  # œ -> oe
        ("Cæsar", "caesar"),  # æ -> ae
        ("21/09/2008", "21/09/2008"),  # séparateurs de date conservés
    ],
)
def test_fold_preserves_punctuation_and_digits(raw: str, expected: str) -> None:
    assert fold(raw) == expected


def test_tokenize_splits_normalized_string() -> None:
    assert tokenize("N°062A « Les demoiselles »") == ["n", "062a", "les", "demoiselles"]


def test_tokenize_empty_string_yields_no_tokens() -> None:
    assert tokenize("   ") == []
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/test_normalization.py -q`
Expected: FAIL — `ImportError: cannot import name 'fold'` (la fonction `fold` n'existe pas encore).

- [ ] **Step 3: Écrire l'implémentation raffinée**

Remplacer **entièrement** le contenu de `src/emule_indexer/domain/normalization.py` par :
```python
"""Normalisation des chaînes pour le matching (cf. spec §8.1)."""

import unicodedata

# Les DEUX lettres qu'Unicode ne replie jamais via NFKD/casefold (lettres à part
# entière, pas des ligatures de compatibilité comme ﬁ). Table explicite (§8.1).
_LIGATURES = {"œ": "oe", "æ": "ae"}


def _common_fold(value: str) -> str:
    """Repli commun : NFKD -> retrait des diacritiques combinants -> casefold -> ligatures."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    for ligature, replacement in _LIGATURES.items():
        folded = folded.replace(ligature, replacement)
    return folded


def fold(value: str) -> str:
    """Repli commun seul : ponctuation et chiffres PRÉSERVÉS (cf. spec §8.1).

    Utilisé par les tokens ``regex`` (ainsi ``teletoon``/``fevrier`` matchent sans
    classes d'accents, et ``°`` reste pour ``n°062a``).
    """
    return _common_fold(value)


def normalize(value: str) -> str:
    """Replie une chaîne pour le matching keyword/coverage (cf. spec §8.1).

    ``fold`` -> non-alphanumériques convertis en espaces -> espaces compactés -> trim.
    """
    folded = fold(value)
    cleaned = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(cleaned.split())


def tokenize(value: str) -> list[str]:
    """Tokens significatifs d'une chaîne, après normalisation."""
    return normalize(value).split()
```

- [ ] **Step 4: Lancer pour vérifier que tout passe (+ coverage)**

Run: `uv run pytest tests/domain/test_normalization.py -q`
Expected: PASS. La boucle `for ligature, replacement in _LIGATURES.items()` et les deux entrées sont exercées par les cas `Sœur`/`Cæsar` ; toutes les branches de `normalize`/`fold` sont couvertes.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur ruff/mypy.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/normalization.py tests/domain/test_normalization.py
git commit -m "refactor(domain): casefold + ligature folding, add fold()"
```

---

## Task 3: Modèles `FileCandidate` + `TargetSegment`

**Files:**
- Create: `src/emule_indexer/domain/matching/__init__.py`
- Create: `src/emule_indexer/domain/matching/models.py`
- Create: `tests/domain/matching/__init__.py`
- Create: `tests/domain/matching/test_models.py`

- [ ] **Step 1: Créer les `__init__.py`**

`src/emule_indexer/domain/matching/__init__.py` :
```python
"""Moteur de matching (domaine pur) : modèles cibles, interpolation, matchers feuilles."""
```

`tests/domain/matching/__init__.py` : *(fichier vide)*
```python
```

- [ ] **Step 2: Écrire les tests qui échouent**

`tests/domain/matching/test_models.py` :
```python
import datetime

from emule_indexer.domain.matching.models import FileCandidate, TargetSegment


def test_file_candidate_defaults() -> None:
    candidate = FileCandidate(filename="keroro.avi")
    assert candidate.filename == "keroro.avi"
    assert candidate.size_mb is None
    assert candidate.duration_sec is None
    assert candidate.bitrate_kbps is None


def test_file_candidate_with_attributes() -> None:
    candidate = FileCandidate(
        filename="keroro.avi",
        size_mb=120.0,
        duration_sec=1320.0,
        bitrate_kbps=900.0,
    )
    assert candidate.size_mb == 120.0
    assert candidate.duration_sec == 1320.0
    assert candidate.bitrate_kbps == 900.0


def test_target_segment_defaults() -> None:
    target = TargetSegment(season=2, number=62, segment="a", title="Les demoiselles")
    assert target.broadcast_date is None
    assert target.status == "lost"
    assert target.aliases == ()


def test_target_segment_target_id_pads_and_uppercases() -> None:
    target = TargetSegment(season=2, number=62, segment="a", title="Les demoiselles")
    assert target.target_id == "S2E062A"


def test_target_segment_full_fields() -> None:
    target = TargetSegment(
        season=1,
        number=5,
        segment="b",
        title="Le grand combat",
        broadcast_date=datetime.date(2008, 9, 21),
        status="partial",
        aliases=("alt one", "alt two"),
    )
    assert target.target_id == "S1E005B"
    assert target.broadcast_date == datetime.date(2008, 9, 21)
    assert target.status == "partial"
    assert target.aliases == ("alt one", "alt two")
```

- [ ] **Step 3: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.matching.models'`.

- [ ] **Step 4: Écrire l'implémentation**

`src/emule_indexer/domain/matching/models.py` :
```python
"""Modèles du moteur de matching (cf. spec §7, §8)."""

import datetime
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileCandidate:
    """Un fichier observé sur le réseau, candidat au matching.

    ``filename`` est le basename brut observé. Les attributs optionnels sont les
    métadonnées (auto-déclarées, donc non fiables, cf. spec §10.1) utilisées par
    ``attr_between`` ; ``None`` = absent.
    """

    filename: str
    size_mb: float | None = None
    duration_sec: float | None = None
    bitrate_kbps: float | None = None


@dataclass(frozen=True)
class TargetSegment:
    """Un segment d'épisode cible (granularité segment, cf. spec §7).

    Fournit ``{number}``, ``{segment}``, ``{title}`` et ``{date_alt}`` (via
    ``broadcast_date``) à l'interpolation des patterns regex.
    """

    season: int
    number: int
    segment: str
    title: str
    broadcast_date: datetime.date | None = None
    status: str = "lost"
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def target_id(self) -> str:
        """Identifiant stable du segment, ex. ``S2E062A``."""
        return f"S{self.season}E{self.number:03d}{self.segment.upper()}"
```

- [ ] **Step 5: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_models.py -q`
Expected: PASS. Tous les champs et la propriété `target_id` sont exercés (défauts + valeurs explicites + padding/upper).

- [ ] **Step 6: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur.

- [ ] **Step 7: Commit**

```bash
git add src/emule_indexer/domain/matching/__init__.py src/emule_indexer/domain/matching/models.py tests/domain/matching/__init__.py tests/domain/matching/test_models.py
git commit -m "feat(domain): FileCandidate and TargetSegment models"
```

---

## Task 4: `date_alternation_pattern()` + mois français repliés

**Files:**
- Create: `src/emule_indexer/domain/matching/interpolation.py`
- Create: `tests/domain/matching/test_interpolation.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/matching/test_interpolation.py` :
```python
import datetime

import re2

from emule_indexer.domain.matching.interpolation import (
    FRENCH_MONTHS,
    date_alternation_pattern,
)
from emule_indexer.domain.normalization import fold


def test_french_months_are_accent_free_and_complete() -> None:
    assert FRENCH_MONTHS[1] == "janvier"
    assert FRENCH_MONTHS[2] == "fevrier"
    assert FRENCH_MONTHS[8] == "aout"
    assert FRENCH_MONTHS[9] == "septembre"
    assert FRENCH_MONTHS[12] == "decembre"
    assert set(FRENCH_MONTHS) == set(range(1, 13))


def test_date_alternation_matches_known_forms() -> None:
    pattern = date_alternation_pattern(datetime.date(2008, 9, 21))
    compiled = re2.compile(pattern)
    for text in (
        "diffuse le 21 septembre 2008 sur teletoon",
        "keroro 21/09/2008.avi",
        "2008-09-21 keroro.avi",
    ):
        assert compiled.search(fold(text)) is not None


def test_date_alternation_does_not_match_unrelated_date() -> None:
    pattern = date_alternation_pattern(datetime.date(2008, 9, 21))
    compiled = re2.compile(pattern)
    assert compiled.search(fold("2007-01-01 autre chose")) is None
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_interpolation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.matching.interpolation'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/matching/interpolation.py` :
```python
"""Interpolation des patterns regex et alternance de dates (cf. spec §8.2)."""

import datetime

import re2

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
    """Fragment RE2 ``(?:…)`` couvrant les formes usuelles d'une date.

    Couvre : ``21 septembre 2008`` (jour mois-replié année),
    ``21/09/2008`` (jour/mois/année, séparateurs ``/ . -``), et
    ``2008-09-21`` (année/mois/jour). ``0*`` autorise un zéro de tête optionnel
    sur jour et mois.
    """
    day = d.day
    month = d.month
    year = d.year
    month_name = FRENCH_MONTHS[month]
    literal = rf"0*{day}\s+{month_name}\s+{year}"
    dmy = rf"0*{day}[/.\-]0*{month}[/.\-]{year}"
    ymd = rf"{year}[/.\-]0*{month}[/.\-]0*{day}"
    return rf"(?:{literal}|{dmy}|{ymd})"
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_interpolation.py -q`
Expected: PASS. Les trois formes matchent leur date, et une date non liée ne matche pas.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/interpolation.py tests/domain/matching/test_interpolation.py
git commit -m "feat(domain): date_alternation_pattern with folded FR months"
```

---

## Task 5: `interpolate()`

**Files:**
- Modify: `src/emule_indexer/domain/matching/interpolation.py`
- Modify: `tests/domain/matching/test_interpolation.py`

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter à la fin de `tests/domain/matching/test_interpolation.py` (et compléter l'import du haut) :
```python
import pytest

from emule_indexer.domain.matching.interpolation import InterpolationError, interpolate
from emule_indexer.domain.matching.models import TargetSegment


def _target(broadcast_date: datetime.date | None = None) -> TargetSegment:
    return TargetSegment(
        season=2,
        number=62,
        segment="a",
        title="Les demoiselles",
        broadcast_date=broadcast_date,
    )


def test_interpolate_substitutes_number_and_segment_escaped() -> None:
    pattern = r"n[°o]?\s*0*{number}\s*{segment}"
    result = interpolate(pattern, _target())
    assert result == r"n[°o]?\s*0*62\s*A"


def test_interpolate_escapes_regex_special_title() -> None:
    target = TargetSegment(season=1, number=1, segment="a", title="C++ (demo)")
    result = interpolate(r"prefix {title} suffix", target)
    # re2.escape rend le titre littéral : '+', '(', ')' et l'espace sont échappés.
    assert result == r"prefix " + re2.escape("C++ (demo)") + r" suffix"
    # Le fragment échappé compile et matche le texte littéral exact.
    assert re2.compile(result).search("prefix C++ (demo) suffix") is not None


def test_interpolate_date_alt_inserts_raw_fragment() -> None:
    target = _target(broadcast_date=datetime.date(2008, 9, 21))
    result = interpolate(r"{date_alt}", target)
    assert result == date_alternation_pattern(datetime.date(2008, 9, 21))
    assert re2.compile(result).search(fold("21/09/2008")) is not None


def test_interpolate_unknown_placeholder_raises() -> None:
    with pytest.raises(InterpolationError, match="bogus"):
        interpolate(r"a {bogus} b", _target())


def test_interpolate_date_alt_without_date_raises() -> None:
    with pytest.raises(InterpolationError, match="date_alt"):
        interpolate(r"{date_alt}", _target(broadcast_date=None))


def test_interpolate_leaves_regex_quantifier_braces_untouched() -> None:
    # Un quantificateur RE2 {2,4} n'est PAS un placeholder et reste intact.
    pattern = r"keroro\d{2,4}{number}"
    assert interpolate(pattern, _target()) == r"keroro\d{2,4}62"
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_interpolation.py -q`
Expected: FAIL — `ImportError: cannot import name 'InterpolationError'` (et `interpolate` indisponible).

- [ ] **Step 3: Écrire l'implémentation**

Ajouter en tête de `src/emule_indexer/domain/matching/interpolation.py`, après les imports existants :
```python
import re as _re

from emule_indexer.domain.matching.models import TargetSegment
```

Puis ajouter à la fin du fichier `src/emule_indexer/domain/matching/interpolation.py` :
```python
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
            return re2.escape(str(target.number))
        if name == "segment":
            return re2.escape(target.segment.upper())
        if name == "title":
            return re2.escape(target.title)
        if name == "date_alt":
            if target.broadcast_date is None:
                raise InterpolationError(
                    "placeholder {date_alt} requiert un broadcast_date non nul"
                )
            return date_alternation_pattern(target.broadcast_date)
        raise InterpolationError(f"placeholder inconnu : {{{name}}}")

    return _PLACEHOLDER.sub(replace, pattern)
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_interpolation.py -q`
Expected: PASS. Les branches `number`/`segment`/`title`/`date_alt`, la branche `broadcast_date is None`, le placeholder inconnu et le cas « quantificateur intact » sont tous exercés → toutes les branches de `replace` couvertes.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/interpolation.py tests/domain/matching/test_interpolation.py
git commit -m "feat(domain): regex interpolation of target fields"
```

---

## Task 6: `KeywordMatcher`

**Files:**
- Create: `src/emule_indexer/domain/matching/matchers.py`
- Create: `tests/domain/matching/test_matchers.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/matching/test_matchers.py` :
```python
from emule_indexer.domain.matching.matchers import KeywordMatcher
from emule_indexer.domain.matching.models import FileCandidate


def test_keyword_single_word_present() -> None:
    matcher = KeywordMatcher("keroro")
    assert matcher.matches(FileCandidate(filename="Keroro 062A.avi")) is True


def test_keyword_single_word_absent() -> None:
    matcher = KeywordMatcher("titar")
    assert matcher.matches(FileCandidate(filename="Keroro 062A.avi")) is False


def test_keyword_multiword_contiguous_present() -> None:
    matcher = KeywordMatcher("mission titar")
    candidate = FileCandidate(filename="Keroro Mission Titar 062A.avi")
    assert matcher.matches(candidate) is True


def test_keyword_multiword_non_contiguous_absent() -> None:
    matcher = KeywordMatcher("mission titar")
    candidate = FileCandidate(filename="mission keroro titar.avi")
    assert matcher.matches(candidate) is False


def test_keyword_accent_and_case_insensitive_via_tokenize() -> None:
    matcher = KeywordMatcher("teletoon")
    assert matcher.matches(FileCandidate(filename="Keroro TÉLÉTOON.avi")) is True
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_matchers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.matching.matchers'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/matching/matchers.py` :
```python
"""Matchers feuilles du moteur de matching (cf. spec §8.2)."""

from emule_indexer.domain.matching.models import FileCandidate
from emule_indexer.domain.normalization import tokenize


class KeywordMatcher:
    """Vrai si la phrase (tokenisée) est une sous-suite CONTIGUË des tokens du nom."""

    def __init__(self, phrase: str) -> None:
        self._tokens = tokenize(phrase)

    def matches(self, candidate: FileCandidate) -> bool:
        needle = self._tokens
        haystack = tokenize(candidate.filename)
        if not needle:
            return True
        last_start = len(haystack) - len(needle)
        for start in range(last_start + 1):
            if haystack[start : start + len(needle)] == needle:
                return True
        return False
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_matchers.py -q`
Expected: PASS. Les cas présent/absent (simple et multi-mots), contigu/non-contigu, et accent/casse couvrent la boucle (match trouvé / boucle épuisée).

> **Note couverture :** la branche `if not needle: return True` (phrase vide) n'est pas exercée par les tests ci-dessus. La phrase vient toujours d'une config réelle non vide dans le moteur, mais le gate branch l'exige. **Ajouter ce test** dans `tests/domain/matching/test_matchers.py` :
> ```python
> def test_keyword_empty_phrase_matches_anything() -> None:
>     matcher = KeywordMatcher("")
>     assert matcher.matches(FileCandidate(filename="whatever.avi")) is True
> ```

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/matchers.py tests/domain/matching/test_matchers.py
git commit -m "feat(domain): KeywordMatcher"
```

---

## Task 7: `RegexMatcher` (RE2)

**Files:**
- Modify: `src/emule_indexer/domain/matching/matchers.py`
- Modify: `tests/domain/matching/test_matchers.py`

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter à la fin de `tests/domain/matching/test_matchers.py` (et compléter l'import `matchers` du haut pour inclure `RegexMatcher`) :
```python
from emule_indexer.domain.matching.matchers import RegexMatcher


def test_regex_literal_matches_case_and_accent_insensitive() -> None:
    # Le pattern littéral "teletoon" matche "Télétoon" grâce à fold(raw).
    matcher = RegexMatcher("teletoon")
    assert matcher.matches(FileCandidate(filename="Keroro Télétoon.avi")) is True


def test_regex_segment_id_style_pattern() -> None:
    matcher = RegexMatcher(r"n[°o]?\s*0*62\s*a")
    assert matcher.matches(FileCandidate(filename="Keroro N°062A.avi")) is True


def test_regex_no_match_returns_false() -> None:
    matcher = RegexMatcher("teletoon")
    assert matcher.matches(FileCandidate(filename="autre fichier.mkv")) is False


def test_regex_without_i_flag_is_case_sensitive() -> None:
    # fold() minusculise déjà ; un pattern en MAJUSCULES sans (?i) ne matche pas.
    matcher = RegexMatcher("TELETOON", flags="")
    assert matcher.matches(FileCandidate(filename="Keroro Télétoon.avi")) is False
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_matchers.py -q`
Expected: FAIL — `ImportError: cannot import name 'RegexMatcher'`.

- [ ] **Step 3: Écrire l'implémentation**

Ajouter en tête de `src/emule_indexer/domain/matching/matchers.py`, après les imports existants :
```python
import re2

from emule_indexer.domain.normalization import fold
```

Puis ajouter à la fin du fichier `src/emule_indexer/domain/matching/matchers.py` :
```python
class RegexMatcher:
    """Match RE2 sur ``fold(filename)``. Si ``"i"`` dans ``flags``, préfixe ``(?i)``.

    On préfixe explicitement ``(?i)`` au pattern plutôt que de s'appuyer sur des
    constantes de flag RE2 (portabilité de l'API ``re2``).
    """

    def __init__(self, pattern: str, flags: str = "i") -> None:
        if "i" in flags:
            pattern = "(?i)" + pattern
        self._re = re2.compile(pattern)

    def matches(self, candidate: FileCandidate) -> bool:
        return self._re.search(fold(candidate.filename)) is not None
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_matchers.py -q`
Expected: PASS. Les deux côtés de `if "i" in flags` sont exercés (défaut `"i"` dans les 3 premiers tests ; `flags=""` dans le 4e), ainsi que match/non-match de `search`.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/matchers.py tests/domain/matching/test_matchers.py
git commit -m "feat(domain): RegexMatcher (RE2)"
```

---

## Task 8: `CoverageMatcher` (rapidfuzz)

**Files:**
- Modify: `src/emule_indexer/domain/matching/matchers.py`
- Modify: `tests/domain/matching/test_matchers.py`

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter à la fin de `tests/domain/matching/test_matchers.py` (et compléter l'import `matchers` du haut pour inclure `CoverageMatcher`) :
```python
from emule_indexer.domain.matching.matchers import CoverageMatcher


def test_coverage_exact_title_is_one_and_matches() -> None:
    matcher = CoverageMatcher("Les demoiselles cambrioleuses", min=0.6)
    candidate = FileCandidate(filename="Keroro 062A Les demoiselles cambrioleuses.avi")
    assert matcher.value(candidate) == 1.0
    assert matcher.matches(candidate) is True


def test_coverage_one_typo_within_fuzz_still_matches() -> None:
    matcher = CoverageMatcher("Les demoiselles cambrioleuses", min=0.6)
    # "demoiseles" (un 'l' manquant) reste >= fuzz 0.85 vs "demoiselles".
    candidate = FileCandidate(filename="demoiseles cambrioleuses.avi")
    assert matcher.value(candidate) == 1.0
    assert matcher.matches(candidate) is True


def test_coverage_unrelated_is_zero_and_no_match() -> None:
    matcher = CoverageMatcher("Les demoiselles cambrioleuses", min=0.6)
    candidate = FileCandidate(filename="totalement autre chose.mkv")
    assert matcher.value(candidate) == 0.0
    assert matcher.matches(candidate) is False


def test_coverage_empty_reference_is_zero() -> None:
    # Référence faite uniquement de stopwords -> aucun token significatif -> 0.0.
    matcher = CoverageMatcher("les des un une", min=0.6)
    candidate = FileCandidate(filename="les demoiselles.avi")
    assert matcher.value(candidate) == 0.0
    assert matcher.matches(candidate) is False
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_matchers.py -q`
Expected: FAIL — `ImportError: cannot import name 'CoverageMatcher'`.

- [ ] **Step 3: Écrire l'implémentation**

Ajouter en tête de `src/emule_indexer/domain/matching/matchers.py`, après les imports existants :
```python
from rapidfuzz import fuzz
```

Puis ajouter à la fin du fichier `src/emule_indexer/domain/matching/matchers.py` :
```python
# Mots-vides français (déjà repliés par tokenize) exclus des tokens significatifs
# de la référence d'un CoverageMatcher (cf. spec §8.2 : R = tokens(title) \ stopwords).
STOPWORDS_FR: frozenset[str] = frozenset(
    {
        "le",
        "la",
        "les",
        "l",
        "de",
        "des",
        "du",
        "d",
        "un",
        "une",
        "et",
        "a",
        "au",
        "aux",
        "en",
    }
)


class CoverageMatcher:
    """Fraction fuzzy des tokens significatifs de ``reference`` couverts (cf. spec §8.2)."""

    def __init__(self, reference: str, min: float, fuzz: float = 0.85) -> None:
        self._reference_tokens = [t for t in tokenize(reference) if t not in STOPWORDS_FR]
        self._min = min
        self._fuzz = fuzz

    def value(self, candidate: FileCandidate) -> float:
        reference_tokens = self._reference_tokens
        if not reference_tokens:
            return 0.0
        candidate_tokens = tokenize(candidate.filename)
        hits = sum(
            1
            for r in reference_tokens
            if any(fuzz.ratio(r, f) / 100 >= self._fuzz for f in candidate_tokens)
        )
        return hits / len(reference_tokens)

    def matches(self, candidate: FileCandidate) -> bool:
        return self.value(candidate) >= self._min
```

> **Note nommage :** le paramètre `fuzz` (seuil flottant) masque le module `fuzz` importé *dans la portée de `__init__`*. C'est intentionnel et sans danger car `__init__` n'appelle pas `fuzz.ratio` (seul `value()`, qui ne shadow pas, l'appelle). La spec impose le nom de paramètre `fuzz` ; on le conserve. *(Si ruff signale un shadowing — il ne devrait pas avec le ruleset `E,F,I,UP,B,SIM` —, renommer l'import en `from rapidfuzz import fuzz as _fuzz` et adapter `value()`.)*

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_matchers.py -q`
Expected: PASS. Les deux côtés de `if not reference_tokens` sont exercés (référence significative vs référence 100 % stopwords) ; `value` exact (1.0), avec typo (1.0), non lié (0.0) ; `matches` vrai/faux des deux côtés du seuil.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/matchers.py tests/domain/matching/test_matchers.py
git commit -m "feat(domain): CoverageMatcher (rapidfuzz)"
```

---

## Task 9: `AttrBetweenMatcher`

**Files:**
- Modify: `src/emule_indexer/domain/matching/matchers.py`
- Modify: `tests/domain/matching/test_matchers.py`

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter à la fin de `tests/domain/matching/test_matchers.py` (et compléter l'import `matchers` du haut pour inclure `AttrBetweenMatcher`) :
```python
import pytest

from emule_indexer.domain.matching.matchers import AttrBetweenMatcher


def test_attr_between_unknown_attr_raises() -> None:
    with pytest.raises(ValueError, match="codec"):
        AttrBetweenMatcher("codec", min=1.0)


def test_attr_between_absent_value_is_false() -> None:
    matcher = AttrBetweenMatcher("size_mb", min=30.0, max=600.0)
    assert matcher.matches(FileCandidate(filename="x.avi")) is False


def test_attr_between_in_range_is_true() -> None:
    matcher = AttrBetweenMatcher("size_mb", min=30.0, max=600.0)
    assert matcher.matches(FileCandidate(filename="x.avi", size_mb=120.0)) is True


def test_attr_between_below_min_is_false() -> None:
    matcher = AttrBetweenMatcher("size_mb", min=30.0, max=600.0)
    assert matcher.matches(FileCandidate(filename="x.avi", size_mb=10.0)) is False


def test_attr_between_above_max_is_false() -> None:
    matcher = AttrBetweenMatcher("size_mb", min=30.0, max=600.0)
    assert matcher.matches(FileCandidate(filename="x.avi", size_mb=900.0)) is False


def test_attr_between_open_lower_bound() -> None:
    matcher = AttrBetweenMatcher("duration_sec", max=1800.0)
    assert matcher.matches(FileCandidate(filename="x.avi", duration_sec=10.0)) is True
    assert matcher.matches(FileCandidate(filename="x.avi", duration_sec=2000.0)) is False


def test_attr_between_open_upper_bound() -> None:
    matcher = AttrBetweenMatcher("bitrate_kbps", min=500.0)
    assert matcher.matches(FileCandidate(filename="x.avi", bitrate_kbps=900.0)) is True
    assert matcher.matches(FileCandidate(filename="x.avi", bitrate_kbps=100.0)) is False


def test_attr_between_no_bounds_accepts_any_present_value() -> None:
    matcher = AttrBetweenMatcher("size_mb")
    assert matcher.matches(FileCandidate(filename="x.avi", size_mb=1.0)) is True
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_matchers.py -q`
Expected: FAIL — `ImportError: cannot import name 'AttrBetweenMatcher'`.

- [ ] **Step 3: Écrire l'implémentation**

Ajouter à la fin du fichier `src/emule_indexer/domain/matching/matchers.py` :
```python
# Enum fermé des attributs numériques de FileCandidate utilisables par attr_between
# (cf. spec §8.2). Tout autre nom -> erreur.
ATTR_NAMES: frozenset[str] = frozenset({"size_mb", "duration_sec", "bitrate_kbps"})


class AttrBetweenMatcher:
    """Vrai si l'attribut numérique est PRÉSENT et dans ``[min, max]`` (cf. spec §8.2).

    Bornes ouvertes quand ``min``/``max`` valent ``None``. Attribut absent -> faux.
    """

    def __init__(
        self,
        attr: str,
        min: float | None = None,
        max: float | None = None,
    ) -> None:
        if attr not in ATTR_NAMES:
            raise ValueError(f"attribut inconnu : {attr!r} (attendu l'un de {sorted(ATTR_NAMES)})")
        self._attr = attr
        self._min = min
        self._max = max

    def matches(self, candidate: FileCandidate) -> bool:
        value: float | None = getattr(candidate, self._attr)
        if value is None:
            return False
        if self._min is not None and value < self._min:
            return False
        if self._max is not None and value > self._max:
            return False
        return True
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_matchers.py -q`
Expected: PASS. Les branches couvertes : `attr` inconnu (raise) / connu ; `value is None` / non ; `self._min is not None` vrai (en/sous-borne) / `None` (borne ouverte) ; idem `self._max`.

- [ ] **Step 5: Vérifier la suite complète + types + lint + coverage final**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tous les tests verts ; **coverage 100 % (branch)** sur tout `emule_indexer` ; aucune erreur ruff/mypy.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/matchers.py tests/domain/matching/test_matchers.py
git commit -m "feat(domain): AttrBetweenMatcher"
```

---

## Self-Review (rempli par l'auteur du plan)

- **Couverture de la spec (périmètre Plan 2a)** :
  - §8.1 normalisation raffinée (NFKD → casefold → ligatures `{œ→oe, æ→ae}`, `fold` ponctuation/chiffres préservés, `norm`/`tokens`) → **Task 2** ✓ (cas `ß→ss`, `œ→oe`, `æ→ae`, `fold("N°062A.AVI")=="n°062a.avi"`, `fold("Télétoon")=="teletoon"`, anciens cas `normalize` conservés).
  - §7 modèle cible (`target_id` `S2E062A`, fournit `{number}/{segment}/{title}/{date_alt}`) → **Task 3** (`TargetSegment.target_id`, padding `{number:03d}`, `segment.upper()`) ✓ ; `FileCandidate` (attributs `size_mb/duration_sec/bitrate_kbps` optionnels) ✓.
  - §8.2 interpolation regex (whitelist `{number} {segment} {title} {date_alt}`, escaping des littéraux, `{date_alt}` brut, placeholder inconnu → erreur, date manquante → erreur) → **Tasks 4-5** ✓ ; mois FR repliés + `date_alternation_pattern` (3 formes + zéro de tête `0*`) → **Task 4** ✓.
  - §8.2 les 4 types de tokens → **Tasks 6-9** : `keyword` (sous-suite contiguë sur `tokens`) ✓ ; `regex` (RE2 sur `fold(raw)`, flag `i` → préfixe `(?i)`) ✓ ; `coverage` (fraction fuzzy rapidfuzz, `R = tokens \ stopwords`, `value` + `min`) ✓ ; `attr_between` (enum fermé, absent→faux, bornes ouvertes) ✓.
  - **Hors périmètre, explicitement renvoyé au Plan 2b** : §8.3 grammaire/tokens nommés/composites/règles ; §8.4 DAG/validation/profondeur ; §8.5 évaluation/engine/golden corpus. **Aucun de ces éléments n'apparaît dans ce plan.** ✓
- **Scan des placeholders (« TBD »/« similaire à »/« etc. »)** : aucun. Chaque step de code contient le code COMPLET (imports inclus), chaque step de run a une commande exacte + sortie attendue, chaque tâche se termine par un `git commit -m` exact. Les seuls `…` présents sont dans des chaînes/regex de prose explicative, pas dans du code à recopier.
- **Cohérence des types/nommage entre tâches** :
  - `fold(value: str) -> str`, `normalize(value: str) -> str`, `tokenize(value: str) -> list[str]` — signatures stables, réutilisées par `KeywordMatcher`/`RegexMatcher`/`CoverageMatcher`/`interpolation` de façon cohérente.
  - `FileCandidate(filename, size_mb, duration_sec, bitrate_kbps)` et `TargetSegment(season, number, segment, title, broadcast_date, status, aliases)` — noms de champs identiques partout (tests `attr_between` lisent bien `size_mb/duration_sec/bitrate_kbps` ; `interpolate` lit `target.number/segment/title/broadcast_date`).
  - Interface matcher homogène : `matches(candidate: FileCandidate) -> bool` sur les 4 ; `value(candidate: FileCandidate) -> float` en plus sur `CoverageMatcher` (cohérent avec §8.2 « renvoie `value` »).
  - `InterpolationError` (custom) levée pour placeholder inconnu ET date manquante ; `ValueError` pour `attr` hors `ATTR_NAMES` (cohérent avec « erreur de validation au chargement » côté domaine pur — la validation config-niveau du Plan 2b s'appuiera dessus).
  - `re2.escape`/`re2.compile` utilisés uniformément ; `(?i)` préfixé manuellement (pas de constante de flag RE2). `fuzz.ratio(...)/100` comparé à `fuzz` seuil (`/100` car rapidfuzz renvoie 0-100).
- **Ambiguïtés résolues** :
  1. **Scanner de placeholders** : choix d'une regex `\{[a-zA-Z_]\w*\}` au lieu de `string.Formatter`, car ce dernier confond les quantificateurs RE2 `{2,4}` avec des champs nommés (vérifié empiriquement). Documenté dans File Structure + testé (`test_interpolate_leaves_regex_quantifier_braces_untouched`).
  2. **`{segment}` casse** : interpolé en `segment.upper()` (cohérent avec `target_id` `…A`) ; le pattern RE2 portant `(?i)` côté `RegexMatcher`, la casse de l'insertion n'empêche pas le match insensible.
  3. **`fold` ne touche pas la casse du `{title}` escapé** : `re2.escape("C++ (demo)")` garde le `C` majuscule (vérifié) ; le matching réel passe par `(?i)` dans `RegexMatcher`. Le test d'interpolation valide donc l'égalité de chaîne + un match sur texte à casse exacte (pas via `fold`) pour rester déterministe.
  4. **Fichier unique `interpolation.py`** (vs `dates.py` séparé) et **`matchers.py` unique** pour les 4 matchers : justifié (cohésion, YAGNI) dans File Structure.
  5. **Shadowing `fuzz`** (paramètre vs module) dans `CoverageMatcher.__init__` : intentionnel (nom imposé par la spec), inoffensif car `ratio` n'est appelé que dans `value()` ; note de repli fournie si ruff/mypy s'en plaignait.
- **Gate coverage 100 % branch** : chaque conditionnel introduit est testé des deux côtés — `if "i" in flags` (Task 7), `broadcast_date is None` (Task 5), `if not needle` keyword vide (Task 6, test ajouté en note), `if not reference_tokens` (Task 8), `value is None` + `min is not None` + `max is not None` (Task 9), boucle ligatures (Task 2). ✓
- **Faits vérifiés empiriquement avant rédaction** (venv jetable avec `google-re2`+`rapidfuzz`) : `fold("N°062A.AVI")=="n°062a.avi"`, `normalize("Straße")=="strasse"`, ligatures `œ/æ` ; `re2.escape` échappe `+ ( ) `(espace) ; `(?i)` fonctionne en préfixe ; `date_alternation_pattern(date(2008,9,21))` matche `21 septembre 2008` / `21/09/2008` / `2008-09-21` sur `fold(...)` et PAS `2007-01-01` ; `fuzz.ratio("demoiseles","demoiselles")/100 ≈ 0.95 ≥ 0.85`, exact = 1.0, non lié = 0.0 ; scanner regex laisse `\d{2,4}` intact. **(`google-re2` s'installe sur l'hôte — Task 1 ne devrait pas BLOCKER.)**
```
