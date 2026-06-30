# Targets Keroro & double numérotation — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommandé) ou superpowers:executing-plans pour implémenter ce plan tâche par tâche. Les étapes utilisent la syntaxe checkbox (`- [ ]`).

**Goal:** Cataloguer les 180 segments S1+S2 de Keroro VF avec titres FR (26 épisodes mono-segment), exposer numéro absolu **et** saisonnal au matching, et supprimer les reliques `broadcast_date`/`{date_alt}` et `aliases`.

**Architecture:** Extension du moteur de matching pur (`packages/matching/`). `TargetSegment` passe de `number` à `absolute_number` + `seasonal_number` ; l'interpolation expose `{season} {seasonal_number} {absolute_number}` ; le token `segment_id` alterne sur la forme absolue (`n°62A`) et saisonnale (`S2E11A`, `2x11A`). La donnée vit dans `deploy/config/crawler/targets.yml`. Aucune base SQL touchée.

**Tech Stack:** Python ≥3.12, dataclasses gelées, `google-re2` (importé `re2`), `rapidfuzz`, pytest (100 % branch), mypy --strict, ruff, sqlfluff.

## Global Constraints

- **Python ≥ 3.12 uniquement.** Domaine `matching` PUR : pas d'I/O, pas de `yaml`/DB/réseau/horloge/logging dans `domain`/le cœur du moteur.
- **TDD strict** : test qui échoue d'abord, on le regarde échouer, puis l'implémentation minimale. Chaque fonction de test annotée `-> None`, params typés.
- **100 % branch coverage PAR PAQUET** (`cd packages/<pkg> && uv run pytest -q`), `--cov-fail-under=100`, `branch=true`. Suites d'intégration deselectionnées, hors gate.
- **`mypy --strict`** sur `src` ET `tests`. **`ruff`** sélectionne `E,F,I,UP,B,SIM`, ligne max **100**.
- **RE2** : pas de lookaround `(?=…)`/`(?<…)`, pas de backreference `\1`. Garde de bord chiffre = guard consommant `(?:^|[^0-9])`, pas `\b`. `re2.escape(...)`/`re2.compile(...)` renvoient `Any` → envelopper `str(...)`.
- **Commits conventionnels** (`feat(domain):`, `refactor:`, `test:`, `docs:`, `chore:`). Co-author footer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **target_id** = `f"S{season}E{absolute_number:03d}{segment.upper()}"` (ex. `S2E062A`).
- **Le gate est par paquet** : après une tâche touchant `matching` ET les tests `crawler` (fixtures partagées), lancer les DEUX suites.

---

## Task 1 : Schéma `TargetSegment` — renommage, extension, suppression dates/aliases

Tâche atomique (un rename + champ requis casse tous les sites d'un coup) : modèle + interpolation + validation + resolver + tous les sites de construction (tests matching ET crawler) + fixtures, en un seul commit vert. `segment_id` est seulement **renommé** ici (`{number}`→`{absolute_number}`, forme absolue inchangée) ; la forme saisonnale arrive en Task 2.

**Files:**
- Modify: `packages/matching/src/catalog_matching/models.py`
- Modify: `packages/matching/src/catalog_matching/interpolation.py`
- Modify: `packages/matching/src/catalog_matching/validation.py`
- Modify: `packages/matching/src/catalog_matching/resolver.py`
- Modify (fixtures): `packages/matching/tests/fixtures/canonical_targets.yaml`, `canonical_config.yaml`
- Modify (config): `deploy/config/crawler/targets.yml`, `deploy/config/crawler/matcher.yml`, `tests/smoke/targets.yml`, `tests/smoke/matcher.yml`
- Modify (tests matching): `test_models.py`, `test_interpolation.py`, `test_validation.py`, `test_resolver.py`, `test_engine.py`, `test_engine_properties.py`
- Modify (tests crawler): `packages/crawler/tests/domain/search/test_keywords.py`, `packages/crawler/tests/composition/test_app.py`, `packages/crawler/tests/adapters/config/test_yaml_loader.py`, `packages/crawler/tests/integration/test_crawler_loop.py`

**Interfaces:**
- Produces : `TargetSegment(season:int, seasonal_number:int, absolute_number:int, segment:str, title:str, status:str="lost")` ; `target_id` sur `absolute_number`. `interpolate(pattern, target)` whitelist `{season} {seasonal_number} {absolute_number} {segment} {title}`. `parse_targets` exige `season`/`seasonal_number`/`absolute_number`.
- Consumes : rien de nouveau.

- [ ] **Step 1 — Écrire les tests d'interpolation cibles (échec attendu).** Dans `packages/matching/tests/test_interpolation.py`, remplace **tout le fichier** par :

```python
import pytest
import re2

from catalog_matching.interpolation import InterpolationError, interpolate
from catalog_matching.models import TargetSegment


def _target() -> TargetSegment:
    return TargetSegment(
        season=2, seasonal_number=11, absolute_number=62, segment="a", title="Les demoiselles"
    )


def test_interpolate_substitutes_absolute_number_and_segment_escaped() -> None:
    pattern = r"n[°o]?\s*0*{absolute_number}\s*{segment}"
    assert interpolate(pattern, _target()) == r"n[°o]?\s*0*62\s*A"


def test_interpolate_substitutes_season_and_seasonal_number() -> None:
    assert interpolate(r"s0*{season}\s*e0*{seasonal_number}", _target()) == r"s0*2\s*e0*11"


def test_interpolate_escapes_regex_special_title() -> None:
    target = TargetSegment(
        season=1, seasonal_number=1, absolute_number=1, segment="a", title="C++ (demo)"
    )
    result = interpolate(r"prefix {title} suffix", target)
    assert result == r"prefix " + re2.escape("C++ (demo)") + r" suffix"
    assert re2.compile(result).search("prefix C++ (demo) suffix") is not None


def test_interpolate_unknown_placeholder_raises() -> None:
    with pytest.raises(InterpolationError, match="bogus"):
        interpolate(r"a {bogus} b", _target())


def test_interpolate_former_number_placeholder_is_now_unknown() -> None:
    # {number} a été renommé {absolute_number} : désormais inconnu (fail-fast).
    with pytest.raises(InterpolationError, match="number"):
        interpolate(r"{number}", _target())


def test_interpolate_leaves_regex_quantifier_braces_untouched() -> None:
    # Un quantificateur RE2 {2,4} n'est PAS un placeholder et reste intact.
    assert interpolate(r"keroro\d{2,4}{absolute_number}", _target()) == r"keroro\d{2,4}62"
```

- [ ] **Step 2 — Lancer, vérifier l'échec.** `( cd packages/matching && uv run pytest tests/test_interpolation.py --no-cov -q )` → FAIL (`TargetSegment` n'accepte pas `seasonal_number`/`absolute_number`).

- [ ] **Step 3 — Réécrire `models.py`.** Remplace `TargetSegment` (et nettoie les imports) :

```python
"""Modèles du moteur de matching (cf. spec §7, §8)."""

from dataclasses import dataclass


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

    Fournit ``{season} {seasonal_number} {absolute_number} {segment} {title}`` à
    l'interpolation des patterns regex.
    """

    season: int
    seasonal_number: int
    absolute_number: int
    segment: str
    title: str
    status: str = "lost"

    @property
    def target_id(self) -> str:
        """Identifiant stable du segment, ex. ``S2E062A``."""
        return f"S{self.season}E{self.absolute_number:03d}{self.segment.upper()}"
```

- [ ] **Step 4 — Réécrire `interpolation.py`** (suppression de toute la machinerie de dates) :

```python
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
    """Substitue la whitelist ``{season} {seasonal_number} {absolute_number} {segment} {title}``.

    Toutes les valeurs sont insérées ``re2.escape``-ées (littérales). Tout autre
    placeholder lève :class:`InterpolationError`.
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
        raise InterpolationError(f"placeholder inconnu : {{{name}}}")

    return _PLACEHOLDER.sub(replace, pattern)
```

- [ ] **Step 5 — `validation.py` : `parse_targets`, sonde, import.** (a) Supprime `import datetime` (ligne 9). (b) Remplace la sonde `_PROBE_TARGET` :

```python
# Cible-sonde pour le compile-check : fournit season/seasonal_number/absolute_number/
# segment/title afin que l'interpolation de toute RegexDef soit testable au chargement.
_PROBE_TARGET = TargetSegment(
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="a",
    title="sonde",
)
```

(c) Remplace le corps de la boucle de `parse_targets` (l'ancien bloc `season=…number=…broadcast…aliases`) par :

```python
    for episode in episodes:
        ep = _require_mapping(episode, "épisode")
        season = int(_require_key(ep, "season", "épisode"))
        seasonal_number = int(_require_key(ep, "seasonal_number", "épisode"))
        absolute_number = int(_require_key(ep, "absolute_number", "épisode"))
        for seg in ep.get("segments", []):
            seg_map = _require_mapping(seg, "segment")
            segments.append(
                TargetSegment(
                    season=season,
                    seasonal_number=seasonal_number,
                    absolute_number=absolute_number,
                    segment=str(_require_key(seg_map, "letter", "segment")),
                    title=str(_require_key(seg_map, "title", "segment")),
                    status=str(seg_map.get("status", "lost")),
                )
            )
```

- [ ] **Step 6 — `resolver.py` : retirer `MissingDateError`.** Ligne 34, remplace l'import par `from catalog_matching.interpolation import interpolate`. Remplace le `case RegexDef` (lignes ~103-111) par :

```python
            case RegexDef(pattern=pattern, flags=flags):
                return RegexMatcher(interpolate(pattern, target), flags=flags)
```

- [ ] **Step 7 — Migrer les fixtures canoniques.** `packages/matching/tests/fixtures/canonical_targets.yaml` :

```yaml
# Cibles canoniques (cf. spec §7). target_id : S2E062A / S2E062B.
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - { letter: A, title: "Les demoiselles cambrioleuses", status: partial }
      - { letter: B, title: "Le grand combat sous-marin", status: partial }
```

`packages/matching/tests/fixtures/canonical_config.yaml` ligne 7 — renomme seulement le placeholder (forme absolue, dual-form en Task 2) :

```yaml
  segment_id:   { regex: "n[°o]?\\s*0*{absolute_number}\\s*{segment}" }
```

- [ ] **Step 8 — Migrer la config PROD + smoke.** Applique la MÊME ligne `segment_id` (avec `{absolute_number}`) dans `deploy/config/crawler/matcher.yml` et `tests/smoke/matcher.yml`. Migre `deploy/config/crawler/targets.yml` et `tests/smoke/targets.yml` au nouveau schéma (épisode 62 seul pour l'instant ; Task 3 remplit S1+S2) :

```yaml
# Cibles de production. Rempli intégralement (S1+S2) par la Task 3 du plan.
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - { letter: A, title: "Les demoiselles cambrioleuses" }   # status défaut "lost"
      - { letter: B, title: "Le grand combat sous-marin" }
```

- [ ] **Step 9 — Réécrire les tests de validation des cibles.** Dans `packages/matching/tests/test_validation.py` : supprime `test_regex_date_alt_placeholder_validates_via_probe` ; remplace les tests `parse_targets` (et tout `number=`/`broadcast_date`/`aliases`) par :

```python
def test_parse_targets_builds_segments_with_per_segment_status() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 2,
                    "seasonal_number": 11,
                    "absolute_number": 62,
                    "segments": [
                        {"letter": "A", "title": "Les demoiselles", "status": "found"},
                        {"letter": "B", "title": "Le grand combat"},
                    ],
                }
            ]
        }
    )
    assert len(targets) == 2
    a, b = targets
    assert a.target_id == "S2E062A"
    assert a.seasonal_number == 11
    assert a.absolute_number == 62
    assert a.status == "found"          # status PROPRE au segment A
    assert b.target_id == "S2E062B"
    assert b.status == "lost"           # défaut, B non marqué


def test_parse_targets_requires_seasonal_number() -> None:
    with pytest.raises(ConfigError, match="seasonal_number"):
        parse_targets(
            {"episodes": [{"season": 1, "absolute_number": 5, "segments": [{"letter": "a", "title": "x"}]}]}
        )


def test_parse_targets_requires_absolute_number() -> None:
    with pytest.raises(ConfigError, match="absolute_number"):
        parse_targets(
            {"episodes": [{"season": 1, "seasonal_number": 5, "segments": [{"letter": "a", "title": "x"}]}]}
        )


def test_parse_targets_default_status_is_lost() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {"season": 1, "seasonal_number": 5, "absolute_number": 5, "segments": [{"letter": "a", "title": "x"}]}
            ]
        }
    )
    assert targets[0].status == "lost"


def test_parse_targets_episode_without_segments() -> None:
    targets = parse_targets({"episodes": [{"season": 1, "seasonal_number": 1, "absolute_number": 1}]})
    assert targets == ()


def test_parse_targets_duplicate_target_id_raises() -> None:
    with pytest.raises(ConfigError, match="double"):
        parse_targets(
            {
                "episodes": [
                    {
                        "season": 2,
                        "seasonal_number": 11,
                        "absolute_number": 62,
                        "segments": [
                            {"letter": "a", "title": "x"},
                            {"letter": "A", "title": "y"},
                        ],
                    }
                ]
            }
        )


def test_regex_with_date_alt_placeholder_is_rejected() -> None:
    # {date_alt} supprimé : un token l'utilisant échoue au chargement (placeholder inconnu).
    with pytest.raises(ConfigError, match="date_alt"):
        parse_matcher_config({"tokens": {"air": {"regex": "{date_alt}"}}, "rules": []})
```

> Adapte les noms/contenus si le fichier existant diffère ; garde les autres tests de `test_validation.py` (DAG, profondeur, overrides) intacts.

- [ ] **Step 10 — Nettoyer `test_resolver.py`.** Supprime `test_resolve_regex_with_date_alt_on_dateless_target_never_matches` et `test_resolve_all_skips_dated_rule_for_dateless_target_without_raising`. Dans la cible globale `_TARGET`, retire `broadcast_date=…` et remplace `number=62` par `seasonal_number=11, absolute_number=62`. Renomme tout `{number}` résiduel en `{absolute_number}`.

- [ ] **Step 11 — Nettoyer `test_models.py`.** Retire les assertions `broadcast_date`/`aliases` ; dans les constructions, remplace `number=N` par `seasonal_number=…, absolute_number=N` (le `target_id` attendu reste basé sur l'absolu). Garde l'assertion `status`.

- [ ] **Step 12 — Migrer `test_engine.py` et `test_engine_properties.py`.** Find/replace mécanique : (a) chaque `TargetSegment(... number=N ...)` → `... seasonal_number=<saisonnal>, absolute_number=N ...` (pour 62 → `seasonal_number=11`; pour les cibles forgées comme `number=99`/`number=1`, mets `seasonal_number` = même valeur, peu importe) ; (b) retire chaque `broadcast_date=datetime.date(...)` et l'import `datetime` s'il devient inutile ; (c) dans les configs embarquées (`_TWO_RULE_RAW`, `_CANONICAL_RAW`, `_INDEX_TIEBREAK_RAW`, et le `raw` inline) remplace `{number}` par `{absolute_number}`.

- [ ] **Step 13 — Migrer les tests crawler.** (a) `test_keywords.py`, `test_app.py`, `test_crawler_loop.py` : retire `broadcast_date=…`, remplace `number=62` par `seasonal_number=11, absolute_number=62`, retire l'import `datetime` s'il devient inutile. (b) `test_yaml_loader.py` : le test vérifie le parsing générique du loader — remplace l'échantillon daté par un champ neutre, par ex. :

```python
    path.write_text(
        'episodes:\n  - { season: 2, seasonal_number: 11, absolute_number: 62 }\n',
        encoding="utf-8",
    )
    data = load_yaml(path)
    assert data["episodes"][0]["absolute_number"] == 62
```

- [ ] **Step 14 — Lancer le gate des DEUX paquets.**

```
( cd packages/matching && uv run pytest -q )
( cd packages/crawler  && uv run pytest -q )
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run sqlfluff lint packages/crawler/src
```

Expected : tout vert (100 % branch sur matching & crawler). Si une branche manque, ajoute le test ciblé (les deux côtés de chaque conditionnel).

- [ ] **Step 15 — Commit.**

```bash
git add -A
git commit -m "refactor(matching): TargetSegment season/seasonal_number/absolute_number, suppression dates+aliases"
```

---

## Task 2 : `segment_id` dual-forme (absolu + saisonnal) + golden corpus

**Files:**
- Modify: `packages/matching/tests/fixtures/golden_corpus.yaml`
- Modify: `packages/matching/tests/fixtures/canonical_config.yaml`
- Modify: `deploy/config/crawler/matcher.yml`, `tests/smoke/matcher.yml`

**Décision (résout §9 de la spec) :** le suffixe `{segment}` reste **obligatoire** dans `segment_id` — c'est un identifiant *au segment* (A/B) servant les tiers `download`/`notify`. Une désignation saisonnale sans lettre (`S2E11`) ne déclenche donc PAS `segment_id` ; elle retombe sur les règles par titre (`teletoon_titre`, `keroro_large`). Précision > recall pour le tier download.

**Interfaces:**
- Consumes : `{season}`, `{seasonal_number}`, `{absolute_number}`, `{segment}` (Task 1).
- Produces : token `segment_id` matchant `n°62A` / `S02E11A` / `2x11A`.

- [ ] **Step 1 — Ajouter les cas golden (échec attendu).** Dans `packages/matching/tests/fixtures/golden_corpus.yaml`, ajoute sous `cases:` :

```yaml
  - id: seasonal_sxxexx_62A_download
    # Forme S02E11A (saison 2, épisode 11, segment A = absolu 62A) + keroro + vidéo.
    filename: "Keroro Mission Titar S02E11A Les demoiselles cambrioleuses.avi"
    tier: download
    target_id: S2E062A
    rule_name: id_segment_exact

  - id: seasonal_NxM_62B_download
    # Forme 2x11B -> segment B (absolu 62B).
    filename: "Keroro Mission Titar 2x11B Le grand combat sous-marin.avi"
    tier: download
    target_id: S2E062B
    rule_name: id_segment_exact

  - id: seasonal_episode_no_segment_falls_back_to_catalog
    # "S2E11" sans lettre -> segment_id NE matche PAS (précision A/B) ; keroro -> catalog, 62A (départage).
    filename: "Keroro S2E11.mkv"
    tier: catalog
    target_id: S2E062A
    rule_name: keroro_large

  - id: decoy_resolution_not_a_segment
    # "1920x1080" ne doit pas déclencher la branche NxM ; keroro -> catalog, PAS download.
    filename: "Keroro 1920x1080 BDRip.mkv"
    tier: catalog
    target_id: S2E062A
    rule_name: keroro_large
```

- [ ] **Step 2 — Lancer, vérifier l'échec.** `( cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q )` → les 2 cas `seasonal_*_download` échouent (segment_id forme absolue seulement).

- [ ] **Step 3 — Réécrire `segment_id` (forme dual).** Dans `packages/matching/tests/fixtures/canonical_config.yaml` ligne 7 :

```yaml
  segment_id:   { regex: "(?:n[°o]?\\s*0*{absolute_number}|s0*{season}\\s*e0*{seasonal_number}|0*{season}\\s*x\\s*0*{seasonal_number})\\s*{segment}" }
```

(Le flag `i` par défaut rend le match insensible à la casse : `S02E11A` et `s02e11a` matchent ; `0*` tolère les zéros de tête.)

- [ ] **Step 4 — Lancer, vérifier le passage.** `( cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q )` → tous les cas passent (anciens absolus + nouveaux saisonnaux + decoys catalog). Si `decoy_resolution_not_a_segment` matche par erreur en download/notify, ajoute un guard de bord chiffre `(?:^|[^0-9])` devant la branche `0*{season}\s*x` et réessaie.

- [ ] **Step 5 — Propager en PROD + smoke.** Applique la même ligne `segment_id` (dual) dans `deploy/config/crawler/matcher.yml` et `tests/smoke/matcher.yml`.

- [ ] **Step 6 — Gate complet des deux paquets** (la config PROD est chargée par `test_main.py` du crawler) :

```
( cd packages/matching && uv run pytest -q )
( cd packages/crawler  && uv run pytest -q )
```

Expected : vert.

- [ ] **Step 7 — Commit.**

```bash
git add -A
git commit -m "feat(matching): segment_id matche numéro absolu ET saisonnal (S2E11/2x11)"
```

---

## Task 3 : Données — catalogue S1+S2 (180 segments) — RÉALISÉE

**Réalité des données (vérifiée par double extraction sonnet+opus du wikitext brut,
identiques) :** la liste FR n'est PAS un A/B uniforme. **26 épisodes mono-segment** (S1 :
10, 11, 22, 23, 25, 26, 31, 33, 35, 46, 51 ; S2 : 20, 22, 23, 24, 25, 27, 29, 37, 39, 43,
44, 49, 50, 51, 52). Total = **180 segments** (S1 = 91, S2 = 89). S1#11 = « Inédit »
(placeholder) **conservé** (décision Geoffrey ; durcissement matcher en Task 4). **17 found**
(S02E52 et S01E10 sont mono → un seul segment found chacun).

**Files:**
- Modify: `deploy/config/crawler/targets.yml` (remplacement intégral, 180 segments)
- Create: `packages/crawler/tests/composition/test_prod_targets.py`

- [x] **Step 1 — Extraction réconciliée.** Deux extractions indépendantes (sonnet+opus) du
  wikitext brut → résultats identiques. Liste figée dans le scratch de session
  (`keroro-episodes-reconciled.txt`). Titres FR **verbatim** (graphies non normalisées
  conservées). `absolute_number` : S1 = seasonal ; S2 = 51 + seasonal.
- [x] **Step 2 — Génération déterministe.** Script jetable (scratch `gen_targets.py`) lit la
  liste réconciliée, émet `targets.yml` (mono → segment A seul ; bi-segment → A+B), pose
  `status: found` sur les 17 segments retrouvés, et auto-contrôle les comptes
  (180 / 91 / 89 / 17, absolus 1–103 contigus). `status` par segment, défaut `lost`.
- [x] **Step 3 — Test data-guard.** `packages/crawler/tests/composition/test_prod_targets.py` :
  charge le YAML PROD via `load_yaml` + `parse_targets` et vérifie **180 cibles uniques**,
  **91 S1 / 89 S2**, **absolus == range(1, 104)**, et l'ensemble exact des **17 `found`**
  (`S1E001A..S1E006B`, `S1E010A`, `S1E027A`, `S1E036A`, `S2E062A`, `S2E103A`).
- [x] **Step 4 — Gate crawler vert** (`uv run pytest -q` → 725 passed/100 % branch ;
  `test_main` charge bien les 180 cibles). Commit `feat(data): catalogue S1+S2 (180 segments)`.

---

## Task 4 : Matcher — épisodes mono sans lettre (niveau **B-safe**)

**Décision Geoffrey : B-safe.** `segment_id` strict **inchangé** (lettre obligatoire, garde
le tier `download`). On ajoute un token `segment_id_loose` (numéro **nu**, **cibles mono
uniquement**, gardes de bord chiffre) consommé par une **nouvelle règle `numero_nu_mono` au
tier `notify`** : un `Keroro 11` nu pour un épisode mono **remonte pour revue (notify), jamais
download**. Pour les cibles bi-segment, `segment_id_loose` est neutralisé (never-match) → la
règle ne les concerne pas.

**Files:**
- Modify: `packages/matching/src/catalog_matching/models.py` (`sole_segment`)
- Modify: `packages/matching/src/catalog_matching/validation.py` (`parse_targets` pose `sole_segment`)
- Modify: `packages/matching/src/catalog_matching/interpolation.py` (placeholder `{mono_gate}`)
- Modify: `packages/matching/tests/fixtures/canonical_config.yaml`, `deploy/config/crawler/matcher.yml`, `tests/smoke/matcher.yml`
- Modify: `packages/matching/tests/{test_models.py,test_validation.py,test_interpolation.py,test_engine.py}`

**Interfaces:**
- `TargetSegment.sole_segment: bool = False` — NON lu du YAML, **dérivé** par `parse_targets`
  (`True` ssi l'épisode n'a qu'un segment). Tous les segments d'un épisode partagent la valeur.
- Placeholder `{mono_gate}` (fragment **brut**, non échappé) : `""` si `target.sole_segment`,
  sinon `[^\s\S]` (classe vide RE2 → **never-match**, neutralise le token pour les bi-segment).

- [ ] **Step 1 — Tests modèle/validation (échec attendu).** Dans `test_models.py` : un
  `TargetSegment(...)` a `sole_segment is False` par défaut. Dans `test_validation.py` :

```python
def test_parse_targets_marks_sole_segment_for_mono_episode() -> None:
    targets = parse_targets(
        {"episodes": [{"season": 1, "seasonal_number": 10, "absolute_number": 10,
                       "segments": [{"letter": "A", "title": "x"}]}]}
    )
    assert targets[0].sole_segment is True


def test_parse_targets_two_segments_are_not_sole() -> None:
    targets = parse_targets(
        {"episodes": [{"season": 2, "seasonal_number": 11, "absolute_number": 62,
                       "segments": [{"letter": "A", "title": "x"}, {"letter": "B", "title": "y"}]}]}
    )
    assert [t.sole_segment for t in targets] == [False, False]
```

- [ ] **Step 2 — Lancer → échec** (`sole_segment` inexistant). `( cd packages/matching && uv run pytest tests/test_validation.py -k sole --no-cov -q )`.

- [ ] **Step 3 — Modèle + parse_targets.** Ajoute `sole_segment: bool = False` à
  `TargetSegment` (après `status`). Dans `parse_targets`, AVANT la boucle des segments :
  `seg_list = ep.get("segments", [])` puis `sole = len(seg_list) == 1` ; passe
  `sole_segment=sole` à chaque `TargetSegment`. Itère `for seg in seg_list:`.

- [ ] **Step 4 — Test interpolation `{mono_gate}` (échec attendu).**

```python
def test_interpolate_mono_gate_empty_for_sole_segment() -> None:
    t = TargetSegment(season=1, seasonal_number=10, absolute_number=10, segment="a",
                      title="x", sole_segment=True)
    assert interpolate(r"{mono_gate}KEROW", t) == "KEROW"


def test_interpolate_mono_gate_never_match_for_multi_segment() -> None:
    t = TargetSegment(season=2, seasonal_number=11, absolute_number=62, segment="a", title="x")
    assert interpolate(r"{mono_gate}KEROW", t) == r"[^\s\S]KEROW"
```

- [ ] **Step 5 — Interpolation.** Dans `interpolate`, ajoute la branche :
  `if name == "mono_gate": return "" if target.sole_segment else r"[^\s\S]"`. Mets à jour la
  docstring de la whitelist.

- [ ] **Step 6 — Token + règle (configs).** Dans `canonical_config.yaml`,
  `deploy/config/crawler/matcher.yml` et `tests/smoke/matcher.yml`, ajoute le token et la
  règle (le `segment_id` strict de Task 2 reste **inchangé**) :

```yaml
  segment_id_loose: { regex: "{mono_gate}(?:^|[^0-9])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9]|$)" }
# … dans rules:, APRÈS numero_titre, AVANT keroro_large :
  - { name: numero_nu_mono, tier: notify, all: [french_safe, is_video, keroro, segment_id_loose] }
```

  > La sonde de validation (`_PROBE_TARGET`, `sole_segment=False`) interpole
  > `[^\s\S](?:^|[^0-9])0*(?:62|11)(?:[^0-9]|$)` qui **compile** sous RE2 → chargement OK.
  > Le golden corpus (cibles bi-segment 62A/62B) : `segment_id_loose` = never-match →
  > `numero_nu_mono` ne fait jamais feu → **aucune régression** (ne PAS ajouter de cible mono
  > à `canonical_targets.yaml` : ça casserait le départage catalog `keroro_only_...`).

- [ ] **Step 7 — Tests moteur routing (échec → vert).** Dans `test_engine.py`, config inline
  avec strict + loose + 2 règles (`id_segment_exact` download, `numero_nu_mono` notify) et 2
  cibles : `target_mono` (`sole_segment=True`, ex. S1 seasonal 10 absolute 10) et
  `target_bi` (62A, `sole_segment=False`). Asserte :
  - `FileCandidate("Keroro 10.avi")` → tier `notify`, règle `numero_nu_mono`, cible mono
    (numéro nu mono → notify, **pas** download) ;
  - `FileCandidate("Keroro 62.avi")` → la cible bi NE matche PAS `segment_id_loose` (never)
    → résultat `catalog`/`keroro_large` ou `None`, **jamais** `numero_nu_mono` ;
  - `FileCandidate("Keroro N°010A.avi")` → strict `segment_id` matche (lettre présente) →
    `download`/`id_segment_exact` (le mono structuré-avec-lettre reste éligible download) ;
  - `FileCandidate("Keroro 105.avi")` → garde de bord : `10` dans `105` ne matche pas →
    pas de `numero_nu_mono`.

- [ ] **Step 8 — Gate des deux paquets** (matching + crawler, `test_main` charge la config
  PROD) 100 % branch ; `mypy`, `ruff check/format`, `sqlfluff`. Restaure toute branche
  manquante par un test ciblé.

- [ ] **Step 9 — Commit** `feat(matching): segment_id_loose mono notify-only (B-safe)`.

---

## Task 5 : Docs, handoff, tag

**Files:**
- Modify: `CLAUDE.md`
- Create: `docs/handoffs/2026-06-30 - handoff - targets keroro double numerotation.md`

- [ ] **Step 1 — Mettre à jour `CLAUDE.md`.** `grep -n -e "{number}" -e "date_alt" -e "broadcast_date" -e "aliases" -e "folded French months" CLAUDE.md`. Remplace la description de `interpolation.py` (Architecture) — la liste `{number} {segment} {title} {date_alt}` + « folded French months » devient `{season} {seasonal_number} {absolute_number} {segment} {title}` (plus de dates). Les exemples `S2E062A` restent valides.

- [ ] **Step 2 — Lancer la garde templates** (inchangée mais fait partie du gate) :

```
uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates
```

- [ ] **Step 3 — Gate COMPLET (les 4 paquets + outillage).**

```
( cd packages/matching && uv run pytest -q )
( cd packages/crawler  && uv run pytest -q )
( cd packages/verifier && uv run pytest -q )
( cd packages/webui    && uv run pytest -q )
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run sqlfluff lint packages/crawler/src
```

Expected : tout vert.

- [ ] **Step 4 — Écrire le handoff.** `docs/handoffs/2026-06-30 - handoff - targets keroro double numerotation.md` : état courant (**180 cibles** S1+S2 dont 26 mono-segment, double numérotation absolu+saisonnal, dates/aliases supprimés, **17 found**), ce qui a été construit, pièges (le suffixe `{segment}` obligatoire pour les bi-segment ; **statut par segment** ; **26 épisodes mono** ; « Inédit » S1#11 conservé ; mono sans lettre routé **notify-only** via `segment_id_loose` — niveau **B-safe**, Task 4 ; la précision NxM autres saisons non testée par le golden corpus), prochaine étape suggérée, et ce qui n'est **PAS** validé contre vrai matériel (recall/precision réels des formes saisonnales et numéro-nu sur des noms eD2k réels — à confirmer en prod ; recherche `keywords.py` n'émet que la forme absolue).

- [ ] **Step 5 — Commit docs.**

```bash
git add CLAUDE.md "docs/handoffs/2026-06-30 - handoff - targets keroro double numerotation.md"
git commit -m "docs: handoff targets Keroro + maj placeholders interpolation"
```

- [ ] **Step 6 — Tag annoté (non poussé).**

```bash
git tag -a v0.21.0-targets-keroro -m "Catalogue Keroro S1+S2, double numérotation, nettoyage dates/aliases"
```

---

## Self-review (auteur du plan)

- **Couverture spec** : §3 nommage/obligatoires → Task 1 ; §4 modèle → Task 1 ; §5 interpolation + segment_id → Task 1 (rename) + Task 2 (dual) ; suppression dates/aliases → Task 1 ; §6 données (180) → Task 3 ; §6 bis matcher mono → Task 4 ; §7 tests → réparti ; §8 livrables (CLAUDE.md, tag, handoff) → Task 5. ✓
- **Décisions tranchées** : `{segment}` obligatoire pour les bi-segment (Task 2) ; statut **par segment** (Task 1/Task 3) ; structure mono confirmée — 26 épisodes mono, S2E52 mono (Task 3) ; mono sans lettre = niveau **B-safe** notify-only (Task 4). ✓
- **Type-consistance** : `seasonal_number`/`absolute_number`/`season`/`segment`/`title`/`status`/`sole_segment` cohérents dans modèle, interpolation, parse_targets, tests, fixtures. `{absolute_number}` partout (plus de `{number}`). ✓
- **Réalité vs hypothèse** : l'hypothèse « 206 / 2 segments par épisode » est **fausse** ; réel = **180 segments** (26 mono), figé par le test data-guard de Task 3.
