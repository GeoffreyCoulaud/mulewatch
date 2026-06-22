# WebUI catalogue (lecture seule) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer un 3ᵉ service `catalog_webui` (Starlette SSR, lecture seule) qui rend le catalogue d'un nœud consultable par un humain (vue par cible, explorateur, détail avec explication de match recalculée, état du nœud) — précédé de l'**extraction** du moteur de matching en paquet partagé `catalog_matching`.

**Architecture:** Clean/Hexagonal. Tâche 1 = extraction mécanique du domaine pur du matching (`emule_indexer.domain.matching` → paquet `catalog_matching`), consommé par le crawler ET le webui (gate-vert = critère de fin, comportement inchangé). Puis le paquet `catalog_webui` : `domain/` pur (view-models + dérivations testables) ; `adapters/` SQLite **read-only** (`mode=ro`+`query_only`), lecture YAML, moteur `catalog_matching`, templates Jinja2 **sans logique** + CSS vendoré ; `composition/build_app()` câble une app Starlette (routes GET). Aucune écriture nulle part.

**Tech Stack:** Python ≥ 3.12. `catalog_matching` : `google-re2`, `rapidfuzz` (pur, aucune I/O). `catalog_webui` : `starlette`, `uvicorn`, `jinja2`, `pyyaml`, `catalog-matching` (workspace) ; **pas** de `httpx` en runtime (seulement en dev pour les tests `ASGITransport`). Tests : `pytest`+`pytest-asyncio`+`pytest-cov` (100 % branch par paquet), `httpx.ASGITransport` in-process. `ruff` (E/F/I/UP/B/SIM, l.100), `mypy --strict` (src+tests), `sqlfluff` (SQL embarqué du webui). CSS vendoré servi par `StaticFiles` (pas de CDN). Garde « templates sans logique » en pre-push + CI.

## Global Constraints

- **100 % branch coverage par paquet** (`--cov-fail-under=100`, `branch=true`). Jamais baisser le seuil ; ajouter le test manquant.
- **TDD strict** : test qui échoue d'abord → run/échec → impl minimale → run/pass → gate → commit. Chaque fonction de test annotée `-> None`, params typés.
- **`mypy --strict`** (racine, span tous les paquets, src + tests) ; **`ruff`** `E,F,I,UP,B,SIM`, line-length **100**.
- **Clean/Hexagonal** : `domain/` PUR (aucun import I/O : pas de sqlite, yaml, jinja, starlette, réseau, horloge). Tout l'I/O en `adapters/`.
- **W-D2/W-D3 (frontière)** : `catalog_webui` importe `catalog_matching` (lib pure) — **jamais** `emule_indexer` ni `download_verifier`. Il lit les DB par SQL direct. Les tests webui **ne** créent **pas** les bases via le runner de migrations du crawler : ils posent un DDL embarqué (fixture) — couplage de schéma assumé, le smoke réel attrape la dérive.
- **W-D2 (lecture seule)** : toute connexion SQLite du webui en `mode=ro` + `PRAGMA query_only=ON`. Aucune écriture, jamais.
- **Conventional commits** (`feat(...)`, `refactor(...)`, `chore(...)`, `test:`, `docs:`). Chaque message se termine par le trailer HEREDOC `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Python only** : aucun JS custom, aucun build step. CSS vendoré (un fichier).

> **Spec :** `docs/superpowers/specs/2026-06-22-webui-catalogue-design.md` — §2 (décisions W-D1..W-D10), §3 (extraction), §4 (archi webui), §5 (routes), §6 (read model), §7 (recalcul explication), §8 (config), §9 (rendu/garde), §10 (packaging), §11 (éthique), §12 (tests), §13 (DoD), §16 (risques). Plan de référence (style/densité, restructuration workspace) : `docs/superpowers/plans/2026-06-13-crawler-mvp-07-verification-pipeline.md` (Task 1).

> **Le gate (devient 7 checks après Task 1 ; 100 % branch PAR PAQUET) — utilisé VERBATIM dans chaque step « Vérifier » :**
> ```bash
> ( cd packages/crawler  && uv run pytest -q )      # crawler
> ( cd packages/verifier && uv run pytest -q )      # verifier
> ( cd packages/matching && uv run pytest -q )      # NOUVEAU (Task 1)
> ( cd packages/webui    && uv run pytest -q )      # NOUVEAU (Task 3)
> uv run ruff check . && uv run ruff format --check .
> uv run mypy
> uv run sqlfluff lint packages/crawler/src packages/webui/src
> ```
> **Run focalisé** : `( cd packages/<pkg> && uv run pytest tests/<…>::<test> --no-cov -q )`.

---

## File Structure

```
emule-indexer/                                    # racine = workspace VIRTUEL
├── pyproject.toml                                # Modify : workspace members (auto via packages/*) + ruff.src + mypy.files (matching, webui) ; sources
├── .github/workflows/ci.yml                      # Modify : + pytest matching + pytest webui + sqlfluff webui + garde templates
├── .githooks/pre-push                            # Modify : idem
├── CLAUDE.md                                     # Modify : 4 paquets, gate, webui dans la table des sous-systèmes
├── packages/
│   ├── crawler/                                  # paquet emule_indexer
│   │   ├── pyproject.toml                         # Modify : deps -google-re2/-rapidfuzz +catalog-matching
│   │   └── src/emule_indexer/…                    # Modify : 10 fichiers src (imports matching→catalog_matching) ; tests 13 fichiers
│   ├── matching/                                 # NOUVEAU — paquet catalog_matching (dist catalog-matching)
│   │   ├── pyproject.toml                         # Create
│   │   ├── src/catalog_matching/                  # MOVED depuis emule_indexer/domain/matching/ + explain dans engine.py
│   │   │   ├── __init__.py models.py config.py validation.py interpolation.py
│   │   │   ├── matchers.py combinators.py resolver.py engine.py
│   │   │   └── normalization.py                   # MOVED (dépendance unique du matching)
│   │   └── tests/                                 # MOVED depuis crawler/tests/domain/matching/ (+ test_normalization)
│   └── webui/                                     # NOUVEAU — paquet catalog_webui (dist catalog-webui)
│       ├── pyproject.toml                         # Create
│       ├── Dockerfile                             # Create (multi-stage uv)
│       ├── src/catalog_webui/
│       │   ├── __init__.py  __main__.py
│       │   ├── domain/ : views.py  coverage.py  format.py        # PUR
│       │   ├── adapters/
│       │   │   ├── db.py                            # open_ro (mode=ro + query_only)
│       │   │   ├── catalog_read.py  local_read.py  targets_read.py  matching_read.py
│       │   │   ├── templates/ : base.html dashboard.html files.html file_detail.html node.html
│       │   │   └── static/ : app.css               # CSS vendoré
│       │   └── composition/app.py                  # build_app + routes
│       ├── tests/                                  # miroir + tests/fixtures/schema.sql (DDL embarqué)
│       └── (garde) scripts/check_templates.py      # garde « templates sans logique » (testée)
├── bricks/compose.core.yaml                       # Modify : + service webui (profils observer/download)
└── docs/runbook-administration.md                 # Modify : section WebUI (reverse proxy, env, où regarder)
```

> **Carte de dépendance (signatures, cohérence vérifiée) :**
> - `catalog_matching.engine.MatchingEngine.explain(self, candidate: FileCandidate, target_id: str) -> Explanation | None` (NOUVEAU, Task 2).
> - `catalog_webui.adapters.db.open_ro(path: Path) -> sqlite3.Connection` (mode=ro + query_only).
> - `catalog_webui.domain.format` : `ed2k_link(ed2k_hash: str, filename: str, size_bytes: int) -> str` ; `short_hash(ed2k_hash: str) -> str`.
> - `catalog_webui.domain.coverage.coverage_for(target_id: str, decisions: Sequence[DecisionRow]) -> CoverageStatus` → `"found"|"partial"|"none"` + meilleur tier.
> - `catalog_webui.domain.views` : `TargetCoverage`, `FileRow`, `FileDetail`, `NodeState` (frozen DTOs, **précalculés** — aucun calcul en template).
> - `catalog_webui.adapters.catalog_read.CatalogReader` : `target_coverage()`, `list_files(*, target, tier, verdict, query, page)`, `file_detail(ed2k_hash)`.
> - `catalog_webui.adapters.local_read.LocalReader.node_state() -> NodeState`.
> - `catalog_webui.adapters.targets_read.load_targets(path: Path) -> tuple[TargetSegment, ...]` ; `matching_read.MatchingExplainer.explain(file_row) -> Explanation | None`.
> - `catalog_webui.composition.app.build_app(*, catalog_db: Path, local_db: Path, targets: Path, matcher: Path, templates_dir: Path, static_dir: Path) -> Starlette`.

---

## Task 1 : Extraction du moteur de matching → paquet `catalog_matching`

**Files :** `git mv` 9 modules + `normalization.py` + 12 tests ; Create `packages/matching/pyproject.toml` + `tests/__init__.py` ; rewrite imports dans **33 fichiers** ; Modify `packages/crawler/pyproject.toml` (deps), racine `pyproject.toml` (ruff.src + mypy.files), `.github/workflows/ci.yml`, `.githooks/pre-push`, `CLAUDE.md`. **AUCUN changement de comportement.** Critère de fin : **gate vert sur 3 paquets**.

> **Décision W-D1.** Mécanique : on déplace le domaine pur tel quel. `normalization.py` part AUSSI (dépendance unique du matching — vérifier qu'aucun autre module crawler ne l'importe ; si si, voir Step 3). Risque = imports oubliés → `mypy`/`ruff`/`pytest` les attrapent (le gate EST la preuve).

- [ ] **Step 1 : créer le squelette du paquet + `git mv` des modules**

```bash
mkdir -p packages/matching/src/catalog_matching packages/matching/tests
git mv packages/crawler/src/emule_indexer/domain/matching/__init__.py packages/matching/src/catalog_matching/__init__.py
for m in models config validation interpolation matchers combinators resolver engine; do
  git mv packages/crawler/src/emule_indexer/domain/matching/$m.py packages/matching/src/catalog_matching/$m.py
done
git mv packages/crawler/src/emule_indexer/domain/normalization.py packages/matching/src/catalog_matching/normalization.py
rmdir packages/crawler/src/emule_indexer/domain/matching
git mv packages/crawler/tests/domain/matching packages/matching/tests/_matching_tmp
# remonter les fichiers de test au niveau tests/ du nouveau paquet
git mv packages/matching/tests/_matching_tmp/*.py packages/matching/tests/
rmdir packages/matching/tests/_matching_tmp
git mv packages/crawler/tests/domain/test_normalization.py packages/matching/tests/test_normalization.py
```

> **Vérifier la dépendance `normalization`** AVANT de continuer :
> ```bash
> grep -rn "domain.normalization\|domain import normalization" packages/crawler/src packages/crawler/tests
> ```
> Si un module crawler HORS matching l'importe, NE PAS le déplacer : laisser `normalization.py` au crawler et ajouter `catalog_matching` une dépendance vers… — non : le domaine doit rester pur. Dans ce cas, **dupliquer** n'est pas permis ; à la place, `normalization` devient un module de `catalog_matching` ET le crawler l'importe depuis là (`from catalog_matching.normalization import fold`). Adapter Step 3 en conséquence. (Ground-truth actuel : seuls `matchers.py` et un fichier de `domain.search` l'utilisent → le 2ᵉ importeur se réécrit en Step 3.)

- [ ] **Step 2 : `packages/matching/pyproject.toml` + `tests/__init__.py`**

`packages/matching/tests/__init__.py` : fichier VIDE.
`packages/matching/pyproject.toml` :
```toml
[project]
name = "catalog-matching"
version = "0.0.0"
description = "Moteur de matching fichier→épisode (domaine pur, YAML-driven) — partagé crawler/webui"
requires-python = ">=3.12"
dependencies = [
    "google-re2>=1.1.20251105",
    "rapidfuzz>=3.14.5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/catalog_matching"]

[tool.pytest.ini_options]
addopts = '--cov=catalog_matching --cov-report=term-missing --cov-fail-under=100 --strict-markers'
testpaths = ["tests"]

[tool.coverage.run]
branch = true
source = ["catalog_matching"]

[tool.coverage.report]
show_missing = true
fail_under = 100
exclude_also = ["if TYPE_CHECKING:"]
```

- [ ] **Step 3 : réécrire les 33 importeurs (`emule_indexer.domain.matching.*` → `catalog_matching.*`, `emule_indexer.domain.normalization` → `catalog_matching.normalization`)**

Réécriture scriptée puis vérification :
```bash
grep -rl "emule_indexer.domain.matching\|emule_indexer.domain.normalization\|emule_indexer\.domain import normalization" packages/crawler packages/matching \
  | xargs sed -i \
      -e 's/emule_indexer\.domain\.matching/catalog_matching/g' \
      -e 's/emule_indexer\.domain\.normalization/catalog_matching.normalization/g'
# Vérifier qu'il ne reste AUCUNE référence à l'ancien chemin :
grep -rn "domain.matching\|domain.normalization" packages/ || echo "OK aucun reste"
```
> Les fichiers de test DÉPLACÉS dans `packages/matching/tests/` qui faisaient `from emule_indexer.domain.matching.engine import …` deviennent `from catalog_matching.engine import …` (couverts par le `sed`). Vérifier ensuite qu'aucun import inter-tests `from tests.…` ne subsiste dans les tests matching déplacés (ils étaient autonomes) : `grep -rn "from tests" packages/matching/tests` → attendu vide.

- [ ] **Step 4 : `packages/crawler/pyproject.toml` — deps**

Retirer `google-re2` et `rapidfuzz` de `[project] dependencies` (transitives via `catalog-matching`) ; ajouter `catalog-matching` :
```toml
dependencies = [
    "apprise>=1.9",
    "catalog-matching",
    "pyyaml>=6.0.3",
    "httpx>=0.28",
    "prometheus-client>=0.21",
]
```

- [ ] **Step 5 : racine `pyproject.toml` — workspace, sources, ruff, mypy**

`[tool.uv.workspace] members = ["packages/*"]` couvre déjà `packages/matching`. Ajouter la source + étendre ruff/mypy :
```toml
[tool.uv.sources]
emule-indexer = { workspace = true }
download-verifier = { workspace = true }
catalog-matching = { workspace = true }
```
Dans `[tool.ruff] src = [...]` ajouter `"packages/matching/src"`, `"packages/matching/tests"`.
Dans `[tool.mypy] files = [...]` ajouter `"packages/matching/src"`, `"packages/matching/tests"`.
Dans `[dependency-groups] dev = [...]` ajouter `"catalog-matching"`.
(L'override `[[tool.mypy.overrides]] module = "re2"` reste — il s'applique au workspace entier.)

- [ ] **Step 6 : régénérer le lock + sync**

```bash
uv lock && uv sync --dev
```
Expected : `catalog-matching` résolu, installé éditable ; crawler le voit (deps transitives `google-re2`/`rapidfuzz` toujours présentes).

- [ ] **Step 7 : adapter gate (CI + hook + CLAUDE.md)**

`.github/workflows/ci.yml` : ajouter après la ligne crawler/verifier :
```yaml
      - run: ( cd packages/matching && uv run pytest )
```
et remplacer `uv run sqlfluff lint packages/crawler/src` par `uv run sqlfluff lint packages/crawler/src` (inchangé ici — le webui l'étendra au Task 13).
`.githooks/pre-push` : ajouter `echo "[pre-push] pytest matching…"; ( cd packages/matching && uv run pytest )`.
`CLAUDE.md` : section « What this is » / « Commands » — mentionner le 3ᵉ paquet `packages/matching` (paquet `catalog_matching`, dist `catalog-matching`) et ajouter sa ligne au gate. Mettre à jour la table des sous-systèmes (Matching engine → `packages/matching/src/catalog_matching/`).

- [ ] **Step 8 : Vérifier (le gate EST la preuve)**

```bash
( cd packages/matching && uv run pytest -q )   # tous les tests matching déplacés → PASS, 100 % branch
( cd packages/crawler  && uv run pytest -q )   # inchangé : PASS, 100 % branch
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
Expected : 3 paquets verts, 100 % branch chacun, mypy/ruff verts. Si un import a été oublié → `mypy`/`pytest` le signale ; corriger l'import (jamais baisser le seuil).

- [ ] **Step 9 : Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(matching): extraction du moteur en paquet partagé catalog_matching

git mv domaine pur (9 modules + normalization + 12 tests) vers packages/matching ;
33 importeurs réécrits (emule_indexer.domain.matching -> catalog_matching) ;
google-re2/rapidfuzz -> deps du paquet matching (transitives crawler) ; gate
étendu (3e paquet). Comportement inchangé : tous les tests matching + crawler verts.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 : `MatchingEngine.explain(candidate, target_id)` (pour le recalcul webui)

**Files :**
- Modify: `packages/matching/src/catalog_matching/engine.py`
- Modify/Create: `packages/matching/tests/test_engine.py` (ou `test_explain.py`)

> Spec W-D7/§7. La fiche détail du webui veut l'`Explanation` de la cible **stockée** (pas seulement de la gagnante de `evaluate`). On factorise la construction d'`Explanation` par cible (que `evaluate` fait déjà pour la gagnante) en une méthode publique `explain(candidate, target_id)`, et `evaluate` la réutilise pour la gagnante (refactor sans changement de comportement). Cible inconnue → `None` ; cible connue mais aucune règle vraie → `Explanation` à `rules_fired=()` (utile pour « pourquoi ça ne matche pas/plus »). **Lire d'abord `engine.py`** pour la forme exacte de la résolution par cible (`resolver`/`ResolvedTarget`) et la construction d'`Explanation` existante.

- [ ] **Step 1 : Écrire le test qui échoue** (`packages/matching/tests/test_explain.py`)

```python
from catalog_matching.engine import MatchingEngine
from catalog_matching.models import Explanation, FileCandidate
from catalog_matching.validation import parse_matcher_config, parse_targets

_MATCHER = {
    "tokens": {"keroro": {"keyword": "keroro"}, "titar": {"keyword": "titar"}},
    "rules": [{"name": "keroro_large", "tier": "catalog", "any": ["keroro", "titar"]}],
}
_TARGETS = {
    "episodes": [
        {"season": 2, "number": 62,
         "segments": [{"letter": "A", "title": "Les demoiselles cambrioleuses"}]}
    ]
}


def _engine() -> MatchingEngine:
    return MatchingEngine(parse_matcher_config(_MATCHER), parse_targets(_TARGETS))


def test_explain_known_target_with_match_returns_explanation() -> None:
    result = _engine().explain(FileCandidate(filename="keroro_062.avi"), "S2E062A")
    assert isinstance(result, Explanation)
    assert result.target_id == "S2E062A"
    assert "keroro_large" in result.rules_fired


def test_explain_unknown_target_returns_none() -> None:
    assert _engine().explain(FileCandidate(filename="x"), "S9E999Z") is None


def test_explain_known_target_no_rule_fired_returns_empty_explanation() -> None:
    result = _engine().explain(FileCandidate(filename="random.txt"), "S2E062A")
    assert isinstance(result, Explanation)
    assert result.rules_fired == ()
```

- [ ] **Step 2 : Run → FAIL** : `( cd packages/matching && uv run pytest tests/test_explain.py --no-cov -q )` → `AttributeError: 'MatchingEngine' object has no attribute 'explain'`.

- [ ] **Step 3 : Implémenter** `explain` dans `engine.py` (réutiliser la résolution par cible existante ; factoriser la construction d'`Explanation` de `evaluate`). Forme attendue :
```python
def explain(self, candidate: FileCandidate, target_id: str) -> Explanation | None:
    """Explique le match de ``candidate`` contre la cible ``target_id`` (config courante).

    ``None`` si ``target_id`` est inconnu de la config. Sinon une ``Explanation`` (vide si
    aucune règle ne se déclenche). Réutilise l'arbre de matchers résolu par cible.
    """
    resolved = self._resolved_by_target.get(target_id)   # adapter au champ réel lu dans engine.py
    if resolved is None:
        return None
    return self._build_explanation(candidate, resolved)  # factorisé depuis evaluate
```
> Adapter aux noms réels (`engine.py`). `evaluate` doit ensuite appeler `_build_explanation` pour sa gagnante (mêmes valeurs qu'avant → tests existants restent verts = preuve du refactor).

- [ ] **Step 4 : Run → PASS** + gate matching (`( cd packages/matching && uv run pytest -q )`, 100 % branch ; les 3 branches d'`explain` couvertes ; `evaluate` inchangé).

- [ ] **Step 5 : Commit** `feat(matching): MatchingEngine.explain(candidate, target_id) pour le recalcul read-only`.

---

## Task 3 : Scaffold du paquet `catalog_webui` (gate-vert sur 4 paquets)

**Files :** Create `packages/webui/pyproject.toml`, `src/catalog_webui/__init__.py`, `tests/test_package.py` ; Modify racine `pyproject.toml` (sources + ruff + mypy + dev), `.github/workflows/ci.yml`, `.githooks/pre-push`.

- [ ] **Step 1 : squelette**
```bash
mkdir -p packages/webui/src/catalog_webui/domain packages/webui/src/catalog_webui/adapters/templates packages/webui/src/catalog_webui/adapters/static packages/webui/src/catalog_webui/composition packages/webui/tests/fixtures packages/webui/scripts
: > packages/webui/src/catalog_webui/__init__.py
```
`packages/webui/pyproject.toml` :
```toml
[project]
name = "catalog-webui"
version = "0.0.0"
description = "WebUI de consultation du catalogue (lecture seule, SSR) — déployable séparé"
requires-python = ">=3.12"
dependencies = [
    "catalog-matching",
    "starlette>=1.3",
    "uvicorn>=0.30",
    "jinja2>=3.1",
    "pyyaml>=6.0.3",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/catalog_webui"]

[tool.pytest.ini_options]
addopts = '--cov=catalog_webui --cov-report=term-missing --cov-fail-under=100 --strict-markers'
testpaths = ["tests"]
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "function"

[tool.coverage.run]
branch = true
source = ["catalog_webui"]

[tool.coverage.report]
show_missing = true
fail_under = 100
exclude_also = ["if TYPE_CHECKING:"]

[tool.sqlfluff.core]
dialect = "sqlite"
max_line_length = 100

[tool.sqlfluff.rules.references.keywords]
ignore_words = "key,value"
```
`packages/webui/tests/test_package.py` :
```python
import catalog_webui


def test_package_is_importable() -> None:
    assert catalog_webui.__name__ == "catalog_webui"
```

- [ ] **Step 2 : racine `pyproject.toml`** — ajouter `catalog-webui = { workspace = true }` (sources), `"catalog-webui"` (dev), `"packages/webui/src"`+`"packages/webui/tests"` (ruff.src ET mypy.files).

- [ ] **Step 3 : gate** — `ci.yml` + `.githooks/pre-push` : ajouter `( cd packages/webui && uv run pytest )` ; remplacer le sqlfluff par `uv run sqlfluff lint packages/crawler/src packages/webui/src` (le `packages/webui/src` n'a pas encore de SQL → sqlfluff sur un dossier sans .sql est un no-op vert).

- [ ] **Step 4 : Vérifier** : `uv lock && uv sync --dev` ; `( cd packages/webui && uv run pytest -q )` → `1 passed`, 100 %. Gate complet vert (4 paquets).

- [ ] **Step 5 : Commit** `chore(webui): scaffold paquet catalog_webui (gate 4 paquets)`.

---

## Task 4 : Connexion SQLite read-only (`adapters/db.py`)

**Files :** Create `packages/webui/src/catalog_webui/adapters/db.py`, `tests/adapters/test_db.py`, `tests/fixtures/__init__.py` + `tests/fixtures/schema.py` (DDL embarqué).

> W-D2 / §16. `open_ro` ouvre en `file:…?mode=ro` (URI) + `PRAGMA query_only=ON`. **Ne PAS** poser `journal_mode=WAL` (ce serait une écriture). Point empirique #1 : si `mode=ro` strict échoue sur une base WAL vivante en prod, le repli (compose) est un montage **RW** + `query_only` — le code `open_ro` reste identique (query_only garantit l'absence d'écriture), seul le flag URI passe à `rw`. On teste avec une vraie base fichier (créée par le DDL embarqué).

> **`tests/fixtures/schema.py`** : copie MINIMALE du DDL (tables lues par le webui), SANS les triggers (inutiles en lecture) — couplage de schéma assumé (Global Constraints). Inclure `files`, `file_observations`, `match_decisions`, `file_verifications` (catalog) et `downloads`, `verification_tasks`, `scheduler_state`, `node_runtime` (local). Reproduire les colonnes EXACTES des migrations (cf. `…/migrations/catalog/0001_initial.sql` et `local/0001_initial.sql` + `0002`). Fournir `def make_catalog(path: Path) -> None` et `def make_local(path: Path) -> None` qui `sqlite3.connect`, exécutent le DDL, **posent `journal_mode=WAL`** (pour tester le chemin RO sur WAL), commit, ferment.

- [ ] **Step 1 : test** (`tests/adapters/test_db.py`)
```python
import sqlite3
from pathlib import Path

import pytest

from catalog_webui.adapters.db import open_ro
from tests.fixtures.schema import make_catalog


def test_open_ro_reads_rows(tmp_path: Path) -> None:
    db = tmp_path / "catalog.db"
    make_catalog(db)
    with sqlite3.connect(db) as seed:  # writer unique simulé
        seed.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", ("a" * 32, 10))
        seed.commit()
    conn = open_ro(db)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
        assert count == 1
    finally:
        conn.close()


def test_open_ro_refuses_writes(tmp_path: Path) -> None:
    db = tmp_path / "catalog.db"
    make_catalog(db)
    conn = open_ro(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", ("b" * 32, 1))
    finally:
        conn.close()


def test_open_ro_rows_are_dict_addressable(tmp_path: Path) -> None:
    db = tmp_path / "catalog.db"
    make_catalog(db)
    conn = open_ro(db)
    try:
        conn.row_factory  # configuré
        row = conn.execute("SELECT 1 AS n").fetchone()
        assert row["n"] == 1
    finally:
        conn.close()
```

- [ ] **Step 2 : Run → FAIL** (module absent).

- [ ] **Step 3 : impl** (`adapters/db.py`)
```python
"""Connexion SQLite STRICTEMENT en lecture seule (spec webui W-D2 / §16).

``open_ro`` ouvre la base en ``mode=ro`` (URI) et pose ``PRAGMA query_only=ON`` : double
garde, jamais d'écriture. On NE pose PAS ``journal_mode=WAL`` (ce serait une écriture) ;
la base est en WAL côté crawler (writer unique), le lecteur en hérite. ``row_factory`` =
``sqlite3.Row`` pour un accès par nom de colonne dans les adapters de lecture.
"""

import sqlite3
from pathlib import Path


def open_ro(path: Path) -> sqlite3.Connection:
    """Ouvre ``path`` en lecture seule (``mode=ro`` + ``query_only``)."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection
```

- [ ] **Step 4 : Run → PASS** + gate webui (100 %).
- [ ] **Step 5 : Commit** `feat(webui): open_ro (sqlite mode=ro + query_only)`.

---

## Task 5 : Domaine pur — `format.py` (lien ed2k, hash court)

**Files :** Create `packages/webui/src/catalog_webui/domain/__init__.py` (vide), `domain/format.py`, `tests/domain/test_format.py`.

> W-D7/W-D8/W-D9. PUR (aucun I/O). `ed2k_link` reconstruit `ed2k://|file|<name>|<size>|<hash>|/`. `short_hash` → 8 premiers + `…`.

- [ ] **Step 1 : test**
```python
from catalog_webui.domain.format import ed2k_link, short_hash


def test_ed2k_link_is_canonical() -> None:
    link = ed2k_link("a" * 32, "Keroro 062.avi", 12345)
    assert link == f"ed2k://|file|Keroro 062.avi|12345|{'a' * 32}|/"


def test_short_hash_truncates_with_ellipsis() -> None:
    assert short_hash("a" * 32) == "aaaaaaaa…"


def test_short_hash_short_input_is_unchanged() -> None:
    assert short_hash("abc") == "abc"
```

- [ ] **Step 2 : Run → FAIL.**
- [ ] **Step 3 : impl** (`domain/format.py`)
```python
"""Formatage pur pour l'affichage (spec webui §4/§7). Aucun I/O."""


def ed2k_link(ed2k_hash: str, filename: str, size_bytes: int) -> str:
    """Reconstruit le lien eD2k canonique d'un fichier observé."""
    return f"ed2k://|file|{filename}|{size_bytes}|{ed2k_hash}|/"


def short_hash(ed2k_hash: str) -> str:
    """Hash tronqué pour l'affichage (8 premiers caractères + ellipse)."""
    if len(ed2k_hash) <= 8:
        return ed2k_hash
    return f"{ed2k_hash[:8]}…"
```

- [ ] **Step 4 : Run → PASS** + gate (les deux côtés de `len <= 8`).
- [ ] **Step 5 : Commit** `feat(webui): domain.format (lien ed2k, hash court)`.

---

## Task 6 : Domaine pur — `coverage.py` + `views.py` (DTOs précalculés)

**Files :** Create `domain/coverage.py`, `domain/views.py`, `tests/domain/test_coverage.py`.

> W-D8 : toute dérivation (statut, libellé, meilleur tier) est PRÉCALCULÉE ici → les templates n'ont aucune logique. `coverage_for` mappe l'ensemble des décisions d'une cible vers un statut. Tiers (du plus fort au plus faible, cf. `matcher.yaml`) : `download` > `notify` > `catalog`. Règle : aucune décision → `none` ; au moins une `download` → `found` ; sinon → `partial`.

- [ ] **Step 1 : test** (`tests/domain/test_coverage.py`)
```python
from catalog_webui.domain.coverage import CoverageStatus, coverage_for


def test_no_decision_is_none() -> None:
    status = coverage_for("S2E062A", [])
    assert status == CoverageStatus(status="none", best_tier=None, file_count=0)


def test_download_tier_is_found() -> None:
    status = coverage_for("S2E062A", [("h1", "download"), ("h2", "catalog")])
    assert status.status == "found"
    assert status.best_tier == "download"
    assert status.file_count == 2


def test_only_weak_tiers_is_partial() -> None:
    status = coverage_for("S2E062A", [("h1", "catalog"), ("h2", "notify")])
    assert status.status == "partial"
    assert status.best_tier == "notify"
```

- [ ] **Step 2 : Run → FAIL.**
- [ ] **Step 3 : impl**

`domain/views.py` (DTOs frozen, ajoutés au fil des tasks ; ici `CoverageStatus`/`TargetCoverage`) :
```python
"""View-models PRÉCALCULÉS (spec webui W-D8) : les templates n'itèrent et n'interpolent
que ces champs — aucune logique côté template."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageStatus:
    status: str            # "found" | "partial" | "none"
    best_tier: str | None  # "download" | "notify" | "catalog" | None
    file_count: int
```
`domain/coverage.py` :
```python
"""Dérivation pure du statut de couverture d'une cible (spec webui §5). Aucun I/O."""

from collections.abc import Sequence

from catalog_webui.domain.views import CoverageStatus

# Du plus fort au plus faible (cf. config/crawler/matcher.yaml).
_TIER_RANK = {"download": 3, "notify": 2, "catalog": 1}


def coverage_for(target_id: str, decisions: Sequence[tuple[str, str]]) -> CoverageStatus:
    """``decisions`` = ``(ed2k_hash, tier)`` des derniers verdicts pour cette cible."""
    if not decisions:
        return CoverageStatus(status="none", best_tier=None, file_count=0)
    best = max(decisions, key=lambda d: _TIER_RANK.get(d[1], 0))[1]
    status = "found" if best == "download" else "partial"
    return CoverageStatus(status=status, best_tier=best, file_count=len(decisions))
```

- [ ] **Step 4 : Run → PASS** + gate (les 3 branches : vide / download / faible).
- [ ] **Step 5 : Commit** `feat(webui): domain.coverage + views (statut de couverture précalculé)`.

---

## Task 7 : `catalog_read.py` — read model du catalogue

**Files :** Create `adapters/catalog_read.py`, `tests/adapters/test_catalog_read.py`. Modify `domain/views.py` (+ `FileRow`, `FileDetail`, `ObservationRow`, `VerificationRow`, `DecisionView`).

> W-D6/§6. SQL paramétré, **read-only** (via `open_ro`). Trois lectures : couverture par cible (group by `target_id`, dernier tier par fichier) ; explorateur (`files` ⨝ dernière observation ⨝ dernière décision ⨝ dernier verdict, filtres `WHERE` + `LIMIT/OFFSET`) ; détail (toutes observations + dernière décision + tous verdicts d'un hash). « Dernier » = `ROW_NUMBER() OVER (PARTITION BY ed2k_hash ORDER BY <ts> DESC, id DESC)`. **SQL sqlfluff-lint** (dialecte sqlite). Tests contre une vraie base peuplée (fixtures DDL).

- [ ] **Step 1 : test** — peupler `files`/`file_observations`/`match_decisions`/`file_verifications` via `make_catalog` + INSERTs, puis asserter :
```python
from pathlib import Path

import sqlite3

from catalog_webui.adapters.catalog_read import CatalogReader
from catalog_webui.adapters.db import open_ro
from tests.fixtures.schema import make_catalog


def _seed(db: Path) -> None:
    make_catalog(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", ("a" * 32, 100))
        c.execute(
            "INSERT INTO file_observations (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id) VALUES"
            " (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("a" * 32, "keroro_062.avi", 100, 5, 2, "[]", "keroro", "2026-06-22T10:00:00.000000+00:00", "n1"),
        )
        c.execute(
            "INSERT INTO match_decisions (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("a" * 32, "S2E062A", "id_segment_exact", "download", "2026-06-22T10:00:01.000000+00:00", "n1"),
        )
        c.commit()


def test_target_coverage_groups_by_target(tmp_path: Path) -> None:
    db = tmp_path / "catalog.db"
    _seed(db)
    reader = CatalogReader(open_ro(db))
    coverage = reader.target_coverage()  # dict[str, list[(hash, tier)]]
    assert coverage["S2E062A"] == [("a" * 32, "download")]


def test_list_files_filters_by_verdict(tmp_path: Path) -> None:
    db = tmp_path / "catalog.db"
    _seed(db)
    reader = CatalogReader(open_ro(db))
    rows = reader.list_files(target=None, tier=None, verdict=None, query=None, page=1)
    assert rows[0].ed2k_hash == "a" * 32
    assert rows[0].filename == "keroro_062.avi"
    assert rows[0].source_count == 5
    assert reader.list_files(target=None, tier=None, verdict="malicious", query=None, page=1) == []


def test_file_detail_carries_observations_and_decision(tmp_path: Path) -> None:
    db = tmp_path / "catalog.db"
    _seed(db)
    detail = CatalogReader(open_ro(db)).file_detail("a" * 32)
    assert detail is not None
    assert detail.size_bytes == 100
    assert detail.decision is not None and detail.decision.target_id == "S2E062A"
    assert len(detail.observations) == 1


def test_file_detail_unknown_hash_is_none(tmp_path: Path) -> None:
    db = tmp_path / "catalog.db"
    _seed(db)
    assert CatalogReader(open_ro(db)).file_detail("f" * 32) is None
```

- [ ] **Step 2 : Run → FAIL.**
- [ ] **Step 3 : impl** — `domain/views.py` ajoute `FileRow`, `ObservationRow`, `VerificationRow`, `DecisionView`, `FileDetail` (tous frozen). `adapters/catalog_read.py` : une classe `CatalogReader(connection)` avec les 3 méthodes ; SQL en constantes module (paginé via `_PAGE_SIZE = 50`, `OFFSET (page-1)*_PAGE_SIZE`). Filtres optionnels composés en SQL paramétré (clauses `AND` conditionnelles côté Python — branches couvertes par les tests). Le détail compose : `file_detail` → `None` si `files` n'a pas le hash. (Écrire le SQL complet ; valider `uv run sqlfluff lint packages/webui/src`.)

- [ ] **Step 4 : Run → PASS** + gate (chaque filtre testé présent/absent ; détail trouvé/None ; sqlfluff vert).
- [ ] **Step 5 : Commit** `feat(webui): catalog_read (couverture, explorateur filtré, détail) read-only`.

---

## Task 8 : `local_read.py` — état du nœud

**Files :** Create `adapters/local_read.py`, `tests/adapters/test_local_read.py`. Modify `domain/views.py` (+ `NodeState`, `DownloadRow`, `VerifTaskRow`).

> §5 `/node`. Lectures `downloads`, `verification_tasks`, `scheduler_state` (KV), `node_runtime` (KV → `node_id`/`created_at`). Tout read-only. État concret (listes + KV), pas de séries temporelles.

- [ ] **Step 1 : test** — `make_local` + INSERTs (un download `active`, une tâche `pending`, `scheduler_state` cycle_index, `node_runtime` node_id) → asserter `NodeState` (compteurs par état, valeurs KV). Inclure le cas DB vide (mode observer) → tout à zéro/None, pas d'erreur.
- [ ] **Step 2 : Run → FAIL.**
- [ ] **Step 3 : impl** — `LocalReader(connection).node_state() -> NodeState` ; `NodeState` porte `downloads: tuple[DownloadRow,...]`, `verification_tasks: tuple[VerifTaskRow,...]`, `scheduler: Mapping[str,str]`, `node_id: str | None`, `created_at: str | None`. KV via `SELECT value FROM scheduler_state WHERE key=?` (None si absent → branche couverte).
- [ ] **Step 4 : Run → PASS** + gate (peuplé + vide).
- [ ] **Step 5 : Commit** `feat(webui): local_read (état du nœud / ordonnancement) read-only`.

---

## Task 9 : `targets_read.py` + `matching_read.py` (cibles + recalcul d'explication)

**Files :** Create `adapters/targets_read.py`, `adapters/matching_read.py`, `tests/adapters/test_targets_read.py`, `tests/adapters/test_matching_read.py`.

> W-D5/W-D6/W-D7/§7. `targets_read.load_targets(path)` = `yaml.safe_load` + `catalog_matching.parse_targets` (pas de réimplémentation). `matching_read.MatchingExplainer` construit `MatchingEngine(parse_matcher_config(load matcher.yaml), load_targets(...))` une fois, et expose `explain(filename, size_bytes, media_length_sec, bitrate_kbps, target_id) -> Explanation | None` — reconstruit un `FileCandidate` **comme le crawler** (lire `emule_indexer/domain/observation.py` pour la conversion exacte `size_bytes → size_mb` et reproduire la MÊME formule ici, sans importer le crawler) puis appelle `engine.explain`.

- [ ] **Step 1 : tests** — `load_targets` sur un YAML minimal → `("S2E062A",)` ; `MatchingExplainer.explain(...)` sur un matcher minimal → `Explanation` (cible qui matche / cible inconnue → `None`).
- [ ] **Step 2 : Run → FAIL.**
- [ ] **Step 3 : impl** — `load_targets` lève une erreur claire si fichier illisible/racine non-mapping (motif `yaml_loader` du crawler, réimplémenté minimalement, PAS importé). `MatchingExplainer` cache l'engine. `FileCandidate` : `size_mb` calculé exactement comme le crawler (vérifier `observation.py`), `duration_sec=media_length_sec`, `bitrate_kbps` tel quel.
- [ ] **Step 4 : Run → PASS** + gate.
- [ ] **Step 5 : Commit** `feat(webui): targets_read + matching_read (recalcul Explanation via catalog_matching)`.

---

## Task 10 : Templates Jinja2 sans logique + CSS vendoré + garde pre-push/CI

**Files :** Create `adapters/templates/{base,dashboard,files,file_detail,node}.html`, `adapters/static/app.css`, `packages/webui/scripts/check_templates.py`, `tests/test_check_templates.py`. Modify `.github/workflows/ci.yml`, `.githooks/pre-push`.

> W-D8. Templates : `extends`/`block`/`include`, `for` (+`else`), `{{ x }}`/`{{ x.attr }}` UNIQUEMENT. Garde = check Python qui rejette `{% if %}`/`{% elif %}`/`{% set %}`/`{% macro %}` et expressions calculées. **Choisir la plus simple qui répond au besoin** (W-D8) : commencer par le **match de tokens** (regex sur les balises interdites) ; si trop de faux positifs apparaissent à l'usage, basculer sur un walk d'AST (`jinja2.Environment().parse` → rejet de `nodes.If/Assign/Macro/Filter/Call`). Le check est **testé 100 % branch** (template conforme accepté ; chaque balise interdite rejetée).

- [ ] **Step 1 : test du garde** (`tests/test_check_templates.py`)
```python
from pathlib import Path

import pytest

from scripts.check_templates import find_logic_violations  # type: ignore[import-not-found]


def test_clean_template_has_no_violation(tmp_path: Path) -> None:
    (tmp_path / "ok.html").write_text("<ul>{% for f in files %}<li>{{ f.name }}</li>{% endfor %}</ul>")
    assert find_logic_violations(tmp_path) == []


@pytest.mark.parametrize(
    "body",
    [
        "{% if x %}a{% endif %}",
        "{% set y = 1 %}",
        "{% macro m() %}{% endmacro %}",
        "{{ a + b }}",
    ],
)
def test_forbidden_constructs_are_flagged(tmp_path: Path, body: str) -> None:
    (tmp_path / "bad.html").write_text(body)
    assert find_logic_violations(tmp_path) != []
```
> `scripts/` doit être importable depuis les tests : ajouter `packages/webui/scripts/__init__.py` (vide) et s'assurer que la rootdir pytest (depuis `packages/webui`) le voit (il l'est : `scripts` est sous le paquet). Si la résolution d'import pose souci, déplacer le garde sous `src/catalog_webui/_dev/check_templates.py` (couvert par coverage) — décision à l'impl ; garder le test identique.

- [ ] **Step 2 : Run → FAIL.**
- [ ] **Step 3 : impl** `check_templates.py` — `find_logic_violations(directory: Path) -> list[str]` (token-match : regex `{% *(if|elif|set|macro)\b` + détection d'opérateur dans `{{...}}`), et un `main()` (`# pragma: no cover` sur `if __name__`) qui exit ≠0 si violations. Écrire les **5 templates** (logic-free) + `app.css` vendoré (feuille minimale sémantique). Les templates n'utilisent que des champs **précalculés** (Tasks 6-8).
- [ ] **Step 4 : Run → PASS.** Câbler le garde au gate : `.github/workflows/ci.yml` + `.githooks/pre-push` → ajouter `uv run python packages/webui/scripts/check_templates.py packages/webui/src/catalog_webui/adapters/templates`.
- [ ] **Step 5 : Vérifier** gate complet (garde verte sur les vrais templates) + 100 % branch.
- [ ] **Step 6 : Commit** `feat(webui): templates Jinja2 sans logique + CSS vendoré + garde pre-push/CI`.

---

## Task 11 : `build_app()` + routes Starlette (SSR)

**Files :** Create `composition/__init__.py` (vide), `composition/app.py`, `tests/composition/test_app.py`.

> §5/§8. Routes GET : `/`, `/files`, `/files/{ed2k_hash}`, `/targets/{target_id}`, `/node`, `/health`, `/static`. `build_app(*, catalog_db, local_db, targets, matcher, templates_dir, static_dir)` ouvre les lecteurs RO, construit `MatchingExplainer`, instancie `Jinja2Templates(directory=templates_dir)` + monte `StaticFiles(directory=static_dir)`, pose tout dans `app.state`. Handlers : récupèrent les view-models depuis les readers et rendent un template. **Aucune logique** hors view-models. Testé via `httpx.ASGITransport`.

- [ ] **Step 1 : test** (`tests/composition/test_app.py`) — fixture qui crée `catalog.db`+`local.db` (DDL+INSERTs), des `targets.yaml`/`matcher.yaml` temporaires, et pointe `templates_dir`/`static_dir` sur les vrais dossiers du paquet (`Path(catalog_webui.__file__).parent / "adapters" / ...`). Asserter, via `_client` (ASGITransport) :
  - `/health` → 200 ;
  - `/` → 200 + contient le `target_id` et le statut ;
  - `/files` → 200 + ligne du fichier ; `/files?verdict=malicious` → 200 + page vide (pas d'erreur) ;
  - `/files/{hash}` peuplé → 200 + lien ed2k + l'explication recalculée ; hash inconnu → 404 ;
  - `/targets/{id}` → 200 ; `/node` → 200 + état.
```python
def _client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
```

- [ ] **Step 2 : Run → FAIL.**
- [ ] **Step 3 : impl** `composition/app.py` — `build_app(...)`, un handler par route (chacun : reader → view-model → `templates.TemplateResponse(name, {"request": request, ...})`). `/files/{hash}` → `file_detail` `None` → `templates.TemplateResponse("404.html", {...}, status_code=404)` (ou `PlainTextResponse(status_code=404)`). `/health` → `JSONResponse({"status": "ok"})`. `Mount("/static", StaticFiles(directory=static_dir))`. Instance module-level `app` pour uvicorn n'est PAS posée ici (les chemins viennent de l'env au Task 12) — `build_app` est la fabrique testable.
- [ ] **Step 4 : Run → PASS** + gate (chaque route ; branche `file_detail None`→404).
- [ ] **Step 5 : Commit** `feat(webui): build_app + routes SSR (dashboard, files, detail, node, health)`.

---

## Task 12 : `__main__.py` (uvicorn) + config par env

**Files :** Create `src/catalog_webui/__main__.py`, `tests/test_main.py`.

> §8. Env : `WEBUI_HOST` (déf `127.0.0.1`), `WEBUI_PORT` (déf `8080`), `CATALOG_DB`, `LOCAL_DB`, `TARGETS_CONFIG`, `MATCHER_CONFIG`. `main()` construit l'app via `build_app(...)` (chemins depuis l'env, `templates_dir`/`static_dir` relatifs au paquet) puis `uvicorn.run(app, host, port)`. Chemin manquant → erreur claire (fail-fast). Testé : `main()` avec `uvicorn.run` monkeypatché ; un chemin DB absent → erreur.

- [ ] **Step 1 : test** — monkeypatch `uvicorn.run`, env complet (chemins de DBs réelles créées) → asserte l'appel `uvicorn.run(app, host=..., port=...)` ; env sans `CATALOG_DB` → `RuntimeError`/`KeyError` clair.
- [ ] **Step 2 : Run → FAIL.**
- [ ] **Step 3 : impl** — lire l'env, résoudre `templates_dir = Path(__file__).parent/"adapters"/"templates"`, `static_dir = .../"static"`, `build_app(...)`, `uvicorn.run(app, host=..., port=int(...))`. `if __name__ == "__main__": # pragma: no cover`.
- [ ] **Step 4 : Run → PASS** + gate.
- [ ] **Step 5 : Commit** `feat(webui): entrée uvicorn (python -m catalog_webui) + config par env`.

---

## Task 13 : Packaging (Dockerfile + service compose) + runbook + tag

**Files :** Create `packages/webui/Dockerfile` ; Modify `bricks/compose.core.yaml` (+ service `webui`), `docs/runbook-administration.md` (section WebUI). Tag annoté en fin.

- [ ] **Step 1 : `packages/webui/Dockerfile`** (calque du verifier, multi-stage uv, non-root 999, sans dépendance binaire) :
```dockerfile
# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/crawler/pyproject.toml,target=packages/crawler/pyproject.toml \
    --mount=type=bind,source=packages/verifier/pyproject.toml,target=packages/verifier/pyproject.toml \
    --mount=type=bind,source=packages/matching/pyproject.toml,target=packages/matching/pyproject.toml \
    --mount=type=bind,source=packages/webui/pyproject.toml,target=packages/webui/pyproject.toml \
    uv sync --locked --no-install-workspace --package catalog-webui
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package catalog-webui

FROM python:3.12-slim-bookworm
RUN groupadd --system --gid 999 nonroot \
    && useradd --system --gid 999 --uid 999 --create-home nonroot
COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "catalog_webui"]
```

- [ ] **Step 2 : service `webui` dans `bricks/compose.core.yaml`** — profils `observer` + `download` ; monte les volumes DB + config en RO ; durcissement standard ; pas de réseau applicatif (seul un port exposé). Healthcheck `/health`.
```yaml
  webui:
    image: ghcr.io/geoffreycoulaud/emule-indexer-webui:${IMAGE_TAG:-latest}
    build:
      context: .
      dockerfile: packages/webui/Dockerfile
    profiles: [observer, download]
    runtime: ${CONTAINER_RUNTIME:-runc}
    environment:
      WEBUI_HOST: 0.0.0.0
      WEBUI_PORT: "8080"
      CATALOG_DB: /data/catalog/catalog.db
      LOCAL_DB: /data/local/local.db
      TARGETS_CONFIG: /app/config/targets.yaml
      MATCHER_CONFIG: /app/config/matcher.yaml
    volumes:
      - catalog-db:/data/catalog:ro
      - local-db:/data/local:ro
      - ./config/crawler/targets.yaml:/app/config/targets.yaml:ro
      - ./config/crawler/matcher.yaml:/app/config/matcher.yaml:ro
    ports:
      - "${WEBUI_PORT:-8080}:8080"
    user: "999:999"
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    pids_limit: 128
    mem_limit: 256m
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8080/health').status==200 else sys.exit(1)"]
      interval: 30s
      timeout: 3s
      retries: 3
      start_period: 5s
    restart: unless-stopped
```
> **Vérifier les chemins DB on-disk** : confirmer les noms de fichiers réels (`catalog.db`/`local.db`) écrits par le crawler dans `/data/catalog` et `/data/local` (lire la config/les défauts du crawler) et ajuster `CATALOG_DB`/`LOCAL_DB`. **Point empirique #1 (§16)** : si `mode=ro` échoue sur la base WAL vivante montée `:ro`, passer le montage à RW (retirer `:ro`) — `open_ro` garde `query_only` ; documenter le verdict dans `docs/reference/`.

- [ ] **Step 3 : runbook** — `docs/runbook-administration.md` : section « WebUI » (à quoi elle sert, lecture seule, `docker compose --profile observer up webui`, exposition derrière reverse proxy + auth/TLS délégués, variables d'env, où regarder : `/`, `/files`, `/node`).

- [ ] **Step 4 : smoke (optionnel, si le marqueur compose_integration couvre le webui)** — ajouter au test compose existant l'assertion que le service `webui` monte et `/health` répond. (Sinon, validation homelab manuelle documentée au runbook.)

- [ ] **Step 5 : Vérifier** — `docker compose -f bricks/compose.core.yaml --profile observer config` valide la syntaxe ; build local du webui réussit ; gate complet (4 paquets) vert.

- [ ] **Step 6 : Commit + tag**
```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(webui): packaging (Dockerfile + service compose observer/download) + runbook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
git tag -a v0.16.0-webui -m "WebUI catalogue lecture seule + extraction moteur matching"
```

---

## Self-Review (à exécuter après rédaction — fait)

**Couverture spec :** W-D1 (Task 1) · W-D2 lecture seule (Task 4) · W-D3 frontière + fixtures DDL (Tasks 4/7, Global Constraints) · W-D4 sans auth/poll (Task 13 runbook) · W-D5 pas d'ops (aucune tâche Prometheus — Grafana conservé en profil monitoring) · W-D6 cibles (Task 9) · W-D7 recalcul explication (Tasks 2/9/11) · W-D8 Jinja sans logique + garde + CSS vendoré (Task 10) · W-D9 minimisation (Tasks 5/7 : compteurs de sources, pas d'IP/user_hash, `source_observations` non lue) · W-D10 deps (Tasks 1/3). Routes §5 (Task 11). Read model §6 (Tasks 7/8). Packaging §10 (Task 13).

**Placeholders :** aucun « TODO/TBD ». Les points laissés à l'impl sont des FAITS à confirmer dans le code existant (signature `MatchingEngine`/`_resolved_by_target`, conversion `size_mb` dans `observation.py`, noms de fichiers DB on-disk) — explicitement fléchés « lire X », pas des trous de spec.

**Cohérence des types :** `open_ro` (Task 4) consommé par `CatalogReader`/`LocalReader` (Tasks 7/8) ; `MatchingEngine.explain` (Task 2) consommé par `MatchingExplainer` (Task 9) ; view-models de `domain/views.py` (Tasks 6/7/8) consommés par les routes (Task 11) ; `build_app` signature (Task 11) consommée par `__main__` (Task 12). Cohérents.

> **Risque #1 (lecture WAL vivante)** : tranché à l'impl (Task 4 code + Task 13 montage) et documenté `docs/reference/` — ne PAS le relitiger en cours de route.
