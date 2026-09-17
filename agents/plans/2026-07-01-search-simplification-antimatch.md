# Simplification recherche + refonte policy matching — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réduire la recherche eD2k/Kad à deux mots-clés sentinelles issus de config (`keroro` + `titar`) et refondre la policy de matching déclarative (garde franchise universelle, anti-match enrichi, format à trois voies, marqueurs de source upgradeurs, neutralisation des clips par nom, règles découplées).

**Architecture:** Un seul vrai changement de code côté recherche (crawler : `keywords.py` + config `search.keywords` + câblage). Tout le reste est de la **config YAML** de la policy de matching (le moteur `catalog_matching` reste intouché) + mises à jour de tests. Le pipeline de download re-télécharge déjà les épisodes `found` (seul le même hash est dédupliqué) → Lot C = test de régression, pas de changement de code.

**Tech Stack:** Python ≥3.12, uv workspace, `google-re2` (RE2, importé `re2`), `rapidfuzz`, PyYAML, pytest + pytest-asyncio, mypy --strict, ruff.

## Global Constraints

- **100 % branch coverage par package** (`--cov-fail-under=100`, `branch=true`) — gate PER PACKAGE (`cd packages/<pkg> && uv run pytest`). Exercer les DEUX côtés de chaque conditionnel ajouté.
- **TDD strict** : test qui échoue d'abord, le voir échouer, puis l'implémentation minimale. Chaque fonction de test annotée `-> None`, params typés.
- **mypy --strict** sur `src` ET `tests`. **ruff** sélectionne `E,F,I,UP,B,SIM`, ligne 100.
- **RE2** : pas de lookaround ni backreference. `re2.error` à la compilation d'un pattern invalide.
- **RegexMatcher matche sur `fold(candidate.filename)`** (diacritiques retirés, casefold) et est **insensible à la casse** par défaut → tokens en minuscules sans accent (mais on APPEND aux alternations existantes `\b(ITA|KOR|...)\b` telles quelles : `(?i)` + fold rendent la casse indifférente).
- **Échappement regex par fichier** : dans les YAML (`*.yml`, `*.yaml`) les backslashes sont **doublés** (`\\b`, `\\s`, `\\.`, `\\d`, `\\(`) — YAML double-quoté. Dans les dicts Python inline (`test_engine.py`) : **raw strings** `r"..."` à backslash simple.
- **Sync de la policy** : toute modif de la policy va dans **quatre** endroits — `packages/matching/tests/fixtures/canonical_config.yaml` (source de vérité), `deploy/config/crawler/matcher.yml`, `tests/smoke/matcher.yml`, et le dict inline `_CANONICAL_RAW` de `packages/matching/tests/test_engine.py`. De même `search.keywords` va dans `deploy/config/crawler/crawler.yml` + `tests/smoke/crawler.yml` + `tests/smoke/crawler.observer.yml`.
- **Conventional commits** : `feat(...)`, `refactor(...)`, `test:`, `docs:`.
- **Commande test isolé** (le `--cov-fail-under=100` fait « échouer » un test seul) : `cd packages/<pkg> && uv run pytest <chemin>::<test> --no-cov -q`.

---

## Task 1 : Config `search.keywords` (crawler)

Champ additif consommé par personne encore → le package reste vert seul.

**Files:**
- Modify: `packages/crawler/src/emule_indexer/adapters/config/crawler_config.py` (dataclass `CrawlerConfig` ~131-159, parser `parse_crawler_config` ~336-398)
- Test: `packages/crawler/tests/adapters/config/test_crawler_config.py`
- Modify: `deploy/config/crawler/crawler.yml`, `tests/smoke/crawler.yml`, `tests/smoke/crawler.observer.yml`

**Interfaces:**
- Produces: `CrawlerConfig.search_keywords: tuple[str, ...]` (défaut `("keroro", "titar")` si section `search` absente).

- [ ] **Step 1 : test — défaut quand `search` absent.** Dans `test_crawler_config.py`, lire d'abord un cas de config minimal existant (repérer le dict `raw` minimal valide déjà utilisé) puis ajouter :

```python
def test_search_keywords_defaults_to_keroro_and_titar_when_section_absent() -> None:
    config = parse_crawler_config(_minimal_raw(), {})  # _minimal_raw : helper existant OU dict inline minimal valide
    assert config.search_keywords == ("keroro", "titar")


def test_search_keywords_read_from_section() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": ["keroro", "titar", "mission titar"]}
    config = parse_crawler_config(raw, {})
    assert config.search_keywords == ("keroro", "titar", "mission titar")


def test_search_keywords_rejects_empty_list() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": []}
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})


def test_search_keywords_rejects_non_string_entry() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": ["keroro", 42]}
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})
```

Si aucun helper `_minimal_raw()` n'existe, lire le fichier, copier le plus petit dict `raw` déjà valide d'un test existant dans un helper local `_minimal_raw()` en tête de fichier, et l'utiliser. Vérifier que `ConfigError` est déjà importé (sinon `from emule_indexer.adapters.config.crawler_config import ConfigError`).

- [ ] **Step 2 : voir échouer.** `cd packages/crawler && uv run pytest tests/adapters/config/test_crawler_config.py -k search_keywords --no-cov -q` → FAIL (`CrawlerConfig` n'a pas `search_keywords`).

- [ ] **Step 3 : implémenter.** Dans `crawler_config.py` :

Ajouter le champ à la dataclass `CrawlerConfig` (après `node_id`, avant les champs à défaut `None`, ou en champ à défaut) :
```python
    search_keywords: tuple[str, ...] = ("keroro", "titar")
```

Ajouter le parser (près de `_parse_download`) :
```python
def _parse_search_keywords(raw: dict[str, Any]) -> tuple[str, ...]:
    """`search.keywords` : liste de mots-clés non vides. Absent → défaut (keroro, titar)."""
    if "search" not in raw:
        return ("keroro", "titar")
    section = _require_mapping(raw["search"], "section 'search'")
    if "keywords" not in section:
        return ("keroro", "titar")
    keywords = section["keywords"]
    if not isinstance(keywords, list) or not keywords:
        raise ConfigError("search.keywords : liste non vide de chaînes attendue")
    result: list[str] = []
    for entry in keywords:
        if not isinstance(entry, str) or not entry:
            raise ConfigError(f"search.keywords : chaîne non vide attendue, obtenu {entry!r}")
        result.append(entry)
    return tuple(result)
```

Câbler dans `parse_crawler_config` (dans la construction du `CrawlerConfig(...)` retourné, ~ligne 396) :
```python
        search_keywords=_parse_search_keywords(raw),
```
(Vérifier que `_require_mapping` existe déjà dans le fichier — il est utilisé par `_parse_download` ; sinon réutiliser le helper de validation de mapping présent.)

- [ ] **Step 4 : voir passer.** `cd packages/crawler && uv run pytest tests/adapters/config/test_crawler_config.py -k search_keywords --no-cov -q` → PASS.

- [ ] **Step 5 : ajouter le champ aux YAML de config.** Dans `deploy/config/crawler/crawler.yml`, `tests/smoke/crawler.yml`, `tests/smoke/crawler.observer.yml`, ajouter une section (après un bloc de tête, indentation racine) :
```yaml
search:
  keywords: [keroro, titar]
```

- [ ] **Step 6 : gate crawler + commit.**
```bash
cd packages/crawler && uv run pytest -q
git add -A && git commit -m "feat(config): search.keywords (défaut keroro+titar) dans crawler.yml"
```

---

## Task 2 : Recherche → mots-clés de config (retrait du per-target)

Change la signature de `generate_keywords` et de `run_search_cycle` → doit atterrir **vert d'un bloc** (le package refuserait de compiler entre-deux). Un seul commit.

**Files:**
- Modify: `packages/crawler/src/emule_indexer/domain/search/keywords.py` (réécriture)
- Modify: `packages/crawler/src/emule_indexer/application/run_search_cycle.py` (signature ~152-165, ligne 169-170, docstring ligne 6, imports)
- Modify: `packages/crawler/src/emule_indexer/composition/app.py` (appel `run_search_cycle` ~225-237)
- Test: `packages/crawler/tests/domain/search/test_keywords.py` (réécriture)
- Test: `packages/crawler/tests/application/test_run_search_cycle.py` (13 sites d'appel + 4 usages `generate_keywords`)
- Test: `packages/crawler/tests/composition/test_app.py` (si un test assemble/appelle la boucle — lire et adapter)

**Interfaces:**
- Consumes: `CrawlerConfig.search_keywords` (Task 1).
- Produces: `generate_keywords(keywords: Sequence[str]) -> tuple[SearchKeyword, ...]` ; `run_search_cycle(*, workers, clients, keywords: Sequence[str], rng, node_id, cycle_index, scheduler_state, backoff, clock, telemetry, edge)` (le paramètre `targets` disparaît).

- [ ] **Step 1 : test keywords (réécriture complète).** Remplacer tout `packages/crawler/tests/domain/search/test_keywords.py` par :

```python
from emule_indexer.domain.search.keywords import SearchKeyword, generate_keywords


def test_generates_one_keyword_per_input_in_order() -> None:
    keywords = generate_keywords(["keroro", "titar"])
    assert [kw.text for kw in keywords] == ["keroro", "titar"]


def test_origin_is_the_keyword_text() -> None:
    (kw,) = generate_keywords(["keroro"])
    assert kw == SearchKeyword(text="keroro", origin="keroro")


def test_deduplicates_keeping_first_seen_order() -> None:
    keywords = generate_keywords(["keroro", "titar", "keroro"])
    assert [kw.text for kw in keywords] == ["keroro", "titar"]


def test_drops_empty_strings() -> None:
    keywords = generate_keywords(["", "keroro"])
    assert [kw.text for kw in keywords] == ["keroro"]


def test_empty_input_yields_empty_tuple() -> None:
    assert generate_keywords([]) == ()


def test_keyword_is_frozen_and_hashable() -> None:
    keyword = SearchKeyword(text="keroro", origin="keroro")
    assert {keyword, keyword} == {keyword}
    assert hash(keyword) == hash(SearchKeyword(text="keroro", origin="keroro"))
```

- [ ] **Step 2 : voir échouer.** `cd packages/crawler && uv run pytest tests/domain/search/test_keywords.py --no-cov -q` → FAIL (ancienne signature per-target).

- [ ] **Step 3 : réécrire `keywords.py`.** Remplacer le corps par (garder l'en-tête de module en l'adaptant : « deux familles » → « mots-clés issus de config ») :

```python
"""Génération des mots-clés de recherche depuis la config (PUR, spec search-simplification).

Domaine PUR : aucune I/O. Les mots-clés sont fournis par la config (``crawler.yml``,
``search.keywords``) — par défaut ``keroro`` (filet large) + ``titar`` (sentinelle FR,
jackpot-proof). ``generate_keywords`` est déterministe : même liste → même tuple, ORDONNÉ
et DÉDUPLIQUÉ (premier vu gagne), pour que le shuffle seedé du cycle parte d'un ordre stable.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchKeyword:
    """Un mot-clé à rechercher + sa provenance. GELÉ et hashable → déduplication triviale."""

    text: str
    origin: str


def generate_keywords(keywords: Sequence[str]) -> tuple[SearchKeyword, ...]:
    """Liste ORDONNÉE et DÉDUPLIQUÉE des mots-clés (spec search-simplification).

    Ordre = ordre d'entrée ; déduplication par ``text`` (premier vu gagne) ; les chaînes
    vides sont ignorées. ``origin`` = le texte lui-même (provenance = mot-clé de config).
    """
    seen: set[str] = set()
    result: list[SearchKeyword] = []
    for text in keywords:
        if text and text not in seen:
            seen.add(text)
            result.append(SearchKeyword(text=text, origin=text))
    return tuple(result)
```

- [ ] **Step 4 : voir passer keywords.** `cd packages/crawler && uv run pytest tests/domain/search/test_keywords.py --no-cov -q` → PASS.

- [ ] **Step 5 : `run_search_cycle.py` — signature + usage.**
  - Docstring ligne 6 : remplacer « `generate_keywords(targets)` → larges + ciblés » par « `generate_keywords(config_keywords)` → sentinelles ».
  - Signature (~156) : remplacer `targets: Sequence[TargetSegment],` par `keywords: Sequence[str],`.
  - Corps (~169-170) : remplacer
    ```python
    keywords = generate_keywords(targets)
    texts = tuple(keyword.text for keyword in keywords)
    ```
    par
    ```python
    generated = generate_keywords(keywords)
    texts = tuple(keyword.text for keyword in generated)
    ```
  - Retirer l'import `TargetSegment` s'il n'est plus utilisé ailleurs dans le fichier (grep `TargetSegment` sur le fichier ; si zéro autre usage, retirer la ligne d'import).

- [ ] **Step 6 : `composition/app.py` — l'appel.** Dans `_run_loop` (~225-237), remplacer l'argument `targets=self._targets,` de l'appel `run_search_cycle(...)` par `keywords=self._crawler_config.search_keywords,`. (Ne PAS toucher `self._targets`, encore utilisé pour le matching/engine et le download.)

- [ ] **Step 7 : `test_run_search_cycle.py` — balayage mécanique.** Dans ce fichier :
  - Ajouter en tête (après les imports) : `_KEYWORDS = ("keroro", "titar")`.
  - Remplacer **chaque** argument `targets=_TARGETS,` des appels `run_search_cycle(...)` par `keywords=_KEYWORDS,` (13 occurrences).
  - Remplacer **chaque** `generate_keywords(_TARGETS)` par `generate_keywords(_KEYWORDS)` (lignes ~356, 390, 429, 471).
  - Supprimer la constante `_TARGETS` (lignes ~37-45) et l'import `from catalog_matching.models import TargetSegment` (devenus inutiles — vérifier par grep qu'aucun autre usage ne subsiste dans le fichier).

- [ ] **Step 8 : `test_app.py` — adapter si besoin.** Lire `packages/crawler/tests/composition/test_app.py`. S'il construit une `CrawlerConfig` (ou appelle `_run_loop`/`run_search_cycle`) qui suppose l'ancienne signature, ajouter `search_keywords=("keroro", "titar")` aux `CrawlerConfig(...)` construits à la main SI le champ n'a pas de défaut appliqué (il en a un — donc probablement rien à faire). Vérifier surtout qu'aucun test n'asserte l'ancien comportement per-target.

- [ ] **Step 9 : gate crawler complet.** `cd packages/crawler && uv run pytest -q` → tout PASS, coverage 100 %. Si une branche de `generate_keywords` manque (ex. le `if text and text not in seen` : cas `text` vide, cas déjà vu, cas neuf), les tests du Step 1 les couvrent déjà (`test_drops_empty_strings`, `test_deduplicates...`, `test_generates...`).

- [ ] **Step 10 : mypy + ruff + commit.**
```bash
uv run mypy ; uv run ruff check . ; uv run ruff format --check .
git add -A && git commit -m "refactor(search): mots-clés depuis config (keroro+titar), retrait du per-target"
```

---

## Task 3 : Policy de matching — fixtures + golden corpus

TDD : on met à jour le golden corpus (les décisions attendues) et on applique la nouvelle policy à la config canonique (+ copies deploy/smoke). `test_engine.py` (config inline distincte) reste vert et sera modernisé en Task 4.

**Files:**
- Modify: `packages/matching/tests/fixtures/canonical_config.yaml` (réécriture de la policy)
- Modify: `packages/matching/tests/fixtures/golden_corpus.yaml` (2 cas changés + nouveaux)
- Modify: `deploy/config/crawler/matcher.yml`, `tests/smoke/matcher.yml` (sync)
- Test: `packages/matching/tests/test_golden_corpus.py` (piloté par les fixtures, pas de modif de code)

**Interfaces:**
- Produces: la policy cible (tokens `is_keroro`, `is_episode`, `source_marker`, `vf`, `idf1`, `not_episode`, `is_archive`, `foreign_lang` enrichi, `is_video` élargi ; 7 règles découplées).

- [ ] **Step 1 : mettre à jour le golden corpus.** Dans `packages/matching/tests/fixtures/golden_corpus.yaml` :

**(a) Modifier le cas `match_via_teletoon_titre_sans_segment_id`** : `rule_name: teletoon_titre` → `rule_name: title_confirmed` (tier `download`, target `S2E062A` inchangés). Commentaire → « teletoon (source_marker) + titre → title_confirmed (download) ».

**(b) Modifier le cas `numero_titre_no_video_extension`** : extension non vidéo → plus de règle actionnable ; devient catalogue. Remplacer par :
```yaml
  - id: segment_id_non_video_falls_back_to_catalog
    # 062A + titre mais extension NON vidéo -> aucune règle actionnable -> keroro_large (catalog).
    filename: "KERORO N°062A Les demoiselles cambrioleuses.txt"
    tier: catalog
    target_id: S2E062A
    rule_name: keroro_large
```

**(c) Ajouter ces cas** à la fin de la liste `cases:` :
```yaml
  - id: title_review_notify_without_source_marker
    # Titre proche, sans segment_id ni marqueur de source -> title_review (notify).
    filename: "Keroro Les demoiselles cambrioleuses.avi"
    tier: notify
    target_id: S2E062A
    rule_name: title_review

  - id: title_confirmed_via_vf_marker
    # Titre + marqueur VF -> title_confirmed (download).
    filename: "Keroro VF Les demoiselles cambrioleuses.avi"
    tier: download
    target_id: S2E062A
    rule_name: title_confirmed

  - id: title_confirmed_via_idf1_marker
    # Titre + marqueur IDF1 (autre chaîne) -> title_confirmed (download).
    filename: "Keroro IDF1 Les demoiselles cambrioleuses.avi"
    tier: download
    target_id: S2E062A
    rule_name: title_confirmed

  - id: archive_with_segment_id_is_notify
    # Archive (.rar) + segment_id -> archive_candidate (notify, revue humaine).
    filename: "Keroro N°062A Les demoiselles cambrioleuses.rar"
    tier: notify
    target_id: S2E062A
    rule_name: archive_candidate

  - id: not_episode_opening_demotes_title_to_catalog
    # Titre proche MAIS "opening" -> not_episode -> is_episode faux -> pas title_review ;
    # keroro_large (catalog) reste (permissif).
    filename: "Keroro Les demoiselles cambrioleuses opening.avi"
    tier: catalog
    target_id: S2E062A
    rule_name: keroro_large

  - id: non_video_non_archive_mp3_is_catalog
    # .mp3 -> ni is_video ni is_archive -> keroro_large (catalog), pas actionnable.
    filename: "Keroro N°062A Les demoiselles cambrioleuses.mp3"
    tier: catalog
    target_id: S2E062A
    rule_name: keroro_large

  - id: decoy_dino_riders_via_titar
    # titar remonte ce Dino-Riders ES ; "dino-riders" -> foreign_lang -> écarté (même avec titar).
    filename: "Dino-Riders - 10 - Titar pierde los estribos.avi"
    discarded: true

  - id: decoy_italian_content_word_guerriero
    filename: "Keroro il guerriero 62.avi"
    discarded: true

  - id: decoy_country_code_jp
    filename: "Keroro Mission Titar 62 (JP).avi"
    discarded: true

  - id: decoy_signor_italian
    filename: "Keroro signor N°062A.avi"
    discarded: true
```

- [ ] **Step 2 : voir échouer.** `cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q` → FAIL (l'ancienne `canonical_config.yaml` ne produit pas ces décisions).

- [ ] **Step 3 : réécrire `canonical_config.yaml`.** Remplacer tout le fichier par (backslashes DOUBLÉS, YAML) :

```yaml
# Config matcher canonique (cf. spec §8.3 + spec 2026-07-01 search-simplification-antimatch).
# Sert le corpus golden bout-en-bout. Copie de vérité : synchroniser deploy/ et tests/smoke/.
tokens:
  keroro:        { keyword: keroro }
  titar:         { keyword: titar }
  keroro_titar:  { any: [keroro, titar] }
  foreign_lang:  { regex: "\\b(ITA|KOR|Korean|Italiano|Coreano|VOSTFR|VOSTA|Subs?FR|Espa[nñ]ol|English\\s?Dub|ENG)\\b|dino-riders|guerriero|risveglio|sarxento|sargento|benjo|fatacolorata|catala|signor|\\((?:ita|j|jp|k|kr|ks)\\)" }
  french_safe:   { not: foreign_lang }
  is_keroro:     { all: [french_safe, keroro_titar] }
  not_episode:   { regex: "opening|ending|g[eé]n[eé]rique|\\bsample\\b|preview|trailer|bande.?annonce" }
  is_episode:    { all: [is_keroro, { not: not_episode }] }
  teletoon:      { regex: "t[eé]l[eé]toon" }
  idf1:          { regex: "\\bidf\\s?1\\b" }
  vf:            { regex: "\\b(?:vf|vff|vfb)\\b|version\\s?francaise" }
  source_marker: { any: [teletoon, idf1, vf] }
  segment_id:    { regex: "(?:n[°o]?\\s*0*{absolute_number}|s0*{season}\\s*e0*{seasonal_number}|0*{season}\\s*x\\s*0*{seasonal_number})\\s*{segment}" }
  segment_id_loose: { regex: "{mono_gate}(?:^|[^0-9])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9]|$)" }
  title_hit:     { coverage: title, min: 0.6 }
  is_video:      { regex: "\\.(avi|mkv|mp4|mpg|mpeg|divx|m4v|ogm)$" }
  is_archive:    { regex: "\\.(zip|7z|rar|r\\d\\d|z\\d\\d|part\\d+\\.rar)$" }
rules:
  - { name: id_segment_exact,    tier: download, all: [is_episode, is_video, segment_id] }
  - { name: title_confirmed,     tier: download, all: [is_episode, is_video, title_hit, source_marker] }
  - { name: numero_nu_confirmed, tier: download, all: [is_episode, is_video, segment_id_loose, source_marker] }
  - { name: title_review,        tier: notify,   all: [is_episode, is_video, title_hit] }
  - { name: numero_nu,           tier: notify,   all: [is_episode, is_video, segment_id_loose] }
  - { name: archive_candidate,   tier: notify,   all: [is_episode, is_archive, { any: [segment_id, title_hit, source_marker] }] }
  - { name: keroro_large,        tier: catalog,  all: [is_keroro] }
```

- [ ] **Step 4 : voir passer le golden.** `cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q` → PASS (tous les cas, dont `test_corpus_covers_every_tier_and_a_discard`). Si un cas échoue, comparer la décision obtenue à l'attendu et corriger le filename/attendu (ne PAS affaiblir la policy).

- [ ] **Step 5 : synchroniser deploy + smoke.** Copier tokens+rules identiques dans `deploy/config/crawler/matcher.yml` et `tests/smoke/matcher.yml` (conserver l'en-tête de commentaire propre à chaque fichier ; corps `tokens:`/`rules:` identique au Step 3). Vérifier le parse :
```bash
cd /home/geoffrey/Repositories/emule-indexer/.claude/worktrees/feat+search-simplification-antimatch
python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in ['deploy/config/crawler/matcher.yml','tests/smoke/matcher.yml']]; print('yaml ok')"
```

- [ ] **Step 6 : gate matching + commit.**
```bash
cd packages/matching && uv run pytest -q      # 100% branche
git add -A && git commit -m "feat(matching): policy refondue (is_keroro/is_episode, source_marker, archives, not_episode) + golden"
```

---

## Task 4 : Policy de matching — tests inline `test_engine.py` (config canonique + routage mono)

Moderniser le dict inline `_CANONICAL_RAW` et `_MONO_ROUTING_RAW` vers la nouvelle policy, et couvrir les règles à numéro nu (impossibles dans le golden : cibles 62A/62B bi-segment).

**Files:**
- Test: `packages/matching/tests/test_engine.py`

**Interfaces:**
- Consumes: la policy cible (Task 3), rejouée en dict Python (raw strings).

- [ ] **Step 1 : remplacer `_CANONICAL_RAW`** (lignes ~119-158) par la nouvelle policy (raw strings, backslash simple) :

```python
_CANONICAL_RAW: dict[str, object] = {
    "tokens": {
        "keroro": {"keyword": "keroro"},
        "titar": {"keyword": "titar"},
        "keroro_titar": {"any": ["keroro", "titar"]},
        "foreign_lang": {
            "regex": (
                r"\b(ITA|KOR|Korean|Italiano|Coreano|VOSTFR|VOSTA|Subs?FR|"
                r"Espa[nñ]ol|English\s?Dub|ENG)\b|dino-riders|guerriero|risveglio|"
                r"sarxento|sargento|benjo|fatacolorata|catala|signor|\((?:ita|j|jp|k|kr|ks)\)"
            ),
        },
        "french_safe": {"not": "foreign_lang"},
        "is_keroro": {"all": ["french_safe", "keroro_titar"]},
        "not_episode": {
            "regex": r"opening|ending|g[eé]n[eé]rique|\bsample\b|preview|trailer|bande.?annonce"
        },
        "is_episode": {"all": ["is_keroro", {"not": "not_episode"}]},
        "teletoon": {"regex": "t[eé]l[eé]toon"},
        "idf1": {"regex": r"\bidf\s?1\b"},
        "vf": {"regex": r"\b(?:vf|vff|vfb)\b|version\s?francaise"},
        "source_marker": {"any": ["teletoon", "idf1", "vf"]},
        "segment_id": {
            "regex": (
                r"(?:n[°o]?\s*0*{absolute_number}|s0*{season}\s*e0*{seasonal_number}"
                r"|0*{season}\s*x\s*0*{seasonal_number})\s*{segment}"
            )
        },
        "segment_id_loose": {
            "regex": r"{mono_gate}(?:^|[^0-9])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9]|$)"
        },
        "title_hit": {"coverage": "title", "min": 0.6},
        "is_video": {"regex": r"\.(avi|mkv|mp4|mpg|mpeg|divx|m4v|ogm)$"},
        "is_archive": {"regex": r"\.(zip|7z|rar|r\d\d|z\d\d|part\d+\.rar)$"},
    },
    "rules": [
        {"name": "id_segment_exact", "tier": "download", "all": ["is_episode", "is_video", "segment_id"]},
        {"name": "title_confirmed", "tier": "download", "all": ["is_episode", "is_video", "title_hit", "source_marker"]},
        {"name": "numero_nu_confirmed", "tier": "download", "all": ["is_episode", "is_video", "segment_id_loose", "source_marker"]},
        {"name": "title_review", "tier": "notify", "all": ["is_episode", "is_video", "title_hit"]},
        {"name": "numero_nu", "tier": "notify", "all": ["is_episode", "is_video", "segment_id_loose"]},
        {"name": "archive_candidate", "tier": "notify", "all": ["is_episode", "is_archive", {"any": ["segment_id", "title_hit", "source_marker"]}]},
        {"name": "keroro_large", "tier": "catalog", "all": ["is_keroro"]},
    ],
}
```

- [ ] **Step 2 : corriger les assertions dépendantes.**
  - `test_evaluate_notify_tier_when_only_numero_titre_matches` (~204-211) : la règle `numero_titre` n'existe plus. Réécrire pour tester `title_review` :
    ```python
    def test_evaluate_notify_tier_via_title_review() -> None:
        # Titre proche, PAS de marqueur de source -> title_review (notify), pas download.
        candidate = FileCandidate(filename="KERORO Les demoiselles cambrioleuses.avi")
        decision = _canonical_engine().evaluate(candidate)
        assert decision is not None
        assert decision.tier == "notify"
        assert decision.rule_name == "title_review"
        assert decision.target_id == "S2E062A"
    ```
  - `test_explanation_on_real_62a_lists_fired_rules_tokens_and_coverage` (~302-320) : mettre à jour le tuple `rules_fired` — le réel 62A fait désormais feu sur :
    ```python
        assert explanation.rules_fired == (
            "id_segment_exact",
            "title_confirmed",
            "title_review",
            "keroro_large",
        )
    ```
    (Les assertions `"title_hit"/"keroro"/"segment_id" in tokens_matched`, `tokens_matched == sorted(...)`, `coverage_values == (("title_hit", 1.0),)` restent valides.)
  - Vérifier rapidement les autres tests utilisant `_canonical_engine()` : `test_evaluate_real_62a_is_download...` (reste `download`/`id_segment_exact`/`S2E062A` ✓), `test_evaluate_discards_non_keroro_file` (✓), `test_evaluate_highest_tier_comes_from_a_different_target` (« keroro N°062B.avi » → `download`/`id_segment_exact`/`S2E062B` ✓), `test_evaluate_tiebreak_same_tier_lowest_target_id_wins` (« Keroro Gunso opening.mkv » → `catalog`/`keroro_large`/`S2E062A` ✓), `test_evaluate_rejects/accepts_filename...` (« Keroro N°062A.avi » → download ✓), `test_evaluate_explanation_lists_coverage_value_even_below_threshold` (✓). Ne modifier que si un run les fait échouer.

- [ ] **Step 3 : moderniser `_MONO_ROUTING_RAW`** (lignes ~354-376) — remplacer par la policy à numéro nu découplée + not_episode + source_marker :

```python
_MONO_ROUTING_RAW: dict[str, object] = {
    "tokens": {
        "is_video": {"regex": r"\.(avi|mkv)$"},
        "keroro": {"keyword": "keroro"},
        "keroro_titar": {"any": ["keroro"]},
        "foreign_lang": {"regex": r"\b(ITA|KOR)\b"},
        "french_safe": {"not": "foreign_lang"},
        "is_keroro": {"all": ["french_safe", "keroro_titar"]},
        "not_episode": {"regex": r"opening|ending|\bsample\b"},
        "is_episode": {"all": ["is_keroro", {"not": "not_episode"}]},
        "teletoon": {"regex": "t[eé]l[eé]toon"},
        "source_marker": {"any": ["teletoon"]},
        "segment_id": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        "segment_id_loose": {
            "regex": r"{mono_gate}(?:^|[^0-9])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9]|$)"
        },
    },
    "rules": [
        {"name": "id_segment_exact", "tier": "download", "all": ["is_episode", "is_video", "segment_id"]},
        {"name": "numero_nu_confirmed", "tier": "download", "all": ["is_episode", "is_video", "segment_id_loose", "source_marker"]},
        {"name": "numero_nu", "tier": "notify", "all": ["is_episode", "is_video", "segment_id_loose"]},
        {"name": "keroro_large", "tier": "catalog", "all": ["is_keroro"]},
    ],
}
```

- [ ] **Step 4 : mettre à jour + ajouter les tests mono.** Dans les tests utilisant `_mono_routing_engine()` :
  - Renommer l'assertion de `test_evaluate_bare_number_on_mono_target_is_notify_numero_nu_mono` : `rule_name == "numero_nu_mono"` → `"numero_nu"` (garder filename « Keroro 10.avi », tier `notify`, target `S1E010A`).
  - `test_evaluate_bare_number_on_bi_segment_target_never_fires_numero_nu_mono` : inchangé sauf que la règle qui « ne fait jamais feu » est maintenant `numero_nu`/`numero_nu_confirmed` ; l'assertion reste `catalog`/`keroro_large` → OK, pas de changement d'assertion (commentaire à ajuster).
  - `test_evaluate_lettered_mono_number_stays_download...` (« Keroro N°010A.avi » → download/id_segment_exact) : inchangé.
  - `test_evaluate_bare_number_digit_boundary_guard_rejects_substring` (« Keroro 105.avi » → catalog) : inchangé.
  - **Ajouter** :
    ```python
    def test_evaluate_bare_number_on_mono_with_source_marker_is_download() -> None:
        # Numéro nu + marqueur de source (teletoon) sur cible mono -> numero_nu_confirmed (download).
        decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 10 teletoon.avi"))
        assert decision is not None
        assert decision.tier == "download"
        assert decision.rule_name == "numero_nu_confirmed"
        assert decision.target_id == "S1E010A"

    def test_evaluate_opening_with_bare_number_demoted_to_catalog_by_not_episode() -> None:
        # "Keroro 10 opening.avi" : numéro nu mono MAIS "opening" -> not_episode -> is_episode
        # faux -> ni numero_nu ni numero_nu_confirmed ; keroro_large (catalog) reste.
        decision = _mono_routing_engine().evaluate(FileCandidate(filename="Keroro 10 opening.avi"))
        assert decision is not None
        assert decision.tier == "catalog"
        assert decision.rule_name == "keroro_large"
    ```

- [ ] **Step 5 : gate matching complet.** `cd packages/matching && uv run pytest -q` → tout PASS, coverage 100 % branche. Si une branche du moteur perd sa couverture (ex. un `case isinstance` de l'explication), ajuster/compléter un test existant — **ne pas** ajouter de `# pragma` sans justification.

- [ ] **Step 6 : mypy + ruff + commit.**
```bash
cd /home/geoffrey/Repositories/emule-indexer/.claude/worktrees/feat+search-simplification-antimatch
uv run mypy ; uv run ruff check . ; uv run ruff format --check .
git add -A && git commit -m "test(matching): tests inline engine alignés sur la policy refondue + routage mono découplé"
```

---

## Task 5 : Lot C — régression « un épisode found se re-télécharge »

Aucun changement de code : `download_policy` ne skippe que `target_status == "complete"`, jamais posé en PROD (statuts `found`/`lost`). Verrouiller ce comportement par un test.

**Files:**
- Test: `packages/crawler/tests/domain/download/test_policy.py`

- [ ] **Step 1 : lire le fichier** `packages/crawler/tests/domain/download/test_policy.py` pour reprendre le style d'appel de `download_policy(...)` (noms des kwargs : `tier`, `target_status`, `already_downloaded`, `committed_bytes`, `file_size`, `disk_cap`) et l'import de `DownloadVerdict`.

- [ ] **Step 2 : ajouter le test de régression.**
```python
def test_found_target_still_downloads_a_new_file() -> None:
    # Invariant produit (spec search-simplification, Lot C) : un épisode déjà "found" se
    # re-télécharge quand un NOUVEAU fichier le matche (redondance d'archivage voulue).
    # Seul target_status == "complete" skippe ; "found" ne l'est jamais en PROD.
    verdict = download_policy(
        tier="download",
        target_status="found",
        already_downloaded=False,
        committed_bytes=0,
        file_size=100_000_000,
        disk_cap=10_000_000_000,
    )
    assert verdict is DownloadVerdict.DOWNLOAD
```

- [ ] **Step 3 : voir passer + gate.**
```bash
cd packages/crawler && uv run pytest tests/domain/download/test_policy.py --no-cov -q   # PASS (comportement déjà présent)
cd packages/crawler && uv run pytest -q                                                 # 100% branche
git add -A && git commit -m "test(download): régression — un épisode found se re-télécharge (nouveau hash)"
```

---

## Vérification finale (gate complet)

- [ ] **Lancer les huit portes** (depuis la racine du worktree) :
```bash
cd /home/geoffrey/Repositories/emule-indexer/.claude/worktrees/feat+search-simplification-antimatch
( cd packages/matching && uv run pytest -q )
( cd packages/crawler  && uv run pytest -q )
( cd packages/verifier && uv run pytest -q )
( cd packages/webui    && uv run pytest -q )
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run sqlfluff lint packages/crawler/src
uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates
```
Attendu : tout vert. (verifier/webui/sqlfluff/templates ne sont pas touchés mais font partie de la porte.)

- [ ] **Revue holistique** (phase Verify du workflow) : relire le diff complet — cohérence de la policy dans les 4 copies, aucune règle actionnable sans `is_episode`, `title_hit` partout référencé nu, `search.keywords` présent dans les 3 crawler.yml.

---

## Self-review (couverture de la spec)

- Spec §3 Lot A (recherche 2 sentinelles config) → Tasks 1-2. ✓
- Spec §3 Lot B (policy : is_keroro/is_episode, foreign_lang enrichi, format 3 voies, source_marker, not_episode, règles découplées, title_hit nu) → Tasks 3-4. ✓
- Spec §3 Lot C (found se re-télécharge) → Task 5 (no-op + régression, conclusion : aucun skip des found en PROD). ✓
- Spec §5 Tests (keywords, golden decoys/upgrades/archive/opening, mono via inline, sync 4 copies) → Steps des Tasks 2-4. ✓
- Spec §2 non-objectifs (pas de score continu, pas de per-target, pas de taille/attr_between, pas de modif moteur) → respectés : seuls config + tests + `keywords.py`/`run_search_cycle.py`/`app.py` touchés. ✓
