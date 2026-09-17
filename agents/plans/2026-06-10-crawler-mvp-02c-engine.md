# Crawler MVP — Plan 2c : Moteur d'évaluation, explicabilité & corpus golden — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** **Compléter le moteur de matching (« le joyau »)** en posant la couche d'**ÉVALUATION** au-dessus des arbres de matchers construits par le Plan 2b. Concrètement, en domaine PUR : (1) un **rang de palier** ordonné (`download > notify > catalog`) pour comparer des `tier` issus du `frozenset` non ordonné `TIERS` ; (2) un **modèle de décision** gelé (`MatchDecision`) portant `tier`, `rule_name`, `target_id` (les colonnes exactes que `match_decisions` persistera, §11) + une **explication** (règles/tokens déclenchés et `value` des coverage de la cible gagnante) ; (3) l'**évaluation par paire `(fichier, cible)`** : 1re règle vraie dans l'ORDRE → `(index, nom, tier)` ; (4) la **décision fichier déterministe** (§8.5) : palier le plus haut, départage par index de règle puis `target_id`, aucune règle vraie nulle part → fichier écarté (`None`) ; (5) l'**explicabilité** (§8.5 : « chaque décision logge tokens/règles déclenchés + `value` des coverage ») exposée comme une structure RETOURNÉE (pas de logging — c'est une préoccupation d'adapter, plan ultérieur) ; (6) une **façade moteur** pure (`MatchingEngine`) qui PRÉ-RÉSOUT chaque cible une fois à la construction (via `MatcherResolver`) puis `evaluate(candidate) -> MatchDecision | None` en brute-force (chaque fichier × chaque cible, §8.5, aucune heuristique d'entonnoir) ; (7) un **corpus golden** (§16) data-driven (réels + forgés : accents/encodages, quasi-collisions, leurres, ép. 62A) épinglé bout-en-bout contre la config canonique §8.3 et les vraies cibles ; (8) des **invariants property-based** (§16 : « une règle plus prioritaire ne baisse jamais le palier », déterminisme sous réordonnancement des cibles), **faits main** et **déterministes** (shuffle seedé, zéro flakiness).

**Architecture:** Couche `domain/` PURE (Clean/Hexagonal). Le moteur d'évaluation prend une `MatcherConfig` **déjà parsée/validée** (Plan 2b) + une séquence de `TargetSegment`, pré-résout les arbres une fois à la construction, et rend une **décision EN MÉMOIRE**. **Aucune I/O** : pas de DB (la persistance de `match_decisions` est le plan modèle-de-données), pas de notification/apprise (§13), pas de téléchargement (§9), pas de métriques Prometheus, **pas de logging** — l'« explicabilité loggée en DEBUG » de §8.5 signifie que le moteur **retourne un résultat explicable** (la `MatchDecision` porte l'explication) ; le logging effectif est l'affaire d'un adapter d'un plan ultérieur. Le moteur reste le « joyau » : TDD strict, gate de coverage **branch 100 %**. Le déterminisme du palier (§8.5/§16) est **pinné par des tests** : deux cibles au même palier départagées par index de règle ; même index départagé par `target_id` ; et le **réordonnancement seedé** des cibles ne change jamais la décision.

**Tech Stack:** Python ≥ 3.12, `uv` (projet/paquets), `ruff` (lint+format, `select=["E","F","I","UP","B","SIM"]`, line-length 100), `mypy --strict` (`files=["src","tests"]`), `pytest` + `pytest-cov` (coverage **branch**, seuil **100 %** imposé : `--cov-fail-under=100`), `google-re2` (RE2, importé `re2`), `rapidfuzz`, `PyYAML` (déjà présents depuis les Plans 2a/2b). **Aucune nouvelle dépendance** (décision verrouillée ci-dessous : property-based **fait main**, pas de `hypothesis`). Le corpus golden est un **fixture YAML** chargé via l'adapter `load_yaml` existant (Plan 2b).

> **Référence spec :** `docs/superpowers/specs/2026-06-10-crawler-mvp-design.md` — **§8.5** (partie **ÉVALUATION** : brute-force par cible, 1re règle vraie par paire, décision fichier = palier max avec départage index puis `target_id`, écart si rien, explicabilité tokens/règles + `value` des coverage, bornage de la longueur du nom), **§11** (la ligne `match_decisions(id, ed2k_hash FK, target_id, rule_name, tier, decided_at, node_id)` — le moteur DOIT exposer `target_id`, `rule_name`, `tier`), **§16** (corpus golden de noms réels + forgés → palier/cible attendus, extensible par la communauté ; property-based « une règle plus prioritaire ne baisse jamais le palier » ; déterminisme : shuffle seedé → zéro flakiness ; gate coverage statement+branch). S'appuie sur le **Plan 2a** (tagué `v0.2.0-matchers`) et le **Plan 2b** (tagué `v0.3.0-config-graph`).

> **Le Plan 2c COMPLÈTE le moteur de matching (« le joyau »).** Ce qui le CONSOMME arrive dans des plans ULTÉRIEURS et est **HORS PÉRIMÈTRE ici** : la **persistance** des décisions dans `catalog.db`/`match_decisions` (§11, plan modèle-de-données) ; la **politique d'auto-download** (§9 : « tout sauf complet », garde-fous, upgrades) ; les **notifications apprise** (§13) ; les **métriques Prometheus** (`matches_total{tier}`…) ; le **logging structuré** (le moteur RETOURNE l'explication, il ne logge pas) ; l'**adapter EC**, l'**orchestration des recherches** (§6), le **verifier/confinement** (§10). Aucun de ces éléments n'apparaît dans ce plan.

---

## File Structure

Décisions verrouillées (ne pas dévier) :

> **DÉCISION 1 — Emplacement du rang de palier → `domain/matching/engine.py`, PAS `config.py`.**
> `TIERS` (frozenset non ordonné) vit déjà dans `config.py` et exprime l'**ensemble fermé valide** d'un `tier` (préoccupation de *validation* : « ce tier est-il licite ? »). Le **rang** `download > notify > catalog` est une préoccupation de *décision* (§8.5 : « palier le plus haut »), pas de validation. On le co-localise donc avec le moteur dans `engine.py` sous la forme `_TIER_RANK: dict[str, int] = {"catalog": 0, "notify": 1, "download": 2}` (entier croissant = palier plus haut). Justification : (a) garde `config.py` focalisé sur le *modèle* + l'*ensemble licite* ; (b) le rang est l'invariant central du moteur — le tester (les deux sens de chaque comparaison) là où il est utilisé donne une couverture lisible ; (c) un test de **cohérence** vérifie `set(_TIER_RANK) == TIERS` (si un palier était ajouté à `TIERS` sans rang, le test casse — fail-fast). Le rang est `dict` (pas tuple ordonné) car la comparaison se fait par lookup `_TIER_RANK[tier]`, O(1), sans `.index()`.

> **DÉCISION 2 — Forme du modèle de décision → `MatchDecision` (dataclass gelée) + `evaluate(...) -> MatchDecision | None`.**
> Champs : `target_id: str`, `rule_name: str`, `tier: str` (les **trois colonnes** exactes que `match_decisions` persiste, §11) ; **plus** l'explicabilité embarquée `explanation: Explanation` (DÉCISION 3). « Aucun match → fichier écarté » est représenté par le **retour `None`** d'`evaluate` (pas un `MatchDecision` sentinelle, pas d'exception) : c'est le plus propre et le plus typé (`MatchDecision | None`, mypy force le `if decision is None` côté appelant). Pas de `decided_at`/`node_id`/`ed2k_hash` dans le moteur : ce sont des colonnes de **persistance** (horloge + identité de nœud + clé de contenu), injectées par l'adapter du plan modèle-de-données — les inclure ici fuiterait le périmètre et imposerait une `Clock` au domaine pur d'évaluation. Décision verrouillée : `MatchDecision` ne porte QUE `target_id`, `rule_name`, `tier`, `explanation`.

> **DÉCISION 3 — Forme de l'explication → `Explanation` gelée portant la cible gagnante : `rules_fired`, `tokens_matched`, `coverage_values`.**
> §8.5 : « chaque décision logge tokens/règles déclenchés + `value` des coverage ». L'explication concerne la **cible gagnante** (celle de la décision retournée) — pas le produit cartésien complet (qui serait du bruit et coûteux). Champs de `Explanation` :
> - `target_id: str` — redondant avec `MatchDecision.target_id` mais autonome (une `Explanation` se lit seule en DEBUG).
> - `rules_fired: tuple[str, ...]` — noms des règles vraies pour la cible gagnante, **dans l'ordre de la config** (la 1re est la gagnante ; les suivantes documentent les autres déclenchements).
> - `tokens_matched: tuple[str, ...]` — noms des **tokens nommés** de la config qui matchent le candidat pour la cible gagnante (trié par nom, déterministe). Source : `resolved.tokens` (Plan 2b), `.matches(candidate)`.
> - `coverage_values: tuple[tuple[str, float], ...]` — pour chaque token nommé qui est un `CoverageMatcher` (test `isinstance`), `(nom, value(candidate))`, trié par nom. C'est ici qu'on lit `CoverageMatcher.value()` (hors Protocol, §8.5/Plan 2b note). `tuple[tuple[str, float], ...]` plutôt qu'un `dict` pour garder l'`Explanation` **gelée et hashable** et l'ordre déterministe.
>
> Justification : structure **pragmatique** (assez pour déboguer une décision en DEBUG : pourquoi ce palier ? quels tokens ont porté ? quelle couverture du titre ?) sans sur-ingénierie (pas d'arbre d'évaluation complet par token composite, pas de trace du produit cartésien). L'explication est **calculée uniquement pour la cible gagnante** quand `evaluate` aboutit (donc jamais sur un fichier écarté → pas de coût sur les leurres).

> **DÉCISION 4 — Façade moteur → `MatchingEngine(config, targets)` qui PRÉ-RÉSOUT à la construction, puis `evaluate(candidate)`.**
> Le constructeur instancie **un** `MatcherResolver(config)` et appelle `resolve_all(target)` pour **chaque** cible **une seule fois**, stockant un `tuple[ResolvedTarget, ...]` (regex interpolées+compilées par cible au chargement, §8.5). `evaluate(candidate)` fait le brute-force §8.5 : pour chaque `ResolvedTarget`, trouver la 1re règle vraie (dans l'ordre de `config.rules`, en zippant `resolved.rules[rule.name]` avec `config.rules` pour récupérer `tier` + index), puis agréger en décision fichier (palier max ; départage index puis `target_id`). **Un seul point d'entrée public riche** (`evaluate -> MatchDecision | None`) plutôt qu'un split `evaluate`/`explain` : l'explication est calculée **paresseusement** pour la seule cible gagnante à l'intérieur d'`evaluate` et embarquée dans la `MatchDecision`, ce qui évite un second parcours et garde une API minimale. Décision verrouillée : **pas** de méthode `explain` séparée ; l'explication est un champ de la décision.

> **DÉCISION 5 — Format du corpus golden → fixture **YAML** (`tests/fixtures/golden_corpus.yaml`) chargé via `load_yaml` + config canonique YAML (`tests/fixtures/canonical_config.yaml`) + cibles YAML (`tests/fixtures/canonical_targets.yaml`), pilotés par un test paramétré.**
> §16 exige un corpus « **extensible par la communauté** ». Un fixture **YAML de données** (nom de fichier → palier/cible attendus, ou `discarded: true`) est éditable **sans toucher au code Python** (une PR communautaire ajoute une entrée), ce qui réalise littéralement « extensible par la communauté » — supérieur à des `pytest.mark.parametrize` codés en dur. On charge la **config canonique §8.3** et les **cibles canoniques §7** depuis des fixtures YAML eux aussi (réutilise `load_yaml` + `parse_matcher_config` + `parse_targets` du Plan 2b → teste **bout-en-bout** la chaîne réelle, pas une config bricolée en Python). Le test paramétré lit les trois fixtures, construit **un** `MatchingEngine`, et pour chaque cas du corpus compare la décision réelle à l'attendu. Justification : (a) extensibilité communautaire native ; (b) bout-en-bout sur la vraie chaîne YAML→validation→résolution→évaluation (dé-risque l'intégration des Plans 2a/2b/2c) ; (c) `load_yaml`/`parse_*` déjà testés (Plan 2b) → on s'appuie dessus. Le chargement des fixtures est un **helper de test**, pas du code de prod (le domaine reste pur). Format d'un cas : voir Task 6.

> **DÉCISION 6 — Property-based → **FAIT MAIN** (générateurs déterministes seedés via `random.Random(seed)`), **PAS `hypothesis`**.**
> §16 valorise les invariants property-based pour le joyau MAIS exige « **zéro flakiness** » et « shuffle **seedé** ». Une dépendance `hypothesis` apporterait du shrinking mais (a) ajoute une dépendance dev non triviale contre la consigne « dépendance-minimale / doit comprendre tout le code », (b) son flux par défaut explore un espace non borné — on veut au contraire un **ensemble fini, reproductible, seedé**. On écrit donc des **property-checks faits main** : un `random.Random(SEED)` (seed constant) génère un échantillon borné d'entrées/permutations ; chaque propriété est une boucle d'assertions déterministe. Invariants couverts : (P1) **réordonner les cibles ne change jamais la décision** (permutations seedées des mêmes cibles → même `MatchDecision`) ; (P2) **monotonie du palier** : si une règle d'index plus petit (donc évaluée d'abord) de palier ≥ devient vraie pour une cible, le palier résultant ne baisse jamais (construit par comparaison de deux configs où l'on « active » une règle plus prioritaire). **Décision verrouillée : aucune nouvelle dépendance ; seed constant exporté dans le test ; chaque run est identique.** *(Si plus tard une exploration plus large est souhaitée, `hypothesis` pourra être ajoutée — hors périmètre 2c.)*

> **DÉCISION 7 — Bornage de la longueur du nom (§8.5/§14 « bornage de la longueur du nom avant matching ») → **DANS LE MOTEUR**, configurable, défaut `4096`, comportement = **écarter** (`return None`) au-delà.**
> La spec liste le bornage comme défense-en-profondeur (RE2 est déjà linéaire). On le met **dans le moteur** plutôt que de le déférer, car `evaluate` est le point exact « avant matching » : un nom au-delà de la borne ne doit même pas atteindre les regex. Forme : `MatchingEngine(config, targets, *, max_filename_length: int = 4096)`. Comportement : `len(candidate.filename) > max_filename_length` → `evaluate` retourne **`None`** (fichier écarté, comme « aucune règle vraie ») — pas d'exception (un nom monstrueux est un *input hostile attendu* §10.2, pas une erreur de programmation) et pas de troncature (tronquer pourrait faire matcher un préfixe et fausser la décision ; écarter est conservateur et sûr). Défaut `4096` = très au-delà d'un basename plausible (un nom eD2k réaliste fait < 256 octets) tout en étant un plafond dur contre un input pathologique. **Les DEUX côtés testés** : nom court (≤ borne) → évalué normalement ; nom > borne → `None` même s'il aurait matché. *(Un bornage additionnel côté ingest/EC — refuser/tronquer en amont — reste possible dans le plan EC ; il serait redondant mais complémentaire. Le bornage moteur est l'autorité de dernier recours.)*

**Layout des modules** (style Plans 2a/2b : fichiers petits, une responsabilité) :

- `src/emule_indexer/domain/matching/engine.py` — **Create** : `MatchDecision` (gelée : `target_id`, `rule_name`, `tier`, `explanation`), `Explanation` (gelée : `target_id`, `rules_fired`, `tokens_matched`, `coverage_values`), `_TIER_RANK`, et `MatchingEngine` (pré-résolution à la construction, `evaluate(candidate) -> MatchDecision | None`, bornage de longueur). Domaine PUR : aucun I/O, aucun logging, aucune DB.
- `tests/domain/matching/test_engine.py` — **Create** : tests unitaires du moteur — rang de palier (les deux sens + cohérence avec `TIERS`), évaluation par paire (1re vraie / aucune vraie), décision fichier (palier max des deux sens ; départage par index ; départage par `target_id` ; écart), explicabilité (`rules_fired`/`tokens_matched`/`coverage_values`, branche coverage / non-coverage), bornage de longueur (sous / au-delà), pré-résolution (regex compilées une fois).
- `tests/domain/matching/test_engine_properties.py` — **Create** : property-checks faits main, seedés (P1 réordonnancement, P2 monotonie de palier).
- `tests/fixtures/__init__.py` — **Create** : marque le paquet (cohérence avec la structure de tests existante) — ou simple présence du dossier ; ce fichier garde le dossier `fixtures` importable si besoin (helpers).
- `tests/fixtures/canonical_config.yaml` — **Create** : la config matcher canonique **verbatim §8.3** (tokens + règles).
- `tests/fixtures/canonical_targets.yaml` — **Create** : les cibles canoniques **§7** (S2E062A + S2E062B au minimum).
- `tests/fixtures/golden_corpus.yaml` — **Create** : le corpus golden data-driven (réels + forgés), extensible.
- `tests/domain/matching/test_golden_corpus.py` — **Create** : charge les 3 fixtures via `load_yaml` + `parse_matcher_config` + `parse_targets`, construit le `MatchingEngine`, paramètre sur chaque cas du corpus, compare la décision à l'attendu.

> **Note couverture (gate 100 % branch) :** chaque conditionnel DOIT être exercé des deux côtés. Points chauds explicitement couverts par les tâches :
> - **rang de palier** : `_TIER_RANK[a] > _TIER_RANK[b]` vrai ET faux (download>notify, notify>catalog, et égalité → départage) ; cohérence `set(_TIER_RANK) == TIERS`.
> - **1re règle vraie / aucune** : une cible où la 1re règle (index 0) matche ; une où seule une règle d'index > 0 matche (boucle qui continue) ; une où **aucune** règle ne matche (la cible ne contribue rien).
> - **décision fichier** : aucune cible ne contribue → `None` ; une seule cible contribue → elle gagne ; deux cibles, palier strictement plus haut gagne (les deux sens du `>`) ; deux cibles **même palier**, index plus petit gagne (départage index, les deux ordres d'itération) ; deux cibles **même palier ET même index**, `target_id` plus petit gagne (départage `target_id`).
> - **bornage** : `len > max` (écarté) ET `len ≤ max` (évalué).
> - **explicabilité** : token coverage présent (branche `isinstance` vraie, `coverage_values` non vide) ET config sans coverage (branche fausse, `coverage_values` vide) ; `rules_fired` à 1 élément ET à plusieurs.
> - **golden** : au moins un cas par palier (`download`/`notify`/`catalog`) + au moins un `discarded` (les deux branches du parseur d'attendu : décision attendue vs écart attendu).

> **Note typage (`mypy --strict`) :** annotations complètes partout, **y compris dans les tests** (chaque fonction de test annotée `-> None`, paramètres typés). `CoverageMatcher.value()` étant hors du `Matcher` Protocol, l'accès se fait après un `isinstance(matcher, CoverageMatcher)` (narrowing mypy) — aucun `cast`, aucun `type: ignore`. Le chargement YAML des fixtures dans les helpers de test renvoie `dict[str, Any]` (frontière `Any`) franchie immédiatement par `parse_matcher_config`/`parse_targets` (Plan 2b), typés.

---

## Task 1: Rang de palier `_TIER_RANK` + squelette `engine.py`

**Files:**
- Create: `src/emule_indexer/domain/matching/engine.py`
- Create: `tests/domain/matching/test_engine.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/matching/test_engine.py` :
```python
from emule_indexer.domain.matching.config import TIERS
from emule_indexer.domain.matching.engine import _TIER_RANK


def test_tier_rank_orders_download_above_notify_above_catalog() -> None:
    assert _TIER_RANK["download"] > _TIER_RANK["notify"]
    assert _TIER_RANK["notify"] > _TIER_RANK["catalog"]


def test_tier_rank_catalog_is_lowest() -> None:
    assert _TIER_RANK["catalog"] < _TIER_RANK["download"]
    assert _TIER_RANK["catalog"] < _TIER_RANK["notify"]


def test_tier_rank_covers_exactly_the_valid_tiers() -> None:
    # Cohérence : tout palier licite (TIERS) a un rang, et aucun rang orphelin.
    assert set(_TIER_RANK) == TIERS
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.matching.engine'`.

- [ ] **Step 3: Écrire l'implémentation (squelette + rang)**

`src/emule_indexer/domain/matching/engine.py` :
```python
"""Moteur d'ÉVALUATION du matching (cf. spec §8.5, partie évaluation).

Domaine PUR. Prend une :class:`MatcherConfig` déjà validée (Plan 2b) et des
:class:`TargetSegment`, pré-résout les arbres de matchers par cible une fois à la
construction (via :class:`MatcherResolver`), puis rend une décision EN MÉMOIRE pour un
:class:`FileCandidate`. AUCUNE I/O, AUCUN logging, AUCUNE DB : l'« explicabilité loggée
en DEBUG » de §8.5 = le moteur RETOURNE un résultat explicable ; le logging est l'affaire
d'un adapter d'un plan ultérieur.
"""

# Rang des paliers (cf. spec §8.5 : « palier le plus haut, download>notify>catalog »).
# Entier croissant = palier plus haut. `TIERS` (config) donne l'ensemble LICITE ; ce
# rang donne l'ORDRE de décision. Un test vérifie set(_TIER_RANK) == TIERS.
_TIER_RANK: dict[str, int] = {"catalog": 0, "notify": 1, "download": 2}
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: PASS — les trois tests verts ; les deux sens de chaque comparaison de rang exercés ; cohérence avec `TIERS` confirmée.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (le module ne contient encore que `_TIER_RANK`, intégralement couvert) ; aucune erreur ruff/mypy.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/engine.py tests/domain/matching/test_engine.py
git commit -m "feat(domain): tier rank for evaluation engine (download>notify>catalog)"
```

---

## Task 2: Modèles de décision `Explanation` + `MatchDecision`

**Files:**
- Modify: `src/emule_indexer/domain/matching/engine.py`
- Modify: `tests/domain/matching/test_engine.py`

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter en tête de `tests/domain/matching/test_engine.py` l'import, puis les tests à la fin du fichier :
```python
import dataclasses

import pytest

from emule_indexer.domain.matching.engine import Explanation, MatchDecision
```
```python
def test_explanation_is_frozen_and_holds_fields() -> None:
    explanation = Explanation(
        target_id="S2E062A",
        rules_fired=("id_segment_exact", "keroro_large"),
        tokens_matched=("is_video", "keroro", "segment_id"),
        coverage_values=(("title_hit", 1.0),),
    )
    assert explanation.target_id == "S2E062A"
    assert explanation.rules_fired == ("id_segment_exact", "keroro_large")
    assert explanation.tokens_matched == ("is_video", "keroro", "segment_id")
    assert explanation.coverage_values == (("title_hit", 1.0),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        explanation.target_id = "S2E062B"  # type: ignore[misc]


def test_match_decision_is_frozen_and_holds_persisted_columns_plus_explanation() -> None:
    explanation = Explanation(
        target_id="S2E062A",
        rules_fired=("id_segment_exact",),
        tokens_matched=("keroro",),
        coverage_values=(),
    )
    decision = MatchDecision(
        target_id="S2E062A",
        rule_name="id_segment_exact",
        tier="download",
        explanation=explanation,
    )
    # Les trois colonnes que match_decisions persistera (spec §11).
    assert decision.target_id == "S2E062A"
    assert decision.rule_name == "id_segment_exact"
    assert decision.tier == "download"
    assert decision.explanation is explanation
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.tier = "notify"  # type: ignore[misc]
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: FAIL — `ImportError: cannot import name 'Explanation' from 'emule_indexer.domain.matching.engine'`.

- [ ] **Step 3: Écrire l'implémentation**

Dans `src/emule_indexer/domain/matching/engine.py`, ajouter en tête (sous la docstring) l'import et les dataclasses AVANT `_TIER_RANK` :
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Explanation:
    """Pourquoi cette décision (cf. spec §8.5 : tokens/règles déclenchés + value coverage).

    Concerne la SEULE cible gagnante. ``rules_fired`` : noms des règles vraies pour cette
    cible, dans l'ordre de la config (la 1re est la gagnante). ``tokens_matched`` : noms
    des tokens nommés de la config qui matchent (triés). ``coverage_values`` : pour chaque
    token coverage, ``(nom, value(candidate))`` (triés). Tuples (et non dicts) pour rester
    GELÉ/hashable et déterministe.
    """

    target_id: str
    rules_fired: tuple[str, ...]
    tokens_matched: tuple[str, ...]
    coverage_values: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class MatchDecision:
    """Décision fichier (cf. spec §8.5). Porte les 3 colonnes de match_decisions (§11).

    ``target_id``/``rule_name``/``tier`` = exactement les colonnes que ``match_decisions``
    persistera (§11). ``decided_at``/``node_id``/``ed2k_hash`` ne sont PAS ici : ce sont
    des colonnes de persistance (horloge + identité + clé contenu) injectées par l'adapter
    DB d'un plan ultérieur. ``explanation`` embarque l'explicabilité (§8.5).
    """

    target_id: str
    rule_name: str
    tier: str
    explanation: Explanation
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: PASS — `Explanation` et `MatchDecision` gelées (mutation → `FrozenInstanceError`), tous les champs lisibles.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % ; aucune erreur ruff/mypy.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/engine.py tests/domain/matching/test_engine.py
git commit -m "feat(domain): MatchDecision + Explanation models (persisted columns + explainability)"
```

---

## Task 3: Évaluation par paire `(fichier, cible)` — 1re règle vraie

**Files:**
- Modify: `src/emule_indexer/domain/matching/engine.py`
- Modify: `tests/domain/matching/test_engine.py`

> Cette tâche introduit une **fonction interne pure** `_first_matching_rule(config, resolved, candidate)` qui, pour une cible déjà résolue, renvoie `(rule_index, rule_name, tier) | None` selon la 1re règle vraie dans l'ordre de `config.rules`. On la teste isolément avant de la composer dans `MatchingEngine` (Task 4). On s'appuie sur `parse_matcher_config`/`MatcherResolver` du Plan 2b pour construire de vraies cibles résolues.

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter les imports en tête de `tests/domain/matching/test_engine.py` :
```python
import datetime

from emule_indexer.domain.matching.config import MatcherConfig
from emule_indexer.domain.matching.engine import _first_matching_rule
from emule_indexer.domain.matching.models import FileCandidate, TargetSegment
from emule_indexer.domain.matching.resolver import MatcherResolver, ResolvedTarget
from emule_indexer.domain.matching.validation import parse_matcher_config
```
Puis un helper et les tests, à la fin du fichier :
```python
_TARGET_62A = TargetSegment(
    season=2,
    number=62,
    segment="a",
    title="Les demoiselles cambrioleuses",
    broadcast_date=datetime.date(2008, 9, 21),
    status="partial",
)

# Config minimale à deux règles d'index distinct pour exercer "1re vraie" et "boucle".
_TWO_RULE_RAW: dict[str, object] = {
    "tokens": {
        "is_video": {"regex": r"\.(avi|mkv)$"},
        "seg": {"regex": r"n[°o]?\s*0*{number}\s*{segment}"},
        "keroro": {"keyword": "keroro"},
    },
    "rules": [
        {"name": "exact", "tier": "download", "all": ["is_video", "seg"]},
        {"name": "large", "tier": "catalog", "any": ["keroro"]},
    ],
}


def _resolve(raw: dict[str, object], target: TargetSegment) -> tuple[MatcherConfig, ResolvedTarget]:
    config = parse_matcher_config(raw)
    resolved = MatcherResolver(config).resolve_all(target)
    return config, resolved


def test_first_matching_rule_returns_index_zero_when_first_rule_true() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    candidate = FileCandidate(filename="Keroro N°062A.avi")
    assert _first_matching_rule(config, resolved, candidate) == (0, "exact", "download")


def test_first_matching_rule_skips_to_later_rule_when_first_false() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    # Pas vidéo + pas de segment 062A -> "exact" faux ; "keroro" vrai -> 2e règle.
    candidate = FileCandidate(filename="keroro autre chose.txt")
    assert _first_matching_rule(config, resolved, candidate) == (1, "large", "catalog")


def test_first_matching_rule_returns_none_when_no_rule_true() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    candidate = FileCandidate(filename="naruto 062.txt")
    assert _first_matching_rule(config, resolved, candidate) is None
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: FAIL — `ImportError: cannot import name '_first_matching_rule' from 'emule_indexer.domain.matching.engine'`.

- [ ] **Step 3: Écrire l'implémentation**

Dans `src/emule_indexer/domain/matching/engine.py`, étendre les imports en tête et ajouter la fonction après les dataclasses :
```python
from emule_indexer.domain.matching.config import MatcherConfig
from emule_indexer.domain.matching.models import FileCandidate
from emule_indexer.domain.matching.resolver import ResolvedTarget
```
```python
def _first_matching_rule(
    config: MatcherConfig,
    resolved: ResolvedTarget,
    candidate: FileCandidate,
) -> tuple[int, str, str] | None:
    """1re règle vraie pour (candidate, cible résolue) → ``(index, nom, tier)`` (§8.5).

    Parcourt ``config.rules`` DANS L'ORDRE (l'index = la position = la priorité) ; pour
    chaque règle, évalue l'arbre déjà construit ``resolved.rules[rule.name]``. Renvoie le
    1er match ; ``None`` si aucune règle ne matche (la cible ne contribue rien).
    """
    for index, rule in enumerate(config.rules):
        if resolved.rules[rule.name].matches(candidate):
            return (index, rule.name, rule.tier)
    return None
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: PASS — 1re règle vraie (index 0), saut vers une règle ultérieure (boucle qui continue), et aucune règle vraie (`None`) : les trois branches de la boucle exercées.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % ; aucune erreur ruff/mypy.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/engine.py tests/domain/matching/test_engine.py
git commit -m "feat(domain): per-(file,target) first-matching-rule evaluation"
```

---

## Task 4: Façade `MatchingEngine` — décision fichier déterministe + bornage de longueur

**Files:**
- Modify: `src/emule_indexer/domain/matching/engine.py`
- Modify: `tests/domain/matching/test_engine.py`

> Cette tâche compose tout : pré-résolution à la construction, brute-force §8.5, palier max, départage déterministe (index puis `target_id`), écart si rien, bornage de longueur. L'**explicabilité** (remplissage de `Explanation`) est ajoutée en Task 5 — ici, `evaluate` produit déjà une `MatchDecision` avec une `Explanation` **minimale** (cible gagnante + règles vraies), suffisante pour les tests de décision ; Task 5 enrichira `tokens_matched`/`coverage_values`. Pour éviter une réécriture, on implémente l'explication COMPLÈTE dès maintenant (helper `_explain`), et Task 5 ne fait qu'ajouter ses tests dédiés. Le code de cette tâche est donc final.

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter l'import et les tests à la fin de `tests/domain/matching/test_engine.py` :
```python
from emule_indexer.domain.matching.engine import MatchingEngine
```
```python
# --- Config canonique §8.3 (réutilisée par plusieurs tests) ---
_CANONICAL_RAW: dict[str, object] = {
    "tokens": {
        "keroro": {"keyword": "keroro"},
        "titar": {"keyword": "titar"},
        "keroro_titar": {"any": ["keroro", "titar"]},
        "teletoon": {"regex": "t[eé]l[eé]toon"},
        "segment_id": {"regex": r"n[°o]?\s*0*{number}\s*{segment}"},
        "air_date": {"regex": "{date_alt}"},
        "title_hit": {"coverage": "title", "min": 0.6},
        "is_video": {"regex": r"\.(avi|mkv|mp4|mpg|ogm)$"},
    },
    "rules": [
        {"name": "id_segment_exact", "tier": "download", "all": ["is_video", "segment_id", "keroro"]},
        {"name": "date_teletoon_titre", "tier": "download",
         "all": ["air_date", "teletoon", {"token": "title_hit", "min": 0.4}]},
        {"name": "numero_titre", "tier": "notify",
         "all": ["segment_id", {"token": "title_hit", "min": 0.5}]},
        {"name": "keroro_large", "tier": "catalog", "any": ["keroro_titar"]},
    ],
}

_TARGET_62B = TargetSegment(
    season=2,
    number=62,
    segment="b",
    title="Le grand combat sous-marin",
    broadcast_date=datetime.date(2008, 9, 21),
    status="lost",
)

_REAL_62A_FILENAME = (
    "[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » "
    "[Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi"
)


def _canonical_engine() -> MatchingEngine:
    config = parse_matcher_config(_CANONICAL_RAW)
    return MatchingEngine(config, (_TARGET_62A, _TARGET_62B))


def test_evaluate_real_62a_is_download_via_first_rule_on_62a() -> None:
    decision = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.rule_name == "id_segment_exact"
    assert decision.target_id == "S2E062A"


def test_evaluate_discards_non_keroro_file() -> None:
    decision = _canonical_engine().evaluate(FileCandidate(filename="Naruto épisode 062 VF.avi"))
    assert decision is None


def test_evaluate_returns_highest_tier_across_targets() -> None:
    # 62A -> download (id_segment_exact) ; 62B -> catalog (keroro_large). Download gagne.
    decision = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert decision is not None
    assert decision.tier == "download"
    assert decision.target_id == "S2E062A"


def test_evaluate_notify_tier_when_only_numero_titre_matches() -> None:
    # 062A + titre mais PAS d'extension vidéo -> id_segment_exact faux, numero_titre vrai.
    candidate = FileCandidate(filename="KERORO N°062A Les demoiselles cambrioleuses.txt")
    decision = _canonical_engine().evaluate(candidate)
    assert decision is not None
    assert decision.tier == "notify"
    assert decision.rule_name == "numero_titre"
    assert decision.target_id == "S2E062A"


def test_evaluate_tiebreak_same_tier_lowest_target_id_wins() -> None:
    # Fichier "Keroro" seul -> 62A et 62B donnent TOUS DEUX keroro_large (catalog, index 3).
    # Même palier ET même index -> départage par target_id : S2E062A < S2E062B.
    decision = _canonical_engine().evaluate(FileCandidate(filename="Keroro Gunso opening.mkv"))
    assert decision is not None
    assert decision.tier == "catalog"
    assert decision.rule_name == "keroro_large"
    assert decision.target_id == "S2E062A"


# --- Départage par INDEX de règle (isolé du target_id) ---
# Deux règles download ; la cible au target_id PLUS GRAND matche la règle d'index PLUS
# PETIT. Si seul target_id départageait, la mauvaise cible gagnerait ; l'index doit primer.
_INDEX_TIEBREAK_RAW: dict[str, object] = {
    "tokens": {
        "is_video": {"regex": r"\.(avi|mkv)$"},
        "seg": {"regex": r"n[°o]?\s*0*{number}\s*{segment}"},
        "title_hit": {"coverage": "title", "min": 0.6},
    },
    "rules": [
        {"name": "by_segment", "tier": "download", "all": ["is_video", "seg"]},
        {"name": "by_title", "tier": "download",
         "all": ["is_video", {"token": "title_hit", "min": 0.6}]},
    ],
}


def test_evaluate_tiebreak_same_tier_lowest_rule_index_wins_over_target_id() -> None:
    config = parse_matcher_config(_INDEX_TIEBREAK_RAW)
    # target_high : grand target_id (S2E099Z), matche by_segment (index 0).
    target_high = TargetSegment(season=2, number=99, segment="z", title="zzz aucun rapport")
    # target_low : petit target_id (S2E001A), matche by_title (index 1).
    target_low = TargetSegment(season=2, number=1, segment="a", title="Les demoiselles cambrioleuses")
    engine = MatchingEngine(config, (target_low, target_high))
    candidate = FileCandidate(filename="N°099Z Les demoiselles cambrioleuses.avi")
    decision = engine.evaluate(candidate)
    assert decision is not None
    # Index 0 (by_segment sur S2E099Z) prime sur index 1 (by_title sur S2E001A),
    # MALGRÉ S2E001A < S2E099Z : l'index départage AVANT le target_id.
    assert decision.rule_name == "by_segment"
    assert decision.target_id == "S2E099Z"


def test_evaluate_rejects_filename_over_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=16)
    # Un nom qui matcherait (download) mais dépasse 16 caractères -> écarté.
    assert engine.evaluate(FileCandidate(filename="Keroro N°062A.avi")) is None


def test_evaluate_accepts_filename_at_or_below_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=4096)
    decision = engine.evaluate(FileCandidate(filename="Keroro N°062A.avi"))
    assert decision is not None
    assert decision.tier == "download"


def test_engine_resolves_each_target_once_at_construction() -> None:
    # La pré-résolution arrive à la construction : evaluate ne reconstruit pas d'arbre.
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A, _TARGET_62B))
    assert len(engine._resolved) == 2
    assert {rt.target.target_id for rt in engine._resolved} == {"S2E062A", "S2E062B"}
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: FAIL — `ImportError: cannot import name 'MatchingEngine' from 'emule_indexer.domain.matching.engine'`.

- [ ] **Step 3: Écrire l'implémentation**

Dans `src/emule_indexer/domain/matching/engine.py`, étendre les imports et ajouter le helper d'explication puis la classe. Imports en tête (ajouter `Sequence`, `CoverageMatcher`, `MatcherResolver`, `TargetSegment`) :
```python
from collections.abc import Sequence

from emule_indexer.domain.matching.matchers import CoverageMatcher
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.matching.resolver import MatcherResolver
```
Helper d'explication (à placer après `_first_matching_rule`) :
```python
def _explain(
    config: MatcherConfig,
    resolved: ResolvedTarget,
    candidate: FileCandidate,
) -> Explanation:
    """Construit l'explication de la cible GAGNANTE (cf. spec §8.5).

    ``rules_fired`` : règles vraies dans l'ordre de la config. ``tokens_matched`` : tokens
    nommés qui matchent (triés). ``coverage_values`` : ``(nom, value)`` des tokens coverage
    (triés). Lit ``CoverageMatcher.value()`` (hors Protocol) via ``isinstance``.
    """
    rules_fired = tuple(
        rule.name for rule in config.rules if resolved.rules[rule.name].matches(candidate)
    )
    tokens_matched = tuple(
        sorted(name for name, matcher in resolved.tokens.items() if matcher.matches(candidate))
    )
    coverage_values = tuple(
        (name, matcher.value(candidate))
        for name, matcher in sorted(resolved.tokens.items())
        if isinstance(matcher, CoverageMatcher)
    )
    return Explanation(
        target_id=resolved.target.target_id,
        rules_fired=rules_fired,
        tokens_matched=tokens_matched,
        coverage_values=coverage_values,
    )
```
Classe façade (à placer en fin de fichier) :
```python
class MatchingEngine:
    """Façade pure du moteur d'évaluation (cf. spec §8.5). Pré-résout les cibles une fois.

    Brute-force §8.5 : chaque fichier est évalué contre TOUTES les cibles (aucune
    heuristique d'entonnoir). Les arbres de matchers (regex interpolées+compilées par
    cible) sont construits UNE FOIS à la construction. ``max_filename_length`` borne la
    longueur du nom avant matching (§8.5/§14) : un nom plus long est écarté (``None``).
    """

    def __init__(
        self,
        config: MatcherConfig,
        targets: Sequence[TargetSegment],
        *,
        max_filename_length: int = 4096,
    ) -> None:
        self._config = config
        self._max_filename_length = max_filename_length
        resolver = MatcherResolver(config)
        self._resolved: tuple[ResolvedTarget, ...] = tuple(
            resolver.resolve_all(target) for target in targets
        )

    def evaluate(self, candidate: FileCandidate) -> MatchDecision | None:
        """Décision fichier déterministe (cf. spec §8.5) ou ``None`` (fichier écarté).

        Bornage de longueur d'abord. Puis, par cible, 1re règle vraie ; décision = palier
        le plus haut, départage déterministe par index de règle puis ``target_id``. Aucune
        règle vraie nulle part → ``None``.
        """
        if len(candidate.filename) > self._max_filename_length:
            return None
        best: tuple[int, int, str] | None = None  # (-rang_palier, index_règle, target_id)
        best_resolved: ResolvedTarget | None = None
        best_rule_name = ""
        best_tier = ""
        for resolved in self._resolved:
            outcome = _first_matching_rule(self._config, resolved, candidate)
            if outcome is None:
                continue
            index, rule_name, tier = outcome
            # Clé de tri : palier le plus HAUT d'abord (-rang), puis index le plus PETIT,
            # puis target_id le plus PETIT. min() sur cette clé donne le gagnant.
            key = (-_TIER_RANK[tier], index, resolved.target.target_id)
            if best is None or key < best:
                best = key
                best_resolved = resolved
                best_rule_name = rule_name
                best_tier = tier
        if best_resolved is None:
            return None
        return MatchDecision(
            target_id=best_resolved.target.target_id,
            rule_name=best_rule_name,
            tier=best_tier,
            explanation=_explain(self._config, best_resolved, candidate),
        )
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: PASS — réel 62A → download/id_segment_exact/S2E062A ; leurre → `None` ; palier max ; notify isolé ; départage par target_id (S2E062A) ; départage par index (by_segment/S2E099Z prime malgré S2E001A plus petit) ; bornage des deux côtés ; pré-résolution (2 cibles résolues à la construction). *(Faits vérifiés empiriquement contre le source réel : le réel 62A fait feu sur les 4 règles, palier le plus haut `download` via `id_segment_exact` index 0 ; le « Keroro » seul donne catalog/keroro_large sur 62A et 62B → départage target_id → S2E062A ; le leurre Naruto → aucune règle → `None`.)*

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (toutes les branches de `evaluate` : bornage vrai/faux, `outcome is None`/non, `best is None`/`key < best`, `best_resolved is None`/non, exercées) ; aucune erreur ruff/mypy.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/matching/engine.py tests/domain/matching/test_engine.py
git commit -m "feat(domain): MatchingEngine evaluate (highest tier, deterministic tiebreak, length bound)"
```

---

## Task 5: Tests d'explicabilité (`Explanation` peuplée par `evaluate`)

**Files:**
- Modify: `tests/domain/matching/test_engine.py`

> Le helper `_explain` est déjà implémenté (Task 4). Cette tâche **verrouille son comportement** par des tests dédiés couvrant les DEUX branches de `isinstance(matcher, CoverageMatcher)` (config AVEC coverage → `coverage_values` non vide ; config SANS coverage → `coverage_values` vide) et `rules_fired` à 1 vs plusieurs éléments. Aucun code de prod nouveau.

- [ ] **Step 1: Ajouter les tests qui échouent**

> Note : `_explain` existant, ces tests doivent passer dès écriture. Pour respecter le rythme TDD (« voir échouer »), écrire D'ABORD une assertion volontairement fausse pour confirmer que le test s'exécute, OU — préféré — exécuter le sous-ensemble et constater qu'il passe immédiatement (le helper est déjà spécifié par Task 4). On documente que ces tests **figent** le contrat de l'explication. Écrire les tests réels ci-dessous.

Ajouter à la fin de `tests/domain/matching/test_engine.py` :
```python
def test_explanation_on_real_62a_lists_fired_rules_tokens_and_coverage() -> None:
    decision = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert decision is not None
    explanation = decision.explanation
    assert explanation.target_id == "S2E062A"
    # Le réel 62A fait feu sur les 4 règles (vérifié empiriquement) -> plusieurs rules_fired.
    assert explanation.rules_fired == (
        "id_segment_exact",
        "date_teletoon_titre",
        "numero_titre",
        "keroro_large",
    )
    # Tokens nommés qui matchent (triés). title_hit est un coverage et matche (value 1.0).
    assert "title_hit" in explanation.tokens_matched
    assert "keroro" in explanation.tokens_matched
    assert "segment_id" in explanation.tokens_matched
    assert explanation.tokens_matched == tuple(sorted(explanation.tokens_matched))
    # coverage_values : title_hit présent avec sa value (branche isinstance VRAIE).
    assert explanation.coverage_values == (("title_hit", 1.0),)


def test_explanation_single_rule_fired_and_no_coverage_token() -> None:
    # Config SANS aucun token coverage -> coverage_values vide (branche isinstance FAUSSE).
    raw: dict[str, object] = {
        "tokens": {
            "is_video": {"regex": r"\.(avi|mkv)$"},
            "seg": {"regex": r"n[°o]?\s*0*{number}\s*{segment}"},
        },
        "rules": [{"name": "only", "tier": "download", "all": ["is_video", "seg"]}],
    }
    engine = MatchingEngine(parse_matcher_config(raw), (_TARGET_62A,))
    decision = engine.evaluate(FileCandidate(filename="N°062A.avi"))
    assert decision is not None
    assert decision.explanation.rules_fired == ("only",)  # une seule règle
    assert decision.explanation.coverage_values == ()  # aucun coverage
    assert decision.explanation.tokens_matched == ("is_video", "seg")
```

- [ ] **Step 2: Lancer pour vérifier**

Run: `uv run pytest tests/domain/matching/test_engine.py -q`
Expected: PASS — l'explication du réel 62A liste les 4 règles, les tokens triés, et `("title_hit", 1.0)` (branche coverage VRAIE) ; la config sans coverage donne `coverage_values == ()` (branche coverage FAUSSE) et `rules_fired == ("only",)` (un seul élément). Les deux côtés de `isinstance(matcher, CoverageMatcher)` exercés.

- [ ] **Step 3: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % ; aucune erreur ruff/mypy.

- [ ] **Step 4: Commit**

```bash
git add tests/domain/matching/test_engine.py
git commit -m "test: pin explainability contract (rules_fired, tokens_matched, coverage_values)"
```

---

## Task 6: Corpus golden — fixtures YAML + test paramétré bout-en-bout

**Files:**
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/canonical_config.yaml`
- Create: `tests/fixtures/canonical_targets.yaml`
- Create: `tests/fixtures/golden_corpus.yaml`
- Create: `tests/domain/matching/test_golden_corpus.py`

> Bout-en-bout sur la VRAIE chaîne : `load_yaml` (Plan 2b adapter) → `parse_matcher_config`/`parse_targets` (Plan 2b validation) → `MatchingEngine` (ce plan). Le corpus est un fixture YAML **éditable par la communauté** (§16 « extensible par la communauté ») : ajouter un cas = ajouter une entrée, sans toucher au code.

- [ ] **Step 1: Écrire le test qui échoue**

`tests/fixtures/__init__.py` :
```python
"""Fixtures de données pour les tests (corpus golden, config & cibles canoniques)."""
```

`tests/domain/matching/test_golden_corpus.py` :
```python
from pathlib import Path
from typing import Any

import pytest

from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.models import FileCandidate
from emule_indexer.domain.matching.validation import parse_matcher_config, parse_targets

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _engine() -> MatchingEngine:
    config = parse_matcher_config(load_yaml(_FIXTURES / "canonical_config.yaml"))
    targets = parse_targets(load_yaml(_FIXTURES / "canonical_targets.yaml"))
    return MatchingEngine(config, targets)


def _corpus_cases() -> list[dict[str, Any]]:
    raw = load_yaml(_FIXTURES / "golden_corpus.yaml")
    cases = raw["cases"]
    assert isinstance(cases, list)
    return [dict(case) for case in cases]


_CASES = _corpus_cases()


@pytest.mark.parametrize("case", _CASES, ids=[str(c["id"]) for c in _CASES])
def test_golden_corpus(case: dict[str, Any]) -> None:
    engine = _engine()
    decision = engine.evaluate(FileCandidate(filename=str(case["filename"])))
    if case.get("discarded", False):
        assert decision is None, f"{case['id']}: attendu écarté, obtenu {decision}"
        return
    assert decision is not None, f"{case['id']}: attendu une décision, obtenu None"
    assert decision.tier == case["tier"], f"{case['id']}: palier"
    assert decision.target_id == case["target_id"], f"{case['id']}: cible"
    assert decision.rule_name == case["rule_name"], f"{case['id']}: règle"


def test_corpus_covers_every_tier_and_a_discard() -> None:
    # Garde-fou de complétude : le corpus exerce les 3 paliers + au moins un écart.
    tiers = {c.get("tier") for c in _CASES if not c.get("discarded", False)}
    assert {"download", "notify", "catalog"} <= tiers
    assert any(c.get("discarded", False) for c in _CASES)
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_golden_corpus.py -q`
Expected: FAIL — `YamlLoadError` / `FileNotFoundError` (les fixtures n'existent pas encore) au moment de `load_yaml`.

- [ ] **Step 3: Créer la config canonique (§8.3)**

`tests/fixtures/canonical_config.yaml` :
```yaml
# Config matcher canonique (cf. spec §8.3). Sert le corpus golden bout-en-bout.
tokens:
  keroro:       { keyword: keroro }
  titar:        { keyword: titar }
  keroro_titar: { any: [keroro, titar] }
  teletoon:     { regex: "t[eé]l[eé]toon" }
  segment_id:   { regex: "n[°o]?\\s*0*{number}\\s*{segment}" }
  air_date:     { regex: "{date_alt}" }
  title_hit:    { coverage: title, min: 0.6 }
  is_video:     { regex: "\\.(avi|mkv|mp4|mpg|ogm)$" }
rules:
  - { name: id_segment_exact,    tier: download, all: [is_video, segment_id, keroro] }
  - { name: date_teletoon_titre, tier: download, all: [air_date, teletoon, { token: title_hit, min: 0.4 }] }
  - { name: numero_titre,        tier: notify,   all: [segment_id, { token: title_hit, min: 0.5 }] }
  - { name: keroro_large,        tier: catalog,  any: [keroro_titar] }
```

- [ ] **Step 4: Créer les cibles canoniques (§7)**

`tests/fixtures/canonical_targets.yaml` :
```yaml
# Cibles canoniques (cf. spec §7). target_id : S2E062A / S2E062B.
episodes:
  - season: 2
    number: 62
    broadcast_date: 2008-09-21
    status: partial
    segments:
      - { letter: A, title: "Les demoiselles cambrioleuses", aliases: [] }
      - { letter: B, title: "Le grand combat sous-marin" }
```

- [ ] **Step 5: Créer le corpus golden**

`tests/fixtures/golden_corpus.yaml` :
```yaml
# Corpus golden (cf. spec §16) : noms réels + forgés -> palier/cible/règle attendus.
# EXTENSIBLE PAR LA COMMUNAUTÉ : ajouter un cas = ajouter une entrée ci-dessous.
# Un cas : { id, filename, tier, target_id, rule_name } ou { id, filename, discarded: true }.
cases:
  - id: real_62A_full_release
    filename: "[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » [Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi"
    tier: download
    target_id: S2E062A
    rule_name: id_segment_exact

  - id: ascii_no_accents_62A
    filename: "KERORO MISSION TITAR N062A Les demoiselles cambrioleuses 21 septembre 2008 TELETOON.avi"
    tier: download
    target_id: S2E062A
    rule_name: id_segment_exact

  - id: near_collision_063A_still_download_via_date_rule
    # Numéro DIFFÉRENT (063A) -> segment_id échoue, mais date+teletoon+titre suffisent
    # (date_teletoon_titre) -> reste download sur 62A. Quasi-collision documentée.
    filename: "[TV] KERORO MISSION TITAR N°063A « Les demoiselles cambrioleuses » [21 septembre 2008 sur TELETOON].avi"
    tier: download
    target_id: S2E062A
    rule_name: date_teletoon_titre

  - id: numero_titre_no_video_extension
    # 062A + titre mais extension non vidéo -> id_segment_exact faux, numero_titre vrai.
    filename: "KERORO N°062A Les demoiselles cambrioleuses.txt"
    tier: notify
    target_id: S2E062A
    rule_name: numero_titre

  - id: keroro_only_catalog_tiebreak_target_id
    # "Keroro" seul -> keroro_large (catalog) sur 62A ET 62B ; départage target_id -> 62A.
    filename: "Keroro Gunso opening.mkv"
    tier: catalog
    target_id: S2E062A
    rule_name: keroro_large

  - id: decoy_non_keroro_naruto
    filename: "Naruto épisode 062 VF.avi"
    discarded: true

  - id: decoy_random_movie
    filename: "Big.Buck.Bunny.1080p.mkv"
    discarded: true
```

- [ ] **Step 6: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_golden_corpus.py -q`
Expected: PASS — chaque cas du corpus correspond à l'attendu (réel 62A → download ; ASCII sans accents → download ; quasi-collision 063A → download via la règle de date ; sans extension vidéo → notify ; Keroro seul → catalog/S2E062A par départage ; leurres → écartés). Le test de complétude confirme les 3 paliers + un écart présents. *(Tous ces verdicts ont été vérifiés empiriquement contre la résolution réelle du Plan 2b.)*

- [ ] **Step 7: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (les deux branches du parseur d'attendu — `discarded` vrai/faux — exercées par le corpus) ; aucune erreur ruff/mypy.

- [ ] **Step 8: Commit**

```bash
git add tests/fixtures/__init__.py tests/fixtures/canonical_config.yaml tests/fixtures/canonical_targets.yaml tests/fixtures/golden_corpus.yaml tests/domain/matching/test_golden_corpus.py
git commit -m "test: golden corpus (real + forged names) pinned end-to-end against canonical config"
```

---

## Task 7: Invariants property-based faits main (déterministes, seedés)

**Files:**
- Create: `tests/domain/matching/test_engine_properties.py`

> §16 : « une règle plus prioritaire ne baisse jamais le palier » + déterminisme « shuffle seedé → zéro flakiness ». Faits main, `random.Random(SEED)` constant (DÉCISION 6). Deux propriétés : **P1** (réordonner les cibles ne change pas la décision) ; **P2** (monotonie du palier sous activation d'une règle plus prioritaire).

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/matching/test_engine_properties.py` :
```python
import datetime
import random

from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.models import FileCandidate, TargetSegment
from emule_indexer.domain.matching.validation import parse_matcher_config

# Seed CONSTANT : chaque run est identique (zéro flakiness, cf. spec §16).
_SEED = 20260610

_CANONICAL_RAW: dict[str, object] = {
    "tokens": {
        "keroro": {"keyword": "keroro"},
        "titar": {"keyword": "titar"},
        "keroro_titar": {"any": ["keroro", "titar"]},
        "teletoon": {"regex": "t[eé]l[eé]toon"},
        "segment_id": {"regex": r"n[°o]?\s*0*{number}\s*{segment}"},
        "air_date": {"regex": "{date_alt}"},
        "title_hit": {"coverage": "title", "min": 0.6},
        "is_video": {"regex": r"\.(avi|mkv|mp4|mpg|ogm)$"},
    },
    "rules": [
        {"name": "id_segment_exact", "tier": "download", "all": ["is_video", "segment_id", "keroro"]},
        {"name": "date_teletoon_titre", "tier": "download",
         "all": ["air_date", "teletoon", {"token": "title_hit", "min": 0.4}]},
        {"name": "numero_titre", "tier": "notify",
         "all": ["segment_id", {"token": "title_hit", "min": 0.5}]},
        {"name": "keroro_large", "tier": "catalog", "any": ["keroro_titar"]},
    ],
}


def _targets() -> list[TargetSegment]:
    date = datetime.date(2008, 9, 21)
    return [
        TargetSegment(season=2, number=62, segment="a", title="Les demoiselles cambrioleuses",
                      broadcast_date=date, status="partial"),
        TargetSegment(season=2, number=62, segment="b", title="Le grand combat sous-marin",
                      broadcast_date=date, status="lost"),
        TargetSegment(season=1, number=5, segment="a", title="Un titre quelconque",
                      broadcast_date=date, status="lost"),
    ]


_FILENAMES = [
    "[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » [21 septembre 2008 TELETOON].avi",
    "KERORO N°062A Les demoiselles cambrioleuses.txt",
    "Keroro Gunso opening.mkv",
    "Naruto épisode 062 VF.avi",
    "keroro mission titar 062b grand combat.avi",
]


def test_property_decision_invariant_under_target_reordering() -> None:
    # P1 : réordonner les cibles ne change JAMAIS la décision (§8.5 déterminisme).
    config = parse_matcher_config(_CANONICAL_RAW)
    rng = random.Random(_SEED)
    base_targets = _targets()
    reference_engine = MatchingEngine(config, base_targets)
    for filename in _FILENAMES:
        candidate = FileCandidate(filename=filename)
        expected = reference_engine.evaluate(candidate)
        for _ in range(20):  # 20 permutations seedées par fichier (déterministe)
            shuffled = base_targets[:]
            rng.shuffle(shuffled)
            got = MatchingEngine(config, shuffled).evaluate(candidate)
            assert got == expected, f"décision dépend de l'ordre des cibles pour {filename!r}"


def test_property_higher_priority_rule_never_lowers_tier() -> None:
    # P2 : ajouter une règle PLUS PRIORITAIRE (index 0) de palier >= ne baisse jamais le
    # palier résultant (§16). On compare la config canonique à une variante où l'on
    # PRÉPEND une règle download large ; pour tout fichier déjà décidé, le palier ne baisse pas.
    from emule_indexer.domain.matching.engine import _TIER_RANK

    config_base = parse_matcher_config(_CANONICAL_RAW)
    raw_boosted = {
        "tokens": dict(_CANONICAL_RAW["tokens"]),  # type: ignore[arg-type]
        "rules": [
            # Règle download PRÉPENDUE (index 0) : tout fichier "keroro" -> download.
            {"name": "boost_keroro_download", "tier": "download", "any": ["keroro_titar"]},
            *_CANONICAL_RAW["rules"],  # type: ignore[misc]
        ],
    }
    config_boosted = parse_matcher_config(raw_boosted)
    targets = _targets()
    engine_base = MatchingEngine(config_base, targets)
    engine_boosted = MatchingEngine(config_boosted, targets)
    for filename in _FILENAMES:
        candidate = FileCandidate(filename=filename)
        base = engine_base.evaluate(candidate)
        boosted = engine_boosted.evaluate(candidate)
        if base is None:
            continue  # rien à comparer : un fichier écarté peut le rester
        assert boosted is not None, f"{filename!r}: décidé sans boost, écarté avec ?!"
        assert _TIER_RANK[boosted.tier] >= _TIER_RANK[base.tier], (
            f"{filename!r}: une règle plus prioritaire a BAISSÉ le palier"
        )
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_engine_properties.py -q`
Expected: FAIL au premier run uniquement si un module manque ; sinon, le test est conçu pour PASSER dès que `MatchingEngine` existe (Task 4). Comme le moteur est déjà implémenté, ces property-checks VALIDENT le moteur. *(Si l'on veut « voir rouge » d'abord : inverser temporairement une assertion — mais le contrat property est ici une garantie sur le code existant ; on documente que ces tests figent les invariants §16.)*

- [ ] **Step 3: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/matching/test_engine_properties.py -q`
Expected: PASS — P1 : pour chaque fichier, 20 permutations seedées donnent la MÊME décision que la référence (déterminisme sous réordonnancement). P2 : préprender une règle download plus prioritaire ne baisse jamais le palier d'un fichier déjà décidé. Run reproductible (seed constant `20260610`).

- [ ] **Step 4: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (ces tests n'ajoutent pas de code de prod ; ils exercent des chemins déjà couverts via de nouvelles entrées) ; aucune erreur ruff/mypy.

- [ ] **Step 5: Commit**

```bash
git add tests/domain/matching/test_engine_properties.py
git commit -m "test: hand-rolled seeded property checks (reorder-invariance, tier monotonicity)"
```

---

## Task 8: Tag de version

**Files:** (aucun fichier ; tag git)

- [ ] **Step 1: Vérifier l'état complet du moteur**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage **100 %** branch ; le moteur d'évaluation complet (rang, décision, explicabilité, façade, corpus golden, properties) est en place.

- [ ] **Step 2: Taguer**

```bash
git tag -a v0.4.0-engine -m "Moteur d'évaluation complet (joyau) : décision déterministe, explicabilité, corpus golden"
```

---

## Self-Review (rempli par l'auteur du plan)

- **Couverture de la spec (périmètre Plan 2c)** :
  - **§8.5 — ÉVALUATION (brute-force par cible, 1re règle vraie par paire)** → **Task 3** (`_first_matching_rule` : ordre des règles, 1re vraie, aucune vraie) + **Task 4** (`MatchingEngine` brute-force chaque fichier × chaque cible, AUCUNE heuristique d'entonnoir ; pré-résolution une fois à la construction = « regex précompilées par cible »). ✓
  - **§8.5 — DÉCISION FICHIER déterministe (palier max ; départage index puis `target_id` ; écart si rien)** → **Task 4** (clé de tri `(-rang_palier, index, target_id)` via `min`/`<` ; `None` si aucune cible ne contribue) + tests des DEUX départages (index isolé du target_id ; target_id à index égal) + **Task 7** (P1 : déterminisme sous réordonnancement seedé). ✓
  - **§8.5 — palier `download>notify>catalog`** → **Task 1** (`_TIER_RANK`, deux sens + cohérence `set(_TIER_RANK) == TIERS`). ✓
  - **§8.5 — explicabilité (tokens/règles déclenchés + `value` des coverage)** → **Task 4** (`_explain`) + **Task 5** (tests : `rules_fired`/`tokens_matched`/`coverage_values`, branche coverage/non-coverage). Le moteur RETOURNE l'explication ; il ne logge PAS (logging = adapter ultérieur). ✓
  - **§8.5/§14 — bornage de la longueur du nom avant matching** → **Task 4** (`max_filename_length=4096`, écarté au-delà, testé des deux côtés ; DÉCISION 7 : in-engine, justifié). ✓
  - **§11 — colonnes persistées (`target_id`, `rule_name`, `tier`)** → **Task 2** (`MatchDecision` les porte exactement ; `decided_at`/`node_id`/`ed2k_hash` explicitement EXCLUS car colonnes de persistance d'un plan ultérieur). ✓
  - **§16 — corpus golden (réels + forgés : accents/encodages, quasi-collisions, leurres, ép. 62A ; extensible par la communauté)** → **Task 6** (fixture YAML data-driven, bout-en-bout via `load_yaml`+`parse_*`+`MatchingEngine` ; réel 62A, variante ASCII sans accents, quasi-collision 063A, leurres Naruto/film, départage target_id ; extensible par ajout d'entrée YAML). ✓
  - **§16 — property-based (« une règle plus prioritaire ne baisse jamais le palier », déterminisme shuffle seedé)** → **Task 7** (P1 réordonnancement-invariant seedé ; P2 monotonie de palier ; faits main, seed constant, zéro flakiness ; DÉCISION 6 : pas de `hypothesis`). ✓
  - **HORS PÉRIMÈTRE — renvoyé à des plans ULTÉRIEURS, ABSENT ici** : persistance `match_decisions`/`catalog.db` (§11) ; auto-download (§9) ; notifications apprise (§13) ; métriques Prometheus ; logging structuré (le moteur retourne, ne logge pas) ; adapter EC, orchestration recherches (§6), verifier/confinement (§10). **Aucun de ces éléments n'apparaît dans le plan.** ✓
- **Scan des placeholders (« TBD »/« similaire à »/« etc. »)** : aucun « TBD »/« à compléter »/« similaire à la Task N ». Chaque step de code contient le code COMPLET (imports inclus) ; chaque step de run a une commande exacte + sortie attendue ; chaque tâche se termine par un `git commit -m` exact. Les `…` résiduels sont dans la prose, pas dans du code à recopier.
- **Cohérence des types/nommage entre tâches** :
  - Noms stables : `MatchDecision`, `Explanation`, `_TIER_RANK`, `_first_matching_rule`, `_explain`, `MatchingEngine`, `evaluate`, `max_filename_length`, attribut `_resolved` — identiques de leur introduction à leur usage et dans les tests.
  - Réutilise byte-exact les interfaces des Plans 2a/2b vérifiées dans le source : `MatcherConfig.rules` (tuple de `Rule`, `Rule.name`/`Rule.tier`/`Rule.condition`), `TIERS`, `MatcherResolver(config).resolve_all(target) -> ResolvedTarget`, `ResolvedTarget.target`/`.tokens`/`.rules` (rules keyé par NOM, sans tier/index → le moteur zippe avec `config.rules` pour tier+index), `CoverageMatcher.value(candidate)` (hors Protocol, accédé via `isinstance`), `TargetSegment.target_id` (`S2E062A`), `parse_matcher_config`/`parse_targets`/`load_yaml`.
  - Pas de référence en avant : `_TIER_RANK` (T1) → modèles (T2) → `_first_matching_rule` (T3) → `MatchingEngine`/`_explain` (T4) → tests d'explication (T5) → golden (T6) → properties (T7). Chaque symbole est défini avant son usage.
  - Exceptions/sentinelles : « aucun match » = `None` (jamais d'exception) ; cohérent avec le typage `MatchDecision | None`.
- **Ambiguïtés résolues (pas de « l'implémenteur décidera »)** — DÉCISIONS verrouillées en tête :
  1. **Rang de palier** → `_TIER_RANK` dans `engine.py` (pas `config.py`), `dict` O(1), test de cohérence avec `TIERS`.
  2. **Modèle de décision** → `MatchDecision(target_id, rule_name, tier, explanation)` gelée ; « aucun match » = `None` ; pas de `decided_at`/`node_id`/`ed2k_hash` (persistance ultérieure).
  3. **Explication** → `Explanation(target_id, rules_fired, tokens_matched, coverage_values)` gelée, cible gagnante seule, tuples (déterministe/hashable), `value` via `isinstance(CoverageMatcher)`.
  4. **Façade** → `MatchingEngine(config, targets, *, max_filename_length=4096)` pré-résout à la construction ; `evaluate -> MatchDecision | None` unique (PAS de `explain` séparé, explication paresseuse sur la gagnante).
  5. **Corpus golden** → fixture YAML data-driven chargé via `load_yaml`, bout-en-bout sur la vraie chaîne, extensible par la communauté.
  6. **Property-based** → fait main, `random.Random(20260610)` seed constant, PAS de `hypothesis` (dépendance-minimale, déterminisme).
  7. **Bornage de longueur** → in-engine, défaut 4096, écarte (`None`) au-delà, les deux côtés testés ; bornage ingest/EC additionnel noté comme complémentaire et hors périmètre.
- **Argument gate coverage 100 % branch** : chaque conditionnel introduit est testé des DEUX côtés —
  - rang : `>`/`<` des deux sens (T1) + cohérence `TIERS` ;
  - `_first_matching_rule` : 1re vraie (index 0) / saut vers règle ultérieure / aucune vraie → `None` (T3) ;
  - `evaluate` : bornage `len > max` / `len ≤ max` (T4) ; `outcome is None` / non (cible sans contribution vs avec) ; `best is None` (1re cible contributrice) / `key < best` vrai-faux (palier plus haut vs plus bas ; index plus petit ; target_id plus petit) ; `best_resolved is None` (tout écarté → `None`) / non (décision) ;
  - `_explain` : `isinstance(CoverageMatcher)` vrai (config canonique, `coverage_values` non vide) / faux (config sans coverage, vide) ; `rules_fired` à plusieurs (réel 62A : 4 règles) / à 1 (T5) ;
  - golden : parseur d'attendu `discarded` vrai / faux exercé par le corpus (leurres vs décisions), + complétude 3 paliers + 1 écart (T6) ;
  - properties : n'ajoutent aucune branche de prod ; couvrent des chemins existants via de nouvelles entrées (T7).
  Aucun `# pragma: no cover` n'est nécessaire dans `engine.py` (pas de branche logiquement morte ; l'exhaustivité de dispatch reste dans `resolver.py` du Plan 2b).
- **Discipline de périmètre 2c vs plans ultérieurs** : ce plan **évalue** et **décide** en mémoire et **complète le joyau** ; il ne persiste rien (`match_decisions`/`catalog.db`), ne télécharge rien (§9), ne notifie rien (§13), n'expose aucune métrique, ne logge pas (il RETOURNE l'explication). `Rule.tier`/l'ordre des règles sont **consommés pour décider** (c'est précisément l'objet de 2c), mais aucune sortie I/O n'est produite. Tous les consommateurs (DB, download, notify, metrics, EC, verifier) sont annoncés en tête comme HORS PÉRIMÈTRE. ✓
- **Faits vérifiés empiriquement avant rédaction** (contre le source réel des Plans 2a/2b, config canonique §8.3 + cibles §7) :
  - Le réel ep-62A `[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » [Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi` fait feu sur **les 4 règles** ; palier le plus haut = **download** via **id_segment_exact** (index 0) ; `title_hit.value == 1.0`.
  - « Keroro » seul (`Keroro Gunso opening.mkv`) → **catalog/keroro_large** sur S2E062A ET S2E062B (même palier, même index 3) → départage **target_id** → **S2E062A**.
  - Leurre Naruto (`Naruto épisode 062 VF.avi`) → **aucune règle** sur aucune cible → **écarté (`None`)**.
  - Sans extension vidéo (`KERORO N°062A Les demoiselles cambrioleuses.txt`) → id_segment_exact faux, **numero_titre** vrai → **notify/S2E062A**.
  - Quasi-collision 063A → segment_id faux mais **date_teletoon_titre** vrai → reste **download/S2E062A** (collision documentée dans le corpus avec le bon `rule_name`).
  - Départage par **index** isolable : une cible au `target_id` plus grand (S2E099Z) matchant la règle d'index 0 prime sur une cible au `target_id` plus petit (S2E001A) matchant l'index 1 → l'index départage AVANT le `target_id` (test dédié construit en conséquence).
  - `CoverageMatcher.value()` est accessible via `isinstance(resolved.tokens["title_hit"], CoverageMatcher)` (hors Protocol).

### OPEN QUESTIONS FOR THE HUMAN
Aucune. Toutes les ambiguïtés matérielles (rang de palier, forme de décision, explication, façade, format du corpus, property-based, bornage de longueur) ont été résolues depuis la spec et verrouillées dans les DÉCISIONS 1–7.
