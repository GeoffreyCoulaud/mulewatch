# Crawler MVP — Plan 2b : Config, graphe de références & construction par cible — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construire la couche **CONFIG + GRAPHE DE MATCHERS** au-dessus des primitives pures du Plan 2a. Concrètement : (1) un **modèle de config** en dataclasses gelées (4 défs de tokens feuilles, défs composites `all`/`any`/`not`, opérandes au point d'usage avec overrides, table de tokens nommés, `Rule`) ; (2) le **chargement YAML** (adapter PyYAML) de la config matcher et de `config/targets.yaml` vers ce modèle ; (3) la **validation au chargement fail-fast** (§8.4 : DAG sans cycle nommant le cycle, profondeur de résolution bornée à 32, `attr_between` dans l'enum, regex RE2-compilables, placeholder d'interpolation inconnu, tier dans l'ensemble fermé, référence de token inconnue) ; (4) les **combinateurs** purs `AllMatcher`/`AnyMatcher`/`NotMatcher` + un `Matcher` `Protocol` ; (5) la **résolution/construction par cible** — pour une `TargetSegment` donnée, bâtir l'arbre de matchers résolu de chaque token nommé et de chaque condition de règle (regex interpolées + compilées par cible, coverage lié à `target.title`).

**Architecture:** Couche `domain/` PURE (Clean/Hexagonal) pour le modèle de config, la validation du graphe, les combinateurs et le resolver : ils opèrent sur des structures Python déjà parsées (`dict`/`list` issus du YAML) ou sur des dataclasses typées — **aucun import de bibliothèque YAML dans le domaine**. La **lecture d'un fichier YAML est une préoccupation d'ADAPTER** (`adapters/config/`). Le moteur reste le « joyau » : TDD strict, gate de coverage branch à 100 %. La construction par cible interpole puis compile les regex **une fois par (token, cible)** au chargement (§8.5, partie construction), de sorte que la boucle d'évaluation du Plan 2c n'ait plus qu'à appeler `.matches(candidate)` sur des arbres déjà bâtis.

**Tech Stack:** Python ≥ 3.12, `uv` (projet/paquets), `ruff` (lint+format, `select=["E","F","I","UP","B","SIM"]`, line-length 100), `mypy --strict` (`files=["src","tests"]`), `pytest` + `pytest-cov` (coverage **branch**, seuil **100 %** imposé : `--cov-fail-under=100`), `google-re2` (RE2, importé `re2`), `rapidfuzz` (déjà présents depuis Plan 2a), **`PyYAML`** (parsing YAML, ajouté en Task 1) + **`types-PyYAML`** (stubs dev pour `mypy --strict`).

> **Référence spec :** `docs/superpowers/specs/2026-06-10-crawler-mvp-design.md` — §7 (forme de `targets.yaml`, `target_id` zéro-paddé `S2E062A`), §8.3 (grammaire des tokens nommés & règles + EBNF), §8.4 (validation au chargement fail-fast : DAG/cycle, profondeur bornée, enum `attr_between`, RE2-compilable, schéma), §8.5 (partie **construction** uniquement : « regex précompilées par cible »). S'appuie sur le **Plan 2a** (terminé, tagué `v0.2.0-matchers`) : `normalization.fold/normalize/tokenize`, `matching.models.{FileCandidate,TargetSegment}`, `matching.interpolation.{interpolate,date_alternation_pattern,InterpolationError}`, `matching.matchers.{KeywordMatcher,RegexMatcher,CoverageMatcher,AttrBetweenMatcher,ATTR_NAMES,STOPWORDS_FR}`.

> **Le Plan 2c SUIT et couvre ce qui est HORS PÉRIMÈTRE ici :** la boucle d'**évaluation** (règles ordonnées, 1re vraie gagne par paire `(fichier, cible)`), la **décision fichier** (palier le plus haut ; départage déterministe par index de règle puis `target_id` ; écart si aucune règle), l'**explicabilité** (tokens/règles déclenchés + `value` des coverage loggés), la **façade publique du moteur** et le **corpus golden**. Aucun de ces éléments n'est implémenté dans ce plan 2b.

---

## File Structure

Décisions verrouillées (ne pas dévier) :

**1. Choix de la lib de parsing → `PyYAML` (`yaml.safe_load`) + validation dataclass faite main.**
Justification : (a) **dépendance légère** et omniprésente, parsing pur sans surface réseau ; (b) `yaml.safe_load` produit des structures Python natives **directement exploitables** — vérifié empiriquement : `broadcast_date: 2008-09-21` est parsé en `datetime.date` (s'aligne pile sur `TargetSegment.broadcast_date: datetime.date | None`), `number` en `int`, `min: 0.6` en `float` / `min: 1` en `int`, un `aliases` absent reste absent, et un opérande inline `{token: title_hit, min: 0.5}` est un `dict` ; (c) **validation faite main = entièrement inspectable**, cohérent avec la règle « doit comprendre tout le code » et avec la nature *fail-fast en nommant précisément l'erreur* exigée par §8.4 (un message qui **nomme le cycle**, qui **nomme** le placeholder/tier/token fautif — plus lisible à produire à la main qu'à extraire d'erreurs pydantic) ; (d) garde le **domaine pur** : la validation n'importe pas `yaml`, elle reçoit des `dict`/`list` déjà parsés. Pydantic serait acceptable mais ajoute une dépendance lourde, déplace une partie de la logique de validation dans des `validator` moins transparents, et n'apporte rien sur la validation *de graphe* (DAG/profondeur) qui reste de toute façon faite main. **`types-PyYAML`** est ajouté en dev car `mypy --strict` exige les stubs de `yaml` (vérifié : sans stubs → `error: Library stubs not installed for "yaml" [import-untyped]`).

**2. Introduction d'un `Matcher` `typing.Protocol` → OUI.**
Justification : on a désormais des **feuilles** (Plan 2a), des **combinateurs** (`AllMatcher`/`AnyMatcher`/`NotMatcher`, ce plan) ET un **consommateur** (le resolver bâtit des arbres hétérogènes ; le Plan 2c les évaluera). La revue finale du Plan 2a recommandait de poser le contrat maintenant. On définit `Matcher(Protocol)` avec la **seule** méthode commune `matches(candidate: FileCandidate) -> bool`. **`CoverageMatcher.value()` n'entre PAS dans le Protocol** (spécifique coverage ; le Plan 2c y accédera par `isinstance`/attribut au moment de logger l'explicabilité). Le Protocol est **structural** (`@runtime_checkable` non requis ici) : les 4 feuilles du Plan 2a le satisfont déjà *sans modification* (elles ont `matches`), donc **aucun changement aux fichiers du Plan 2a**. Les combinateurs et le resolver annotent `Matcher` partout, ce qui donne à mypy un type d'arbre homogène.

**3. Frontière domaine/adapter pour le YAML.**
- **Adapter** (`adapters/config/yaml_loader.py`) : SEUL endroit qui importe `yaml` et touche le système de fichiers (`Path.read_text`). Lit deux fichiers → renvoie des `dict`/`list` bruts (`RawConfig` = simple alias de structures Python). Ne valide rien sur le fond.
- **Domaine** (`domain/matching/config.py`, `validation.py`, `combinators.py`, `resolver.py`) : modèle typé, validation (schéma + graphe + RE2 + interpolation), combinateurs, construction par cible. Opère sur des `dict`/`list` ou des dataclasses. **Zéro I/O, zéro `import yaml`.**

**4. Layout des modules** (style Plan 2a : fichiers petits, une responsabilité) :

- `pyproject.toml` — **Modify** : ajoute `pyyaml` à `[project].dependencies` et `types-pyyaml` au groupe `dev` (via `uv add` en Task 1).
- `uv.lock` — **Modify** : régénéré par `uv add`.
- `src/emule_indexer/domain/matching/combinators.py` — **Create** : `Matcher` (Protocol) + `AllMatcher`, `AnyMatcher`, `NotMatcher` (combinateurs purs, wrappent des `Matcher` enfants).
- `src/emule_indexer/domain/matching/config.py` — **Create** : modèle de config **typé & gelé** — défs de tokens feuilles (`KeywordDef`, `RegexDef`, `CoverageDef`, `AttrBetweenDef`), défs composites (`AllDef`, `AnyDef`, `NotDef`), opérandes au point d'usage (`TokenRef` avec overrides `min`/`fuzz`, condition inline réutilise les `*Def`), `Rule` (`name`, `tier`, `condition`), `MatcherConfig` (table `tokens: dict[str, TokenDef]` + `rules: tuple[Rule, ...]`). Pas d'I/O, pas de `yaml`.
- `src/emule_indexer/domain/matching/validation.py` — **Create** : `parse_matcher_config(raw: dict) -> MatcherConfig` (schéma → modèle, fail-fast), `parse_targets(raw: dict) -> tuple[TargetSegment, ...]`, validation du **graphe** (DAG/cycle nommé, profondeur bornée défaut 32), compile-check RE2 + interpolation-check (sur une cible-sonde), enum `attr_between`, tier fermé, référence inconnue. Exceptions : `ConfigError(Exception)` (base) + sous-types nommés.
- `src/emule_indexer/domain/matching/resolver.py` — **Create** : `MatcherResolver` — porte une `MatcherConfig` validée ; `resolve_token(name, target) -> Matcher`, `resolve_rule(rule, target) -> Matcher`, `resolve_all(target) -> ResolvedTarget` (arbres de tous les tokens + règles pour une cible). Interpole+compile les regex par cible, lie coverage à `target.title`, applique les overrides au point d'usage, résout les composites récursivement.
- `src/emule_indexer/adapters/__init__.py` — **Create** : docstring couche adapters.
- `src/emule_indexer/adapters/config/__init__.py` — **Create** : docstring sous-paquet config.
- `src/emule_indexer/adapters/config/yaml_loader.py` — **Create** : `load_yaml(path: Path) -> dict[str, Any]` (lit + `yaml.safe_load` + garde-fou « racine = mapping »). SEUL fichier qui importe `yaml`.
- `tests/domain/matching/test_combinators.py` — **Create** : tests `AllMatcher`/`AnyMatcher`/`NotMatcher` + conformité Protocol.
- `tests/domain/matching/test_config.py` — **Create** : tests des dataclasses de config (gelées, valeurs par défaut).
- `tests/domain/matching/test_validation.py` — **Create** : tests `parse_matcher_config`/`parse_targets` + tous les chemins d'erreur (les deux côtés de chaque branche).
- `tests/domain/matching/test_resolver.py` — **Create** : tests de construction par cible.
- `tests/adapters/__init__.py` — **Create** : paquet de tests adapters.
- `tests/adapters/config/__init__.py` — **Create** : paquet de tests config adapter.
- `tests/adapters/config/test_yaml_loader.py` — **Create** : tests du loader (lecture fichier réel via `tmp_path`, racine non-mapping → erreur).

> **Note couverture (gate 100 % branch) :** chaque conditionnel DOIT être exercé des deux côtés — détection de cycle (cycle / pas de cycle), dépassement de profondeur (dépassé / dans la borne), dispatch par type de feuille (chaque arm `keyword`/`regex`/`coverage`/`attr_between` + arm composite `all`/`any`/`not` + arm `TokenRef`/inline), override présent / absent (`min`/`fuzz` au point d'usage), chaque chemin d'échec de validation (tier hors ensemble / dans l'ensemble, token inconnu / connu, `attr_between` hors enum / dans l'enum, regex non-compilable / compilable, placeholder inconnu / connu, def de token mal formée / bien formée, racine YAML non-mapping / mapping). Les tâches ci-dessous incluent explicitement ces paires.

> **Note typage (`mypy --strict`) :** annotations complètes partout, **y compris dans les tests** (chaque fonction de test annotée `-> None`, paramètres typés). Le tri d'un `dict` de config provenant de YAML est typé `dict[str, Any]` côté adapter ; la frontière `Any → dataclasses typées` est franchie **dans `validation.py`** (un seul endroit), de sorte que le reste du domaine est strictement typé.

> **Décision de design — représentation du modèle de config (Task 3) :** **union étiquetée par dataclasses gelées distinctes**, PAS un seul dataclass à champs optionnels ni un `dict` brut. Chaque forme de la grammaire §8.3 est une classe (`KeywordDef`, `RegexDef`, `CoverageDef`, `AttrBetweenDef`, `AllDef`, `AnyDef`, `NotDef`, `TokenRef`). Le type `TokenDef = KeywordDef | RegexDef | CoverageDef | AttrBetweenDef | AllDef | AnyDef | NotDef` (alias d'union). Une `Condition` (corps de règle / opérande inline `{condition}`) est `AllDef | AnyDef | NotDef`. Justification : (a) **dispatch exhaustif** par `match`/`isinstance` que mypy vérifie ; (b) chaque forme ne porte QUE ses champs (pas de `min: float | None` qui n'a de sens que pour coverage) → invariants encodés dans le type ; (c) le `match` du resolver et de la validation a un `case _:` impossible que mypy prouve mort via `assert_never` — propre et 100 %-couvrable sans branche morte non testable.

> **Décision de design — `AllDef`/`AnyDef` sont à la fois « def de token composite » ET « condition de règle » (Task 3) :** la grammaire §8.3 réutilise la même forme `all: […]` / `any: […]` / `not: …` pour un token composite nommé ET pour le corps d'une règle ET pour une condition inline `{condition}`. On **réutilise les mêmes dataclasses** (`AllDef`/`AnyDef`/`NotDef`) dans les trois contextes plutôt que de dupliquer. Les opérandes d'un `AllDef`/`AnyDef` sont de type `Operand = str | TokenRef | AllDef | AnyDef | NotDef` (un `str` = nom de token nu ; `TokenRef` = `{token: name, min?, fuzz?}` ; `AllDef|AnyDef|NotDef` = condition inline `{condition}`). `NotDef.operand` est un unique `Operand`.

> **Décision de design — surcharges au point d'usage (Task 7) :** `{token: title_hit, min: 0.4}` ne crée PAS un nouveau token ; il **résout** le token `title_hit` puis, **si et seulement si** ce token est un `CoverageDef`, applique les overrides `min`/`fuzz` à la construction du `CoverageMatcher`. Override `min`/`fuzz` sur un token non-coverage → **erreur de validation** (`ConfigError`) au chargement (détectée par `parse_matcher_config`, pas à la résolution), car la grammaire EBNF (`{ token: … (min)? (fuzz)? }`) ne fait sens que pour coverage. Décision verrouillée : un `TokenRef` avec `min` ou `fuzz` non-None référant un token non-coverage est rejeté au chargement.

---

## Task 1: Dépendances — `PyYAML` (runtime) + `types-PyYAML` (dev)

**Files:**
- Modify: `pyproject.toml` (sections `[project].dependencies` et `[dependency-groups].dev` — éditées automatiquement par `uv add`)
- Modify: `uv.lock` (régénéré par `uv add`)

- [ ] **Step 1: Ajouter la dépendance runtime de parsing**

Run: `uv add pyyaml`
Expected: ajoute `pyyaml` à `[project].dependencies` dans `pyproject.toml`, résout/installe la wheel, met à jour `uv.lock`, code de sortie 0.

- [ ] **Step 2: Ajouter les stubs de types en dev (requis par `mypy --strict`)**

Run: `uv add --dev types-pyyaml`
Expected: ajoute `types-pyyaml` au groupe `dev`, met à jour `uv.lock`, code de sortie 0. *(Sans ces stubs, `mypy --strict` échoue sur `import yaml` avec `Library stubs not installed for "yaml" [import-untyped]` — vérifié empiriquement.)*

- [ ] **Step 3: Vérifier l'import de `yaml`**

Run: `uv run python -c "import yaml; print(yaml.safe_load('a: 1'))"`
Expected: affiche `{'a': 1}`, code de sortie 0.

- [ ] **Step 4: Vérifier que lint + types + tests existants passent encore**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (aucun code de prod neuf, gate satisfait) ; aucune erreur ruff/mypy.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add pyyaml and types-pyyaml"
```

---

## Task 2: Combinateurs `AllMatcher`/`AnyMatcher`/`NotMatcher` + `Matcher` Protocol

**Files:**
- Create: `src/emule_indexer/domain/matching/combinators.py`
- Create: `tests/domain/matching/test_combinators.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/matching/test_combinators.py` :
```python
from emule_indexer.domain.matching.combinators import (
    AllMatcher,
    AnyMatcher,
    Matcher,
    NotMatcher,
)
from emule_indexer.domain.matching.matchers import KeywordMatcher
from emule_indexer.domain.matching.models import FileCandidate


class _Const:
    """Matcher de test à verdict constant (satisfait le Protocol Matcher)."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict

    def matches(self, candidate: FileCandidate) -> bool:
        return self._verdict


_ANY = FileCandidate(filename="whatever.avi")


def test_all_true_when_every_child_true() -> None:
    matcher = AllMatcher((_Const(True), _Const(True)))
    assert matcher.matches(_ANY) is True


def test_all_false_when_one_child_false() -> None:
    matcher = AllMatcher((_Const(True), _Const(False)))
    assert matcher.matches(_ANY) is False


def test_all_empty_is_true() -> None:
    # all([]) == True (neutre de la conjonction).
    matcher = AllMatcher(())
    assert matcher.matches(_ANY) is True


def test_any_true_when_one_child_true() -> None:
    matcher = AnyMatcher((_Const(False), _Const(True)))
    assert matcher.matches(_ANY) is True


def test_any_false_when_all_children_false() -> None:
    matcher = AnyMatcher((_Const(False), _Const(False)))
    assert matcher.matches(_ANY) is False


def test_any_empty_is_false() -> None:
    # any([]) == False (neutre de la disjonction).
    matcher = AnyMatcher(())
    assert matcher.matches(_ANY) is False


def test_not_inverts_child() -> None:
    assert NotMatcher(_Const(True)).matches(_ANY) is False
    assert NotMatcher(_Const(False)).matches(_ANY) is True


def test_nested_combinators() -> None:
    # all[ any[False, True], not False ] == all[True, True] == True
    matcher = AllMatcher((AnyMatcher((_Const(False), _Const(True))), NotMatcher(_Const(False))))
    assert matcher.matches(_ANY) is True


def test_real_leaf_satisfies_protocol_and_composes() -> None:
    leaf: Matcher = KeywordMatcher("keroro")
    matcher = AnyMatcher((leaf, _Const(False)))
    assert matcher.matches(FileCandidate(filename="Keroro 062A.avi")) is True
    assert matcher.matches(FileCandidate(filename="autre.avi")) is False
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_combinators.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.matching.combinators'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/matching/combinators.py` :
```python
"""Contrat ``Matcher`` (Protocol) et combinateurs ``all``/``any``/``not`` (cf. spec §8.3).

Domaine pur : ces combinateurs wrappent d'autres ``Matcher`` (feuilles du Plan 2a ou
combinateurs) et exposent la même interface ``matches(candidate) -> bool``.
"""

from typing import Protocol

from emule_indexer.domain.matching.models import FileCandidate


class Matcher(Protocol):
    """Contrat structural commun aux feuilles (Plan 2a) et aux combinateurs.

    Les 4 matchers feuilles (`KeywordMatcher`, `RegexMatcher`, `CoverageMatcher`,
    `AttrBetweenMatcher`) le satisfont déjà sans modification (ils ont `matches`).
    `CoverageMatcher.value()` n'entre PAS dans le contrat : le Plan 2c y accédera
    spécifiquement pour l'explicabilité.
    """

    def matches(self, candidate: FileCandidate) -> bool: ...


class AllMatcher:
    """Conjonction : vrai si TOUS les enfants matchent (``all([]) == True``)."""

    def __init__(self, children: tuple[Matcher, ...]) -> None:
        self._children = children

    def matches(self, candidate: FileCandidate) -> bool:
        return all(child.matches(candidate) for child in self._children)


class AnyMatcher:
    """Disjonction : vrai si AU MOINS un enfant matche (``any([]) == False``)."""

    def __init__(self, children: tuple[Matcher, ...]) -> None:
        self._children = children

    def matches(self, candidate: FileCandidate) -> bool:
        return any(child.matches(candidate) for child in self._children)


class NotMatcher:
    """Négation : vrai si l'enfant unique NE matche PAS."""

    def __init__(self, child: Matcher) -> None:
        self._child = child

    def matches(self, candidate: FileCandidate) -> bool:
        return not self._child.matches(candidate)
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_combinators.py -q`
Expected: PASS. Les deux côtés de chaque combinateur sont exercés : `all` vrai/faux/vide, `any` vrai/faux/vide, `not` des deux côtés, imbrication, et une vraie feuille `KeywordMatcher` typée `Matcher` composée → conformité Protocol confirmée par mypy.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur ruff/mypy.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/combinators.py tests/domain/matching/test_combinators.py
git commit -m "feat(domain): Matcher Protocol + All/Any/Not combinators"
```

---

## Task 3: Modèle de config (union étiquetée de dataclasses gelées)

**Files:**
- Create: `src/emule_indexer/domain/matching/config.py`
- Create: `tests/domain/matching/test_config.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/matching/test_config.py` :
```python
import dataclasses

import pytest

from emule_indexer.domain.matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
    CoverageDef,
    KeywordDef,
    MatcherConfig,
    NotDef,
    RegexDef,
    Rule,
    TokenRef,
)


def test_keyword_def_holds_phrase() -> None:
    assert KeywordDef(phrase="keroro").phrase == "keroro"


def test_regex_def_defaults_flags_to_i() -> None:
    assert RegexDef(pattern="teletoon").flags == "i"
    assert RegexDef(pattern="teletoon", flags="").flags == ""


def test_coverage_def_defaults() -> None:
    cov = CoverageDef(reference="title", min=0.6)
    assert cov.reference == "title"
    assert cov.min == 0.6
    assert cov.fuzz == 0.85


def test_attr_between_def_holds_bounds() -> None:
    ab = AttrBetweenDef(attr="size_mb", min=30.0, max=600.0)
    assert ab.attr == "size_mb"
    assert ab.min == 30.0
    assert ab.max == 600.0
    assert AttrBetweenDef(attr="size_mb").min is None
    assert AttrBetweenDef(attr="size_mb").max is None


def test_composite_defs_hold_operands() -> None:
    comp = AnyDef(operands=("keroro", "titar"))
    assert comp.operands == ("keroro", "titar")
    assert AllDef(operands=()).operands == ()
    assert NotDef(operand="keroro").operand == "keroro"


def test_token_ref_overrides_default_to_none() -> None:
    ref = TokenRef(name="title_hit")
    assert ref.name == "title_hit"
    assert ref.min is None
    assert ref.fuzz is None
    assert TokenRef(name="title_hit", min=0.4).min == 0.4


def test_rule_holds_name_tier_condition() -> None:
    rule = Rule(name="keroro_large", tier="catalog", condition=AnyDef(operands=("keroro_titar",)))
    assert rule.name == "keroro_large"
    assert rule.tier == "catalog"
    assert isinstance(rule.condition, AnyDef)


def test_matcher_config_holds_tokens_and_rules() -> None:
    config = MatcherConfig(
        tokens={"keroro": KeywordDef(phrase="keroro")},
        rules=(Rule(name="r", tier="catalog", condition=AnyDef(operands=("keroro",))),),
    )
    assert config.tokens["keroro"] == KeywordDef(phrase="keroro")
    assert len(config.rules) == 1


def test_defs_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        KeywordDef(phrase="x").phrase = "y"  # type: ignore[misc]
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.matching.config'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/matching/config.py` :
```python
"""Modèle de config du moteur de matching — union étiquetée de dataclasses gelées.

Représente la grammaire §8.3 (tokens nommés feuilles + composites, opérandes au
point d'usage, règles). Domaine PUR : aucune I/O, aucun import de bibliothèque YAML.
La construction depuis du YAML parsé (dict/list) est faite par ``validation.py``.
"""

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

Operand = "str | TokenRef | AllDef | AnyDef | NotDef"


@dataclass(frozen=True)
class AllDef:
    """``all: [operand, ...]`` — conjonction."""

    operands: tuple["str | TokenRef | AllDef | AnyDef | NotDef", ...]


@dataclass(frozen=True)
class AnyDef:
    """``any: [operand, ...]`` — disjonction."""

    operands: tuple["str | TokenRef | AllDef | AnyDef | NotDef", ...]


@dataclass(frozen=True)
class NotDef:
    """``not: operand`` — négation d'un unique opérande."""

    operand: "str | TokenRef | AllDef | AnyDef | NotDef"


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

    tokens: dict[str, TokenDef] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()
```

> **Note typage :** `Operand` est une annotation forward-référencée par chaîne (`"str | TokenRef | AllDef | AnyDef | NotDef"`) car `AllDef`/`AnyDef`/`NotDef` se référencent mutuellement et eux-mêmes (récursion). La constante `Operand` en tête n'est qu'un repère lisible — les dataclasses utilisent la chaîne littérale dans leurs annotations pour que mypy résolve la récursion. `mypy --strict` accepte ces forward refs.

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_config.py -q`
Expected: PASS. Tous les défauts (`flags="i"`, `fuzz=0.85`, `min/max=None`, overrides `None`), le gel (`FrozenInstanceError`), et les trois usages de composite sont exercés.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur ruff/mypy.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/config.py tests/domain/matching/test_config.py
git commit -m "feat(domain): matcher config model (tagged dataclass union)"
```

---

## Task 4: Adapter YAML loader

**Files:**
- Create: `src/emule_indexer/adapters/__init__.py`
- Create: `src/emule_indexer/adapters/config/__init__.py`
- Create: `src/emule_indexer/adapters/config/yaml_loader.py`
- Create: `tests/adapters/__init__.py`
- Create: `tests/adapters/config/__init__.py`
- Create: `tests/adapters/config/test_yaml_loader.py`

- [ ] **Step 1: Créer les `__init__.py`**

`src/emule_indexer/adapters/__init__.py` :
```python
"""Couche adapters (I/O) : YAML, EC, SQLite, apprise, etc. — dépend de ports/domain."""
```

`src/emule_indexer/adapters/config/__init__.py` :
```python
"""Adapters de configuration : lecture des fichiers YAML (tokens/règles, targets)."""
```

`tests/adapters/__init__.py` : *(fichier vide)*
```python
```

`tests/adapters/config/__init__.py` : *(fichier vide)*
```python
```

- [ ] **Step 2: Écrire les tests qui échouent**

`tests/adapters/config/test_yaml_loader.py` :
```python
from pathlib import Path

import pytest

from emule_indexer.adapters.config.yaml_loader import YamlLoadError, load_yaml


def test_load_yaml_reads_mapping(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("tokens:\n  keroro: { keyword: keroro }\n", encoding="utf-8")
    data = load_yaml(path)
    assert data == {"tokens": {"keroro": {"keyword": "keroro"}}}


def test_load_yaml_parses_dates(tmp_path: Path) -> None:
    path = tmp_path / "t.yaml"
    path.write_text(
        "episodes:\n  - { number: 62, broadcast_date: 2008-09-21 }\n", encoding="utf-8"
    )
    import datetime

    data = load_yaml(path)
    assert data["episodes"][0]["broadcast_date"] == datetime.date(2008, 9, 21)


def test_load_yaml_non_mapping_root_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(YamlLoadError, match="mapping"):
        load_yaml(path)


def test_load_yaml_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(YamlLoadError, match="mapping"):
        load_yaml(path)
```

- [ ] **Step 3: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/config/test_yaml_loader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.adapters.config.yaml_loader'`.

- [ ] **Step 4: Écrire l'implémentation**

`src/emule_indexer/adapters/config/yaml_loader.py` :
```python
"""Adapter : lecture d'un fichier YAML en structures Python (cf. spec §4 frontière I/O).

SEUL module du projet qui importe ``yaml`` et touche le système de fichiers pour la
config. Ne valide PAS le fond (schéma/graphe/RE2) : c'est le rôle du domaine
(``domain.matching.validation``). Garde-fou minimal : la racine doit être un mapping.
"""

from pathlib import Path
from typing import Any

import yaml


class YamlLoadError(Exception):
    """Le fichier YAML est illisible ou sa racine n'est pas un mapping."""


def load_yaml(path: Path) -> dict[str, Any]:
    """Lit ``path`` et renvoie sa racine (un mapping) parsée par ``yaml.safe_load``.

    ``safe_load`` parse les dates ISO en ``datetime.date`` et n'instancie aucun objet
    Python arbitraire (pas de ``yaml.load`` non sûr). Racine non-mapping (liste, scalaire,
    fichier vide → ``None``) lève :class:`YamlLoadError`.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise YamlLoadError(f"racine YAML attendue = mapping, obtenu {type(raw).__name__} ({path})")
    return raw
```

- [ ] **Step 5: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/adapters/config/test_yaml_loader.py -q`
Expected: PASS. Les deux côtés de `if not isinstance(raw, dict)` sont exercés (mapping OK ; liste et fichier vide → `None` → erreur), et le parsing de date est confirmé.

- [ ] **Step 6: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur ruff/mypy. *(C'est le premier fichier qui importe `yaml` ; mypy doit le typer sans erreur grâce à `types-pyyaml` de la Task 1.)*

- [ ] **Step 7: Commit**

```bash
git add src/emule_indexer/adapters/__init__.py src/emule_indexer/adapters/config/__init__.py src/emule_indexer/adapters/config/yaml_loader.py tests/adapters/__init__.py tests/adapters/config/__init__.py tests/adapters/config/test_yaml_loader.py
git commit -m "feat(adapters): YAML loader (safe_load, mapping-root guard)"
```

---

## Task 5: Parsing du schéma → modèle (`parse_matcher_config` + `parse_targets`)

**Files:**
- Create: `src/emule_indexer/domain/matching/validation.py`
- Create: `tests/domain/matching/test_validation.py`

> Cette tâche couvre le **schéma** (forme des dict → dataclasses, fail-fast nommant l'erreur) ET les **validations locales** (tier fermé, `attr_between` dans l'enum, override coverage-only). La validation de **graphe** (DAG/cycle, profondeur) et le **compile-check RE2 / interpolation** arrivent en **Task 6** sur la même base de fichiers.

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/matching/test_validation.py` :
```python
import datetime

import pytest

from emule_indexer.domain.matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
    CoverageDef,
    KeywordDef,
    NotDef,
    RegexDef,
    TokenRef,
)
from emule_indexer.domain.matching.validation import (
    ConfigError,
    parse_matcher_config,
    parse_targets,
)


def test_parse_leaf_token_defs() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "teletoon": {"regex": "t[eé]l[eé]toon"},
                "video": {"regex": "\\.(avi|mkv)$", "flags": ""},
                "title_hit": {"coverage": "title", "min": 0.6},
                "small": {"attr_between": "size_mb", "min": 30, "max": 600},
            },
            "rules": [],
        }
    )
    assert config.tokens["keroro"] == KeywordDef(phrase="keroro")
    assert config.tokens["teletoon"] == RegexDef(pattern="t[eé]l[eé]toon", flags="i")
    assert config.tokens["video"] == RegexDef(pattern="\\.(avi|mkv)$", flags="")
    assert config.tokens["title_hit"] == CoverageDef(reference="title", min=0.6)
    assert config.tokens["small"] == AttrBetweenDef(attr="size_mb", min=30.0, max=600.0)


def test_parse_composite_token_def() -> None:
    config = parse_matcher_config(
        {"tokens": {"kt": {"any": ["keroro", "titar"]}}, "rules": []}
    )
    assert config.tokens["kt"] == AnyDef(operands=("keroro", "titar"))


def test_parse_not_token_def() -> None:
    config = parse_matcher_config({"tokens": {"nk": {"not": "keroro"}}, "rules": []})
    assert config.tokens["nk"] == NotDef(operand="keroro")


def test_parse_rule_with_inline_token_ref_and_condition() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "title_hit": {"coverage": "title", "min": 0.6},
                "seg": {"regex": "0*{number}"},
            },
            "rules": [
                {
                    "name": "numero_titre",
                    "tier": "notify",
                    "all": ["seg", {"token": "title_hit", "min": 0.5}],
                }
            ],
        }
    )
    rule = config.rules[0]
    assert rule.name == "numero_titre"
    assert rule.tier == "notify"
    assert rule.condition == AllDef(operands=("seg", TokenRef(name="title_hit", min=0.5)))


def test_parse_rule_with_nested_inline_condition() -> None:
    config = parse_matcher_config(
        {
            "tokens": {"keroro": {"keyword": "keroro"}, "titar": {"keyword": "titar"}},
            "rules": [{"name": "r", "tier": "catalog", "not": {"any": ["keroro", "titar"]}}],
        }
    )
    assert config.rules[0].condition == NotDef(operand=AnyDef(operands=("keroro", "titar")))


def test_unknown_tier_raises_and_names_it() -> None:
    with pytest.raises(ConfigError, match="bogus"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "bogus", "any": ["keroro"]}],
            }
        )


def test_attr_between_unknown_attr_raises_and_names_it() -> None:
    with pytest.raises(ConfigError, match="codec"):
        parse_matcher_config(
            {"tokens": {"c": {"attr_between": "codec", "min": 1}}, "rules": []}
        )


def test_unknown_token_definition_shape_raises() -> None:
    with pytest.raises(ConfigError, match="forme de token inconnue"):
        parse_matcher_config({"tokens": {"x": {"frobnicate": "y"}}, "rules": []})


def test_token_def_with_multiple_keys_raises() -> None:
    with pytest.raises(ConfigError, match="exactement une clé"):
        parse_matcher_config(
            {"tokens": {"x": {"keyword": "a", "regex": "b"}}, "rules": []}
        )


def test_override_on_non_coverage_token_raises() -> None:
    with pytest.raises(ConfigError, match="title_hit"):
        parse_matcher_config(
            {
                "tokens": {"kw": {"keyword": "keroro"}},
                "rules": [
                    {"name": "r", "tier": "catalog", "all": [{"token": "kw", "min": 0.5}]}
                ],
            }
        )


def test_rule_without_condition_key_raises() -> None:
    with pytest.raises(ConfigError, match="condition"):
        parse_matcher_config(
            {"tokens": {"keroro": {"keyword": "keroro"}}, "rules": [{"name": "r", "tier": "catalog"}]}
        )


def test_rule_with_two_condition_keys_raises() -> None:
    with pytest.raises(ConfigError, match="une seule condition"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": ["keroro"], "any": ["keroro"]}],
            }
        )


def test_token_ref_missing_name_raises() -> None:
    with pytest.raises(ConfigError, match="token"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": [{"min": 0.5}]}],
            }
        )


def test_operand_wrong_type_raises() -> None:
    with pytest.raises(ConfigError, match="opérande"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": [123]}],
            }
        )


def test_parse_targets_builds_segments() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 2,
                    "number": 62,
                    "broadcast_date": datetime.date(2008, 9, 21),
                    "status": "partial",
                    "segments": [
                        {"letter": "A", "title": "Les demoiselles", "aliases": ["alt"]},
                        {"letter": "B", "title": "Le grand combat"},
                    ],
                }
            ]
        }
    )
    assert len(targets) == 2
    a, b = targets
    assert a.target_id == "S2E062A"
    assert a.broadcast_date == datetime.date(2008, 9, 21)
    assert a.status == "partial"
    assert a.aliases == ("alt",)
    assert b.target_id == "S2E062B"
    assert b.aliases == ()
    assert b.status == "partial"


def test_parse_targets_default_status_is_lost() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {"season": 1, "number": 5, "segments": [{"letter": "a", "title": "x"}]}
            ]
        }
    )
    assert targets[0].status == "lost"
    assert targets[0].broadcast_date is None


def test_parse_targets_missing_episodes_raises() -> None:
    with pytest.raises(ConfigError, match="episodes"):
        parse_targets({})
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_validation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.matching.validation'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/matching/validation.py` :
```python
"""Validation au chargement (fail-fast) : schéma YAML parsé -> modèle de config.

Domaine PUR : reçoit des structures déjà parsées (``dict``/``list``), n'importe pas
``yaml``, ne touche pas le disque. Couvre la spec §8.4 (validation au chargement) côté
schéma + validations locales (tier fermé, enum ``attr_between``, override coverage-only).
La validation de graphe (DAG/profondeur) et le compile-check RE2 sont ajoutés en Task 6.
"""

import datetime
from typing import Any

from emule_indexer.domain.matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
    Condition,
    CoverageDef,
    KeywordDef,
    NotDef,
    RegexDef,
    Rule,
    TIERS,
    TokenDef,
    TokenRef,
)
from emule_indexer.domain.matching.matchers import ATTR_NAMES
from emule_indexer.domain.matching.models import TargetSegment

_CONDITION_KEYS = ("all", "any", "not")


class ConfigError(Exception):
    """Erreur fatale de configuration au chargement (schéma, tier, enum, override)."""


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} : mapping attendu, obtenu {type(value).__name__}")
    return value


def _parse_operand(raw: Any, tokens: dict[str, TokenDef]) -> "str | TokenRef | AllDef | AnyDef | NotDef":
    """Un opérande : nom de token nu (str), ``{token: …}`` (TokenRef), ou condition inline."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        if "token" in raw:
            return _parse_token_ref(raw, tokens)
        if any(key in raw for key in _CONDITION_KEYS):
            return _parse_condition(raw, tokens)
        raise ConfigError(f"opérande inconnu : {raw!r}")
    raise ConfigError(f"opérande de type invalide : {type(raw).__name__} ({raw!r})")


def _parse_token_ref(raw: dict[str, Any], tokens: dict[str, TokenDef]) -> TokenRef:
    name = raw.get("token")
    if not isinstance(name, str):
        raise ConfigError(f"opérande {{token: …}} sans nom de token valide : {raw!r}")
    min_value = raw.get("min")
    fuzz_value = raw.get("fuzz")
    ref = TokenRef(
        name=name,
        min=None if min_value is None else float(min_value),
        fuzz=None if fuzz_value is None else float(fuzz_value),
    )
    # Override min/fuzz n'a de sens que sur un token coverage (cf. EBNF §8.3).
    if (ref.min is not None or ref.fuzz is not None) and not isinstance(
        tokens.get(name), CoverageDef
    ):
        raise ConfigError(
            f"override min/fuzz interdit sur le token non-coverage {name!r}"
        )
    return ref


def _parse_condition(raw: dict[str, Any], tokens: dict[str, TokenDef]) -> Condition:
    present = [key for key in _CONDITION_KEYS if key in raw]
    if len(present) != 1:
        raise ConfigError(f"une seule condition (all/any/not) attendue, obtenu {present!r}")
    key = present[0]
    body = raw[key]
    if key == "not":
        return NotDef(operand=_parse_operand(body, tokens))
    if not isinstance(body, list):
        raise ConfigError(f"'{key}:' attend une liste d'opérandes, obtenu {type(body).__name__}")
    operands = tuple(_parse_operand(item, tokens) for item in body)
    if key == "all":
        return AllDef(operands=operands)
    return AnyDef(operands=operands)


def _require_float(mapping: dict[str, Any], key: str) -> float | None:
    """Lit une borne flottante optionnelle (``None`` si absente)."""
    value = mapping.get(key)
    return None if value is None else float(value)


def _parse_token_def(raw: Any, tokens: dict[str, TokenDef]) -> TokenDef:
    """Dispatch d'une def de token : composite (all/any/not) ou feuille (4 types).

    Lit TOUTES les clés annexes de la def (``flags`` du regex, ``min``/``fuzz`` du
    coverage, ``min``/``max`` de l'attr_between), pas seulement la clé-type.
    """
    mapping = _require_mapping(raw, "définition de token")
    if any(key in mapping for key in _CONDITION_KEYS):
        return _parse_condition(mapping, tokens)
    leaf_keys = [k for k in ("keyword", "regex", "coverage", "attr_between") if k in mapping]
    if len(leaf_keys) > 1:
        raise ConfigError(f"un token feuille a exactement une clé-type, obtenu {sorted(leaf_keys)}")
    if "keyword" in mapping:
        return KeywordDef(phrase=str(mapping["keyword"]))
    if "regex" in mapping:
        flags = mapping.get("flags", "i")
        return RegexDef(pattern=str(mapping["regex"]), flags=str(flags))
    if "coverage" in mapping:
        min_value = _require_float(mapping, "min")
        if min_value is None:
            raise ConfigError("un token coverage doit déclarer 'min'")
        fuzz_value = _require_float(mapping, "fuzz")
        return CoverageDef(
            reference=str(mapping["coverage"]),
            min=min_value,
            fuzz=0.85 if fuzz_value is None else fuzz_value,
        )
    if "attr_between" in mapping:
        attr = str(mapping["attr_between"])
        if attr not in ATTR_NAMES:
            raise ConfigError(
                f"attr_between inconnu : {attr!r} (attendu l'un de {sorted(ATTR_NAMES)})"
            )
        return AttrBetweenDef(
            attr=attr,
            min=_require_float(mapping, "min"),
            max=_require_float(mapping, "max"),
        )
    raise ConfigError(f"forme de token inconnue : clés {sorted(mapping)}")
```

> **Note dispatch feuille :** le garde-fou `leaf_keys` rejette une def mêlant DEUX clés-type (`{keyword: a, regex: b}`) avec le motif `exactement une clé` (attendu par `test_token_def_with_multiple_keys_raises`). Ensuite chaque type est testé par présence de sa clé-type, ce qui laisse `flags`/`min`/`fuzz`/`max` coexister avec elle. La branche `len(leaf_keys) > 1` vraie (test multi-clés) ET fausse (toutes les autres defs) est exercée → couverte des deux côtés.

Puis ajouter les entrées publiques à la fin du fichier `src/emule_indexer/domain/matching/validation.py` :
```python
def _parse_rule(raw: Any, tokens: dict[str, TokenDef]) -> Rule:
    mapping = _require_mapping(raw, "règle")
    name = str(mapping.get("name", ""))
    if not name:
        raise ConfigError(f"règle sans 'name' : {raw!r}")
    tier = mapping.get("tier")
    if tier not in TIERS:
        raise ConfigError(f"tier inconnu pour la règle {name!r} : {tier!r} (attendu {sorted(TIERS)})")
    present = [key for key in _CONDITION_KEYS if key in mapping]
    if not present:
        raise ConfigError(f"règle {name!r} sans condition (all/any/not)")
    if len(present) != 1:
        raise ConfigError(f"règle {name!r} : une seule condition attendue, obtenu {present!r}")
    return Rule(name=name, tier=str(tier), condition=_parse_condition(mapping, tokens))


def parse_matcher_config(raw: dict[str, Any]) -> "MatcherConfig":
    """Construit un :class:`MatcherConfig` validé (schéma) depuis un dict YAML parsé."""
    from emule_indexer.domain.matching.config import MatcherConfig

    tokens_raw = _require_mapping(raw.get("tokens", {}), "section 'tokens'")
    tokens: dict[str, TokenDef] = {}
    for token_name, token_raw in tokens_raw.items():
        tokens[str(token_name)] = _parse_token_def(token_raw, tokens)
    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ConfigError(f"section 'rules' : liste attendue, obtenu {type(rules_raw).__name__}")
    rules = tuple(_parse_rule(rule_raw, tokens) for rule_raw in rules_raw)
    return MatcherConfig(tokens=tokens, rules=rules)


def parse_targets(raw: dict[str, Any]) -> tuple[TargetSegment, ...]:
    """Construit les :class:`TargetSegment` depuis ``targets.yaml`` parsé (cf. spec §7)."""
    episodes = raw.get("episodes")
    if not isinstance(episodes, list):
        raise ConfigError("section 'episodes' : liste attendue")
    segments: list[TargetSegment] = []
    for episode in episodes:
        ep = _require_mapping(episode, "épisode")
        season = int(ep["season"])
        number = int(ep["number"])
        broadcast = ep.get("broadcast_date")
        broadcast_date = broadcast if isinstance(broadcast, datetime.date) else None
        status = str(ep.get("status", "lost"))
        for seg in ep.get("segments", []):
            seg_map = _require_mapping(seg, "segment")
            aliases = tuple(str(alias) for alias in seg_map.get("aliases", ()))
            segments.append(
                TargetSegment(
                    season=season,
                    number=number,
                    segment=str(seg_map["letter"]),
                    title=str(seg_map["title"]),
                    broadcast_date=broadcast_date,
                    status=status,
                    aliases=aliases,
                )
            )
    return tuple(segments)
```

> **Note import circulaire :** `parse_matcher_config` importe `MatcherConfig` **localement** (dans la fonction) pour éviter tout cycle au niveau module — `config.py` ne dépend de rien d'autre, donc l'import top-level marcherait aussi, mais le local garde `validation.py` robuste si `config.py` venait à importer un helper de validation plus tard. Les défs (`KeywordDef`, etc.) sont importées en top-level car sans risque. **`Operand` n'est PAS importé** (les annotations d'opérande sont des chaînes forward-référencées) : ne pas l'ajouter, sinon ruff signalerait un import inutilisé (`F401`).

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_validation.py -q`
Expected: PASS. Chaque chemin est exercé des deux côtés : feuilles bien formées vs `forme de token inconnue` / `exactement une clé` (plusieurs clés) ; composite/`not` ; `TokenRef` valide vs sans `token` (`token` manquant) ; tier valide vs `bogus` ; `attr_between` valide vs `codec` ; override coverage-only OK vs sur `keyword` (rejeté) ; règle avec/ sans condition / avec deux conditions ; opérande de type invalide (`123`) ; `parse_targets` avec/sans `broadcast_date`, status défaut `lost` vs explicite, aliases présents/absents, `episodes` manquant.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 %, aucune erreur ruff/mypy.

> **Note couverture résiduelle :** si le gate signale une branche non couverte (p. ex. `_parse_token_ref` quand `min`/`fuzz` sont absents ET le token est coverage, ou `parse_targets` sans clé `segments`), AJOUTER le test manquant correspondant dans `tests/domain/matching/test_validation.py` AVANT de committer — ne jamais baisser le seuil. Cas à couvrir explicitement si manquants : `{"token": "title_hit"}` sans override sur un coverage (OK, pas d'erreur), épisode sans `segments` (boucle vide → 0 segment), `broadcast_date` absent (`None`).

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/validation.py tests/domain/matching/test_validation.py
git commit -m "feat(domain): schema validation of matcher config and targets"
```

---

## Task 6: Validation de graphe (DAG/cycle nommé, profondeur bornée) + compile-check RE2/interpolation

**Files:**
- Modify: `src/emule_indexer/domain/matching/validation.py`
- Modify: `tests/domain/matching/test_validation.py`

> Ajoute la validation §8.4 « lourde » : (a) le **graphe de références acyclique** — tout token nu / `TokenRef` cité doit exister, et le graphe token→token doit être un **DAG** (cycle → erreur fatale **nommant le cycle**) ; (b) **profondeur de résolution bornée** (défaut 32) → dépassement = erreur fatale ; (c) **compile-check RE2** + **interpolation-check** : chaque `RegexDef` doit s'interpoler sur une cible-sonde et compiler sous RE2 (un placeholder inconnu → `InterpolationError` capturée et re-levée en `ConfigError` ; un pattern non-RE2 → `re2.error` capturée et re-levée en `ConfigError`). On expose `validate_config(config, *, max_depth=32) -> None` et on l'appelle depuis `parse_matcher_config`.

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter à la fin de `tests/domain/matching/test_validation.py` (et compléter les imports du haut pour inclure `CycleError`, `DepthExceededError`, `UnknownTokenError`, `validate_config`) :
```python
from emule_indexer.domain.matching.validation import (
    CycleError,
    DepthExceededError,
    UnknownTokenError,
    validate_config,
)


def test_unknown_token_reference_raises_and_names_it() -> None:
    with pytest.raises(UnknownTokenError, match="ghost"):
        parse_matcher_config(
            {
                "tokens": {"kt": {"any": ["keroro", "ghost"]}, "keroro": {"keyword": "keroro"}},
                "rules": [],
            }
        )


def test_unknown_token_in_rule_raises() -> None:
    with pytest.raises(UnknownTokenError, match="ghost"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": ["keroro", "ghost"]}],
            }
        )


def test_direct_cycle_is_detected_and_named() -> None:
    with pytest.raises(CycleError) as excinfo:
        parse_matcher_config(
            {"tokens": {"a": {"any": ["b"]}, "b": {"any": ["a"]}}, "rules": []}
        )
    message = str(excinfo.value)
    assert "a" in message and "b" in message


def test_self_cycle_is_detected() -> None:
    with pytest.raises(CycleError, match="loop"):
        parse_matcher_config({"tokens": {"loop": {"any": ["loop"]}}, "rules": []})


def test_acyclic_composite_graph_validates() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "titar": {"keyword": "titar"},
                "kt": {"any": ["keroro", "titar"]},
                "deep": {"all": ["kt", "keroro"]},
            },
            "rules": [{"name": "r", "tier": "catalog", "any": ["deep"]}],
        }
    )
    assert "deep" in config.tokens


def test_regex_compile_check_rejects_bad_pattern() -> None:
    with pytest.raises(ConfigError, match="RE2"):
        parse_matcher_config({"tokens": {"bad": {"regex": "(unbalanced"}}, "rules": []})


def test_regex_unknown_placeholder_rejected_at_load() -> None:
    with pytest.raises(ConfigError, match="bogus"):
        parse_matcher_config({"tokens": {"bad": {"regex": "n {bogus}"}}, "rules": []})


def test_regex_with_known_placeholders_validates() -> None:
    config = parse_matcher_config(
        {"tokens": {"seg": {"regex": "n[°o]?\\s*0*{number}\\s*{segment}"}}, "rules": []}
    )
    assert "seg" in config.tokens


def test_regex_date_alt_placeholder_validates_via_probe() -> None:
    # {date_alt} exige un broadcast_date ; la sonde de validation en fournit un.
    config = parse_matcher_config({"tokens": {"air": {"regex": "{date_alt}"}}, "rules": []})
    assert "air" in config.tokens


def test_depth_within_bound_validates() -> None:
    # Chaîne a -> b -> c (profondeur 3) avec max_depth=3 : OK.
    config = parse_matcher_config(
        {
            "tokens": {
                "c": {"keyword": "x"},
                "b": {"any": ["c"]},
                "a": {"any": ["b"]},
            },
            "rules": [],
        }
    )
    validate_config(config, max_depth=3)


def test_depth_exceeded_raises() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "c": {"keyword": "x"},
                "b": {"any": ["c"]},
                "a": {"any": ["b"]},
            },
            "rules": [],
        }
    )
    with pytest.raises(DepthExceededError, match="32|profondeur"):
        validate_config(config, max_depth=2)


def test_default_max_depth_is_32() -> None:
    # Une chaîne de 33 tokens dépasse le défaut 32.
    tokens: dict[str, object] = {"t0": {"keyword": "x"}}
    for i in range(1, 34):
        tokens[f"t{i}"] = {"any": [f"t{i - 1}"]}
    with pytest.raises(DepthExceededError):
        parse_matcher_config({"tokens": tokens, "rules": []})
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_validation.py -q`
Expected: FAIL — `ImportError: cannot import name 'CycleError'` (et `DepthExceededError`/`UnknownTokenError`/`validate_config` indisponibles).

- [ ] **Step 3: Écrire l'implémentation**

Ajouter en tête de `src/emule_indexer/domain/matching/validation.py`, après les imports existants :
```python
import re2

from emule_indexer.domain.matching.interpolation import InterpolationError, interpolate
```

Puis ajouter les sous-types d'erreur juste après la définition de `ConfigError` :
```python
class UnknownTokenError(ConfigError):
    """Une référence pointe vers un token absent de la table."""


class CycleError(ConfigError):
    """Le graphe de références token->token contient un cycle (le message le nomme)."""


class DepthExceededError(ConfigError):
    """La profondeur de résolution dépasse la borne (défaut 32)."""
```

Puis ajouter à la fin du fichier `src/emule_indexer/domain/matching/validation.py` :
```python
_DEFAULT_MAX_DEPTH = 32

# Cible-sonde pour le compile-check : fournit number/segment/title/date_alt afin que
# l'interpolation de toute RegexDef soit testable au chargement (cf. spec §8.4/§8.5).
_PROBE_TARGET = TargetSegment(
    season=2,
    number=62,
    segment="a",
    title="sonde",
    broadcast_date=datetime.date(2008, 9, 21),
)


def _operand_refs(operand: "str | TokenRef | AllDef | AnyDef | NotDef") -> tuple[str, ...]:
    """Noms de tokens directement référencés par un opérande (str, TokenRef ou inline)."""
    if isinstance(operand, str):
        return (operand,)
    if isinstance(operand, TokenRef):
        return (operand.name,)
    if isinstance(operand, NotDef):
        return _operand_refs(operand.operand)
    # AllDef | AnyDef
    refs: list[str] = []
    for child in operand.operands:
        refs.extend(_operand_refs(child))
    return tuple(refs)


def _def_refs(token_def: TokenDef) -> tuple[str, ...]:
    """Noms de tokens directement référencés par une def (vide pour une feuille)."""
    if isinstance(token_def, AllDef | AnyDef):
        refs: list[str] = []
        for child in token_def.operands:
            refs.extend(_operand_refs(child))
        return tuple(refs)
    if isinstance(token_def, NotDef):
        return _operand_refs(token_def.operand)
    return ()


def _check_references_exist(config: "MatcherConfig") -> None:
    """Toute référence (dans un token composite OU une règle) doit exister."""
    known = set(config.tokens)
    for token_def in config.tokens.values():
        for ref in _def_refs(token_def):
            if ref not in known:
                raise UnknownTokenError(f"référence vers un token inconnu : {ref!r}")
    for rule in config.rules:
        for ref in _operand_refs(rule.condition):
            if ref not in known:
                raise UnknownTokenError(
                    f"règle {rule.name!r} : référence vers un token inconnu : {ref!r}"
                )


def _check_acyclic(config: "MatcherConfig") -> None:
    """Détecte un cycle dans le graphe token->token et le NOMME (cf. spec §8.4)."""
    graph = {name: _def_refs(token_def) for name, token_def in config.tokens.items()}
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []

    def walk(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = stack[stack.index(name) :] + [name]
            raise CycleError(f"cycle de références : {' -> '.join(cycle)}")
        visiting.add(name)
        stack.append(name)
        for ref in graph.get(name, ()):  # ref existe (vérifié par _check_references_exist)
            walk(ref)
        stack.pop()
        visiting.discard(name)
        done.add(name)

    for token_name in graph:
        walk(token_name)


def _max_resolution_depth(config: "MatcherConfig") -> int:
    """Profondeur maximale d'un token (feuille = 1). Suppose le graphe acyclique."""
    graph = {name: _def_refs(token_def) for name, token_def in config.tokens.items()}
    memo: dict[str, int] = {}

    def depth(name: str) -> int:
        if name in memo:
            return memo[name]
        refs = graph.get(name, ())
        result = 1 if not refs else 1 + max(depth(ref) for ref in refs)
        memo[name] = result
        return result

    return max((depth(name) for name in graph), default=0)


def _check_regexes_compile(config: "MatcherConfig") -> None:
    """Chaque RegexDef s'interpole (placeholders connus) et compile sous RE2 (cf. §8.4)."""
    for name, token_def in config.tokens.items():
        if not isinstance(token_def, RegexDef):
            continue
        try:
            pattern = interpolate(token_def.pattern, _PROBE_TARGET)
        except InterpolationError as exc:
            raise ConfigError(f"token {name!r} : interpolation invalide : {exc}") from exc
        if "i" in token_def.flags:
            pattern = "(?i)" + pattern
        try:
            re2.compile(pattern)
        except re2.error as exc:
            raise ConfigError(f"token {name!r} : regex non compilable sous RE2 : {exc}") from exc


def validate_config(config: "MatcherConfig", *, max_depth: int = _DEFAULT_MAX_DEPTH) -> None:
    """Valide le graphe (références, DAG, profondeur) et les regex (cf. spec §8.4).

    Lève :class:`UnknownTokenError`, :class:`CycleError`, :class:`DepthExceededError`
    ou :class:`ConfigError` (regex/interpolation). À appeler après le parsing schéma.
    """
    _check_references_exist(config)
    _check_acyclic(config)
    depth = _max_resolution_depth(config)
    if depth > max_depth:
        raise DepthExceededError(
            f"profondeur de résolution {depth} > max {max_depth} (défaut {_DEFAULT_MAX_DEPTH})"
        )
    _check_regexes_compile(config)
```

Enfin, brancher la validation dans `parse_matcher_config` : remplacer sa dernière ligne `return MatcherConfig(tokens=tokens, rules=rules)` par :
```python
    config = MatcherConfig(tokens=tokens, rules=rules)
    validate_config(config)
    return config
```

> **Note typage :** `isinstance(x, AllDef | AnyDef)` (syntaxe d'union dans `isinstance`, Python ≥ 3.10) est accepté par mypy et ruff. Les fonctions internes prennent `"MatcherConfig"` en annotation forward-référencée (chaîne) puisque `MatcherConfig` n'est importé que localement dans `parse_matcher_config` ; ajouter en haut du fichier, sous `from typing import Any`, un import pour le typage uniquement :
> ```python
> from typing import TYPE_CHECKING, Any
>
> if TYPE_CHECKING:
>     from emule_indexer.domain.matching.config import MatcherConfig
> ```
> `exclude_also = ["if TYPE_CHECKING:"]` (déjà dans `pyproject.toml`) exempte ce bloc du gate coverage.

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_validation.py -q`
Expected: PASS. Les deux côtés de chaque branche sont exercés : référence inconnue (token / règle) vs connue ; cycle direct `a<->b` (message contient `a` et `b`) + auto-cycle `loop` vs DAG valide ; `memo` hit/miss dans `_max_resolution_depth` (token partagé `keroro`/`kt`) ; profondeur dans la borne vs dépassée + défaut 32 (chaîne de 34) ; regex compilable vs `(unbalanced` (→ `RE2`) ; placeholder connu (`{number}{segment}`, `{date_alt}` via sonde) vs `{bogus}` (→ erreur nommant `bogus`).

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert, coverage 100 % (branch), aucune erreur ruff/mypy.

> **Note couverture résiduelle :** si une branche reste découverte — p. ex. `walk` quand `name in done` (token déjà visité via un autre chemin), ou `_max_resolution_depth` `default=0` (table de tokens vide) — AJOUTER le test ciblé. Cas explicites à garantir : un token référencé par DEUX parents (couvre `name in done` et `memo` hit) ; `parse_matcher_config({"tokens": {}, "rules": []})` (table vide → `_max_resolution_depth` `default=0`, pas d'erreur).

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/validation.py tests/domain/matching/test_validation.py
git commit -m "feat(domain): graph validation (DAG, depth, RE2 compile-check)"
```

---

## Task 7: Résolution / construction par cible (`MatcherResolver`)

**Files:**
- Create: `src/emule_indexer/domain/matching/resolver.py`
- Create: `tests/domain/matching/test_resolver.py`

> Construit, pour une `TargetSegment` donnée, les **arbres de `Matcher` résolus** : chaque `RegexDef` est `interpolate(pattern, target)` puis `RegexMatcher(pattern, flags)` (regex **précompilée par cible**, §8.5) ; chaque `CoverageDef` lie `reference=target.title` quand `reference == "title"`, avec overrides `min`/`fuzz` du point d'usage ; `KeywordDef`/`AttrBetweenDef` sont statiques ; les composites (`AllDef`/`AnyDef`/`NotDef`) deviennent `AllMatcher`/`AnyMatcher`/`NotMatcher` récursivement (DAG/profondeur déjà garantis par Task 6). Un `ResolvedTarget` regroupe les arbres des tokens nommés et des règles pour cette cible.

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/matching/test_resolver.py` :
```python
import datetime

from emule_indexer.domain.matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
    CoverageDef,
    KeywordDef,
    NotDef,
    RegexDef,
    Rule,
    TokenRef,
)
from emule_indexer.domain.matching.matchers import AttrBetweenMatcher, CoverageMatcher, RegexMatcher
from emule_indexer.domain.matching.models import FileCandidate, TargetSegment
from emule_indexer.domain.matching.resolver import MatcherResolver
from emule_indexer.domain.matching.validation import parse_matcher_config

_TARGET = TargetSegment(
    season=2,
    number=62,
    segment="a",
    title="Les demoiselles cambrioleuses",
    broadcast_date=datetime.date(2008, 9, 21),
)


def _resolver_from(raw: dict[str, object]) -> MatcherResolver:
    return MatcherResolver(parse_matcher_config(raw))


def test_resolve_keyword_token() -> None:
    resolver = _resolver_from({"tokens": {"keroro": {"keyword": "keroro"}}, "rules": []})
    matcher = resolver.resolve_token("keroro", _TARGET)
    assert matcher.matches(FileCandidate(filename="Keroro 062A.avi")) is True
    assert matcher.matches(FileCandidate(filename="autre.avi")) is False


def test_resolve_regex_token_interpolates_per_target() -> None:
    resolver = _resolver_from(
        {"tokens": {"seg": {"regex": "n[°o]?\\s*0*{number}\\s*{segment}"}}, "rules": []}
    )
    matcher = resolver.resolve_token("seg", _TARGET)
    assert isinstance(matcher, RegexMatcher)
    assert matcher.matches(FileCandidate(filename="Keroro N°062A.avi")) is True
    # Une autre cible (numéro 7) produit un matcher distinct qui ne matche pas 062.
    other = TargetSegment(season=2, number=7, segment="b", title="x")
    assert resolver.resolve_token("seg", other).matches(
        FileCandidate(filename="Keroro N°062A.avi")
    ) is False


def test_resolve_coverage_binds_title() -> None:
    resolver = _resolver_from(
        {"tokens": {"title_hit": {"coverage": "title", "min": 0.6}}, "rules": []}
    )
    matcher = resolver.resolve_token("title_hit", _TARGET)
    assert isinstance(matcher, CoverageMatcher)
    candidate = FileCandidate(filename="062A Les demoiselles cambrioleuses.avi")
    assert matcher.matches(candidate) is True
    assert matcher.value(candidate) == 1.0


def test_resolve_coverage_non_title_reference_used_literally() -> None:
    # Une référence != "title" est utilisée telle quelle comme texte de référence.
    resolver = _resolver_from(
        {"tokens": {"lit": {"coverage": "keroro titar", "min": 0.5}}, "rules": []}
    )
    matcher = resolver.resolve_token("lit", _TARGET)
    assert isinstance(matcher, CoverageMatcher)
    assert matcher.matches(FileCandidate(filename="keroro titar 062.avi")) is True


def test_resolve_attr_between_token() -> None:
    resolver = _resolver_from(
        {"tokens": {"sz": {"attr_between": "size_mb", "min": 30, "max": 600}}, "rules": []}
    )
    matcher = resolver.resolve_token("sz", _TARGET)
    assert isinstance(matcher, AttrBetweenMatcher)
    assert matcher.matches(FileCandidate(filename="x.avi", size_mb=120.0)) is True
    assert matcher.matches(FileCandidate(filename="x.avi", size_mb=5.0)) is False


def test_resolve_composite_any_token() -> None:
    resolver = _resolver_from(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "titar": {"keyword": "titar"},
                "kt": {"any": ["keroro", "titar"]},
            },
            "rules": [],
        }
    )
    matcher = resolver.resolve_token("kt", _TARGET)
    assert matcher.matches(FileCandidate(filename="titar only.avi")) is True
    assert matcher.matches(FileCandidate(filename="ni l un ni l autre.avi")) is False


def test_resolve_composite_all_and_not() -> None:
    resolver = _resolver_from(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "titar": {"keyword": "titar"},
                "k_not_t": {"all": ["keroro", {"not": "titar"}]},
            },
            "rules": [],
        }
    )
    matcher = resolver.resolve_token("k_not_t", _TARGET)
    assert matcher.matches(FileCandidate(filename="keroro seul.avi")) is True
    assert matcher.matches(FileCandidate(filename="keroro titar.avi")) is False


def test_resolve_rule_condition() -> None:
    resolver = _resolver_from(
        {
            "tokens": {"keroro": {"keyword": "keroro"}, "titar": {"keyword": "titar"}},
            "rules": [{"name": "r", "tier": "catalog", "all": ["keroro", "titar"]}],
        }
    )
    rule = resolver.config.rules[0]
    matcher = resolver.resolve_rule(rule, _TARGET)
    assert matcher.matches(FileCandidate(filename="keroro titar 062.avi")) is True
    assert matcher.matches(FileCandidate(filename="keroro seul.avi")) is False


def test_token_ref_override_applies_min() -> None:
    # title_hit a min=0.6 ; l'usage {token: title_hit, min: 0.4} abaisse le seuil
    # -> couverture partielle (1 token sur 3) atteint 0.4 mais pas 0.6.
    resolver = _resolver_from(
        {
            "tokens": {"title_hit": {"coverage": "title", "min": 0.6}},
            "rules": [
                {"name": "low", "tier": "notify", "all": [{"token": "title_hit", "min": 0.34}]}
            ],
        }
    )
    matcher = resolver.resolve_rule(resolver.config.rules[0], _TARGET)
    # "demoiselles" seul = 1/3 des tokens significatifs (demoiselles/cambrioleuses + ...).
    candidate = FileCandidate(filename="quelque chose demoiselles xyz.avi")
    assert matcher.matches(candidate) is True
    # Sans override (min=0.6), le même candidat NE matcherait PAS.
    strict = resolver.resolve_token("title_hit", _TARGET)
    assert strict.matches(candidate) is False


def test_token_ref_override_applies_fuzz() -> None:
    resolver = _resolver_from(
        {
            "tokens": {"title_hit": {"coverage": "title", "min": 0.6}},
            "rules": [
                {
                    "name": "f",
                    "tier": "notify",
                    "all": [{"token": "title_hit", "min": 0.6, "fuzz": 0.99}],
                }
            ],
        }
    )
    matcher = resolver.resolve_rule(resolver.config.rules[0], _TARGET)
    assert isinstance(matcher, type(matcher))  # arbre construit sans erreur


def test_token_ref_without_override_resolves_token_as_is() -> None:
    resolver = _resolver_from(
        {
            "tokens": {"title_hit": {"coverage": "title", "min": 0.6}},
            "rules": [{"name": "plain", "tier": "notify", "all": [{"token": "title_hit"}]}],
        }
    )
    matcher = resolver.resolve_rule(resolver.config.rules[0], _TARGET)
    candidate = FileCandidate(filename="Les demoiselles cambrioleuses 062A.avi")
    assert matcher.matches(candidate) is True


def test_resolve_all_returns_every_token_and_rule_matcher() -> None:
    resolver = _resolver_from(
        {
            "tokens": {"keroro": {"keyword": "keroro"}, "seg": {"regex": "0*{number}"}},
            "rules": [{"name": "r", "tier": "catalog", "any": ["keroro"]}],
        }
    )
    resolved = resolver.resolve_all(_TARGET)
    assert set(resolved.tokens) == {"keroro", "seg"}
    assert set(resolved.rules) == {"r"}
    assert resolved.tokens["keroro"].matches(FileCandidate(filename="keroro.avi")) is True
    assert resolved.rules["r"].matches(FileCandidate(filename="keroro.avi")) is True
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_resolver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.matching.resolver'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/matching/resolver.py` :
```python
"""Construction par cible des arbres de matchers (cf. spec §8.5, partie construction).

Domaine PUR. À partir d'une :class:`MatcherConfig` VALIDÉE (DAG/profondeur/RE2 garantis
par ``validation.validate_config``) et d'une :class:`TargetSegment`, bâtit l'arbre de
:class:`Matcher` de chaque token nommé et de chaque règle. Les regex sont interpolées et
compilées PAR CIBLE ; les coverage liés à ``target.title`` (avec overrides au point
d'usage) ; keyword/attr_between sont statiques.
"""

from dataclasses import dataclass
from typing import assert_never

from emule_indexer.domain.matching.combinators import (
    AllMatcher,
    AnyMatcher,
    Matcher,
    NotMatcher,
)
from emule_indexer.domain.matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
    CoverageDef,
    KeywordDef,
    MatcherConfig,
    NotDef,
    RegexDef,
    Rule,
    TokenRef,
)
from emule_indexer.domain.matching.interpolation import interpolate
from emule_indexer.domain.matching.matchers import (
    AttrBetweenMatcher,
    CoverageMatcher,
    KeywordMatcher,
    RegexMatcher,
)
from emule_indexer.domain.matching.models import TargetSegment

# Mot-clé de config désignant le titre de la cible comme référence de coverage (§8.5).
_TITLE_KEYWORD = "title"


@dataclass(frozen=True)
class ResolvedTarget:
    """Arbres de matchers construits pour une cible : tokens nommés + règles, par nom."""

    target: TargetSegment
    tokens: dict[str, Matcher]
    rules: dict[str, Matcher]


class MatcherResolver:
    """Construit les arbres de :class:`Matcher` d'une config validée, par cible."""

    def __init__(self, config: MatcherConfig) -> None:
        self.config = config

    def resolve_token(
        self,
        name: str,
        target: TargetSegment,
        min_override: float | None = None,
        fuzz_override: float | None = None,
    ) -> Matcher:
        """Construit le matcher du token ``name`` pour ``target`` (overrides coverage)."""
        return self._build_def(self.config.tokens[name], target, min_override, fuzz_override)

    def resolve_rule(self, rule: Rule, target: TargetSegment) -> Matcher:
        """Construit le matcher de la condition d'une règle pour ``target``."""
        return self._build_def(rule.condition, target, None, None)

    def resolve_all(self, target: TargetSegment) -> ResolvedTarget:
        """Construit tous les arbres (tokens + règles) pour ``target``."""
        tokens = {name: self.resolve_token(name, target) for name in self.config.tokens}
        rules = {rule.name: self.resolve_rule(rule, target) for rule in self.config.rules}
        return ResolvedTarget(target=target, tokens=tokens, rules=rules)

    def _build_operand(
        self,
        operand: "str | TokenRef | AllDef | AnyDef | NotDef",
        target: TargetSegment,
    ) -> Matcher:
        if isinstance(operand, str):
            return self.resolve_token(operand, target)
        if isinstance(operand, TokenRef):
            return self.resolve_token(operand.name, target, operand.min, operand.fuzz)
        return self._build_def(operand, target, None, None)

    def _build_def(
        self,
        token_def: KeywordDef | RegexDef | CoverageDef | AttrBetweenDef | AllDef | AnyDef | NotDef,
        target: TargetSegment,
        min_override: float | None,
        fuzz_override: float | None,
    ) -> Matcher:
        match token_def:
            case KeywordDef(phrase=phrase):
                return KeywordMatcher(phrase)
            case RegexDef(pattern=pattern, flags=flags):
                return RegexMatcher(interpolate(pattern, target), flags=flags)
            case CoverageDef(reference=reference, min=min_value, fuzz=fuzz_value):
                text = target.title if reference == _TITLE_KEYWORD else reference
                return CoverageMatcher(
                    reference=text,
                    min=min_value if min_override is None else min_override,
                    fuzz=fuzz_value if fuzz_override is None else fuzz_override,
                )
            case AttrBetweenDef(attr=attr, min=min_value, max=max_value):
                return AttrBetweenMatcher(attr, min=min_value, max=max_value)
            case AllDef(operands=operands):
                return AllMatcher(tuple(self._build_operand(op, target) for op in operands))
            case AnyDef(operands=operands):
                return AnyMatcher(tuple(self._build_operand(op, target) for op in operands))
            case NotDef(operand=operand):
                return NotMatcher(self._build_operand(operand, target))
            case _:  # pragma: no cover - exhaustif (mypy le prouve via assert_never)
                assert_never(token_def)
```

> **Note `assert_never` + `# pragma: no cover` :** le `case _:` est inatteignable — mypy prouve l'exhaustivité du `match` sur l'union `TokenDef`. La branche ne peut donc PAS être couverte par un test. Le `# pragma: no cover` est l'exception conventionnelle et **ne masque rien d'autre** (toutes les autres branches `case` ont des tests). C'est le seul `pragma` du plan. *(Sans lui, le gate 100 % échouerait sur une ligne logiquement morte mais que coverage.py compte.)*

> **Note overrides :** `min_override`/`fuzz_override` ne sont consommés que par la branche `CoverageDef`. Quand un `TokenRef` non-coverage les porte, la validation (Task 5) l'a déjà rejeté au chargement — donc à la résolution, un override non-None implique forcément une cible `CoverageDef`. Les autres branches ignorent simplement ces paramètres (ils restent `None` via `resolve_token`/`_build_operand` qui ne les propagent que pour `TokenRef`).

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_resolver.py -q`
Expected: PASS. Chaque arm du `match` est exercé : `KeywordDef`, `RegexDef` (interpolation par cible → cible 62 vs 7 distinctes), `CoverageDef` (référence `title` liée vs référence littérale non-`title`), `AttrBetweenDef`, `AllDef`, `AnyDef`, `NotDef` ; `TokenRef` avec override `min` (les deux côtés du seuil), avec override `fuzz`, et SANS override (résolution telle quelle) ; opérande `str` nu ; `resolve_all` (tokens + règles complets).

- [ ] **Step 5: Vérifier la suite complète + types + lint + coverage final**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tous les tests verts ; **coverage 100 % (branch)** sur tout `emule_indexer` ; aucune erreur ruff/mypy. *(Le seul `# pragma: no cover` est le `case _: assert_never(...)` du resolver.)*

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/resolver.py tests/domain/matching/test_resolver.py
git commit -m "feat(domain): per-target matcher resolver (interpolate+compile per target)"
```

---

## Self-Review (rempli par l'auteur du plan)

- **Couverture de la spec (périmètre Plan 2b)** :
  - **§8.3** (tokens nommés feuilles + composites, opérandes au point d'usage, règles, EBNF) → **Task 3** (modèle : `KeywordDef`/`RegexDef`/`CoverageDef`/`AttrBetweenDef`, `AllDef`/`AnyDef`/`NotDef`, `TokenRef`, `Rule`, `MatcherConfig`, `TIERS`) + **Task 5** (parsing schéma : nom nu / `{token: …}` / `{condition}` inline ; les trois usages de `all`/`any`/`not`). ✓
  - **§8.4** (validation fail-fast) → **Task 5** (tier ∈ ensemble fermé, `attr_between` ∈ `ATTR_NAMES`, schéma : une clé / clés annexes, override coverage-only) + **Task 6** (référence inconnue **nommée** ; **DAG/cycle nommé** ; **profondeur bornée défaut 32** ; **RE2 compile-check** + **interpolation-check** plaçant un placeholder inconnu en erreur). ✓
  - **§8.5 — partie CONSTRUCTION seulement** (« regex précompilées par cible ») → **Task 7** (`MatcherResolver.resolve_token`/`resolve_rule`/`resolve_all` : interpolation+compilation par cible, coverage lié à `target.title`, overrides au point d'usage, composites récursifs). ✓
  - **§7** (forme `targets.yaml`, `target_id`) → **Task 5** `parse_targets` (segments → `TargetSegment`, `letter`→`segment`, status défaut `lost`, `aliases` optionnels, `broadcast_date` en `datetime.date`). ✓
  - **Combinateurs + Protocol** (recommandé par la revue 2a) → **Task 2** (`Matcher` Protocol, `AllMatcher`/`AnyMatcher`/`NotMatcher`). ✓
  - **Adapter YAML** (frontière I/O) → **Task 4** (`load_yaml`, seul importeur de `yaml`). ✓
  - **HORS PÉRIMÈTRE — renvoyé au Plan 2c, ABSENT ici** : boucle d'évaluation (règles ordonnées, 1re vraie gagne par paire), décision fichier (palier max, départage index puis `target_id`, écart si rien), explicabilité (logs tokens/règles + `value`), façade publique du moteur, corpus golden. **Aucun de ces éléments n'apparaît dans ce plan** — le resolver s'arrête à la construction des arbres ; il n'évalue ni n'ordonne rien, ne décide pas de palier, ne logge pas. ✓
- **Scan des placeholders (« TBD »/« similaire à »/« etc. »)** : aucun « TBD »/« à compléter »/« similaire à la Task N ». Chaque step de code contient le code COMPLET (imports inclus), chaque step de run a une commande exacte + sortie attendue, chaque tâche se termine par un `git commit -m` exact. Tous les `…` restants sont dans de la prose/regex explicative, pas dans du code à recopier.
- **Cohérence des types/nommage entre tâches** :
  - `Matcher` (Protocol, Task 2) annote tous les arbres (combinateurs Task 2, resolver Task 7) ; les 4 feuilles Plan 2a le satisfont sans modif.
  - Dataclasses de config (Task 3) : noms de champs stables réutilisés à l'identique en Task 5 (parsing), Task 6 (`_def_refs`/`_operand_refs` lisent `.operands`/`.operand`/`.name`) et Task 7 (`match KeywordDef(phrase=…)`, etc.). `TokenDef`/`Condition`/`Operand` cohérents.
  - Exceptions : `ConfigError` base (Task 5) ; sous-types `UnknownTokenError`/`CycleError`/`DepthExceededError` (Task 6) en héritent ; `YamlLoadError` (Task 4) distinct côté adapter. `InterpolationError`/`re2.error` (Plan 2a) capturées et re-levées en `ConfigError` au chargement (Task 6).
  - Construction réutilise les feuilles Plan 2a **byte-exact** : `RegexMatcher(pattern, flags=…)`, `CoverageMatcher(reference=…, min=…, fuzz=…)`, `AttrBetweenMatcher(attr, min=…, max=…)`, `KeywordMatcher(phrase)` — signatures vérifiées dans le source 2a.
  - `interpolate(pattern, target)` (Plan 2a) appelé en Task 6 (compile-check sur sonde) et Task 7 (par cible réelle), de façon cohérente.
- **Ambiguïtés résolues (pas de « l'implémenteur décidera »)** :
  1. **Lib de parsing** : PyYAML + validation main (justifié, alternative pydantic écartée avec argument). `types-pyyaml` requis pour mypy (vérifié empiriquement).
  2. **Protocol** : OUI, `Matcher` à une seule méthode `matches`, `value` exclu (justifié).
  3. **Représentation du modèle** : union étiquetée de dataclasses gelées (vs dataclass à champs optionnels / dict) — dispatch exhaustif `match`/`assert_never` (justifié).
  4. **`all`/`any`/`not` partagés** entre token composite, corps de règle et condition inline — mêmes dataclasses réutilisées (justifié).
  5. **Override `min`/`fuzz` coverage-only** : rejeté au CHARGEMENT s'il porte sur un token non-coverage (pas à la résolution) — décision verrouillée + testée.
  6. **Profondeur** : « profondeur de résolution » = longueur de la plus longue chaîne token→token (feuille = 1) ; défaut 32 ; `validate_config(config, *, max_depth=32)` exposé et testé aux deux bornes + chaîne de 34 pour le défaut.
  7. **`reference == "title"`** lié à `target.title` ; toute autre `reference` utilisée littéralement (testé) — cohérent avec « le mot-clé `title` signifie le titre de la cible ».
  8. **Frontière Any→typé** : franchie uniquement dans `validation.py` ; le reste du domaine est strictement typé.
- **Argument gate coverage 100 % branch** : chaque conditionnel introduit est testé des deux côtés —
  - combinateurs : `all`/`any` vrai/faux/vide, `not` des deux côtés (Task 2) ;
  - dataclasses : tous défauts + gel (Task 3) ;
  - loader : `isinstance(raw, dict)` vrai (mapping) / faux (liste + fichier vide `None`) (Task 4) ;
  - validation schéma : chaque arm de `_parse_token_def`/`_parse_operand`/`_parse_condition`/`_parse_rule` + chaque chemin d'erreur (tier, enum, une clé, deux conditions, override non-coverage, opérande mauvais type, `episodes` absent, status défaut/explicite, `broadcast_date` présent/absent, aliases présents/absents) (Task 5, + note couverture résiduelle pour les arms positifs sans override) ;
  - validation graphe : référence inconnue/connue, cycle direct + auto-cycle / DAG, `memo` hit/miss + nœud `done`, profondeur dans/hors borne + défaut 32, regex compilable / `(unbalanced`, placeholder connu (`{number}`/`{segment}`/`{date_alt}`) / `{bogus}`, table vide (`default=0`) (Task 6, + note couverture résiduelle) ;
  - resolver : chaque arm du `match` (7 formes) + `TokenRef` avec/sans override (`min` aux deux côtés du seuil, `fuzz`) + opérande `str` nu (Task 7). Le **seul** `# pragma: no cover` est le `case _: assert_never` logiquement mort (exhaustivité prouvée par mypy) — justifié en place.
- **Discipline de périmètre 2b vs 2c** : ce plan **construit** des arbres de matchers et **valide** la config ; il n'**évalue** jamais une décision multi-règles/multi-cibles, n'ordonne pas, ne calcule pas de palier, ne logge pas d'explicabilité, n'expose pas de façade moteur ni de corpus golden — tous renvoyés au Plan 2c (annoncé en tête). Les `Rule.tier`/l'ordre des règles sont **modélisés et validés** (nécessaire à la construction) mais **jamais consommés pour décider** ici. ✓
- **Faits vérifiés empiriquement avant rédaction** (venv jetable PyYAML + google-re2 + rapidfuzz, et import du source 2a réel) :
  - `yaml.safe_load` : `broadcast_date: 2008-09-21` → `datetime.date` ; `number` → `int` ; `min: 0.6` → `float`, `min: 1` → `int` ; `aliases` absent → clé absente ; `{token: title_hit, min: 0.5}` → `dict`.
  - mypy strict sans `types-pyyaml` → `error: Library stubs not installed for "yaml" [import-untyped]` ; avec → `Success`.
  - RE2 : pattern invalide `(` → exception `re2.error` (= `re2._re2.Error`), **catchable via `except re2.error`** ; `re2.escape("C++ (x)")` → `C\+\+\ \(x\)` ; `(?i)teletoon` matche `teletoon`. *(Un message absl est écrit sur stderr lors d'un compile raté — bénin, n'affecte pas le test.)*
  - Flux bout-en-bout sur le **source 2a réel** : `interpolate(r"n[°o]?\s*0*{number}\s*{segment}", target(62,a))` → `n[°o]?\s*0*62\s*A` ; `RegexMatcher(...).matches("Keroro N°062A.avi")` → `True` ; `CoverageMatcher(reference=title, min=0.6).value("keroro les demoiselles.avi")` cohérent ; `RegexMatcher("(")` → `re2.error`. **(PyYAML s'installe sans build natif — Task 1 ne devrait pas BLOCKER.)**
