# Crawler MVP — Plan 1 : Fondations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Établir les fondations d'`emule-indexer` (toolchain, squelette Clean Architecture, CI, gate de coverage) et valider la chaîne TDD de bout en bout avec la première unité de domaine pure : `normalize()` + `tokenize()`.

**Architecture:** Projet Python en layout `src/`, architecture Clean/Hexagonal (le domaine pur ne dépend de rien). Tout l'outillage (lint, types, tests, coverage) est verrouillé dès le départ et imposé en CI. La normalisation Unicode (NFKD) est la première brique du futur moteur de matching.

**Tech Stack:** Python ≥ 3.12, `uv` (projet/paquets), `ruff` (lint+format), `mypy --strict`, `pytest` + `pytest-cov` (coverage branch, seuil 100 % imposé), GitHub Actions.

> **Référence spec :** `docs/superpowers/specs/2026-06-10-crawler-mvp-design.md` — §4 (couches), §8.1 (normalisation), §15 (stack), §16 (TDD + coverage). Ce plan couvre uniquement les fondations ; les plans 2 à 8 (cf. en-tête de la session) suivront.

---

## File Structure

- `pyproject.toml` — métadonnées projet + config `ruff`, `mypy`, `pytest`, `coverage`.
- `.python-version` — épingle la version Python pour `uv`.
- `.gitignore` — artefacts Python/outils.
- `src/emule_indexer/__init__.py` — racine du paquet.
- `src/emule_indexer/domain/__init__.py` — couche domaine (pure, sans I/O).
- `src/emule_indexer/domain/normalization.py` — `normalize()`, `tokenize()`.
- `tests/__init__.py` — paquet de tests.
- `tests/domain/__init__.py`
- `tests/domain/test_normalization.py` — tests de la normalisation.
- `.github/workflows/ci.yml` — pipeline : sync, lint, format-check, types, tests+coverage.
- `.githooks/pre-push` — hook pré-push : rejoue les checks de la CI.
- `scripts/setup-dev.sh` — active les hooks (`core.hooksPath`) + `uv sync`.
- `README.md` — présentation multi-audience (chercheur / curieux / développeur).

---

## Task 1: Scaffolding du projet + outillage

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `src/emule_indexer/__init__.py`
- Create: `src/emule_indexer/domain/__init__.py`

- [ ] **Step 1: Créer `.python-version`**

```
3.12
```

- [ ] **Step 2: Créer `.gitignore`**

```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage.xml
htmlcov/
dist/
*.db
*.db-wal
*.db-shm
```

- [ ] **Step 3: Créer `pyproject.toml`**

```toml
[project]
name = "emule-indexer"
version = "0.0.0"
description = "Surveillance eMule pour retrouver le lost media Keroro VF"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = [
  "pytest>=8",
  "pytest-cov>=5",
  "mypy>=1.10",
  "ruff>=0.5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/emule_indexer"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src", "tests"]

[tool.pytest.ini_options]
addopts = "--cov=emule_indexer --cov-report=term-missing --cov-fail-under=100 --strict-markers"
testpaths = ["tests"]

[tool.coverage.run]
branch = true
source = ["emule_indexer"]

[tool.coverage.report]
show_missing = true
fail_under = 100
exclude_also = ["if TYPE_CHECKING:"]
```

- [ ] **Step 4: Créer les `__init__.py` du paquet**

`src/emule_indexer/__init__.py` :
```python
"""emule-indexer — surveillance eMule pour le lost media Keroro VF."""
```

`src/emule_indexer/domain/__init__.py` :
```python
"""Couche domaine : logique pure, sans I/O."""
```

- [ ] **Step 5: Synchroniser l'environnement**

Run: `uv sync`
Expected: crée `.venv/`, installe les dépendances dev, installe le projet en éditable. Génère `uv.lock`.

- [ ] **Step 6: Vérifier l'import du paquet**

Run: `uv run python -c "import emule_indexer; print('ok')"`
Expected: affiche `ok`, code de sortie 0.

- [ ] **Step 7: Vérifier que lint + types passent sur le squelette**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: aucune erreur (ruff « All checks passed! », mypy « Success »). *(Si `ruff format --check` échoue, lancer `uv run ruff format .` puis recommencer.)*

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore src
git commit -m "chore: scaffold uv project with ruff/mypy/pytest/coverage gates"
```

---

## Task 2: `normalize()` en TDD

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/domain/__init__.py`
- Create: `tests/domain/test_normalization.py`
- Create: `src/emule_indexer/domain/normalization.py`

- [ ] **Step 1: Créer les `__init__.py` de tests**

`tests/__init__.py` : *(fichier vide)*
```python
```
`tests/domain/__init__.py` : *(fichier vide)*
```python
```

- [ ] **Step 2: Écrire le test qui échoue**

`tests/domain/test_normalization.py` :
```python
import pytest

from emule_indexer.domain.normalization import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Télétoon", "teletoon"),            # diacritiques retirés (NFKD)
        ("KERORO", "keroro"),                # minuscules
        ("café", "cafe"),                    # accent composé décomposé puis retiré
        ("N°062A", "n 062a"),                # ° non-alphanumérique -> espace
        ("« Les demoiselles »", "les demoiselles"),  # guillemets -> espaces
        ("a__b  c", "a b c"),                # ponctuation + espaces multiples compactés
        ("  trim  ", "trim"),                # trim des bords
        ("", ""),                            # chaîne vide
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected
```

- [ ] **Step 3: Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/domain/test_normalization.py -q`
Expected: FAIL — `ModuleNotFoundError` / `ImportError: cannot import name 'normalize'`.

- [ ] **Step 4: Écrire l'implémentation minimale**

`src/emule_indexer/domain/normalization.py` :
```python
"""Normalisation des chaînes pour le matching (cf. spec §8.1)."""

import unicodedata


def normalize(value: str) -> str:
    """Replie une chaîne pour le matching.

    NFKD (décomposition de compatibilité) -> suppression des diacritiques
    combinants -> minuscules -> non-alphanumériques convertis en espaces ->
    espaces compactés -> trim.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_marks.lower()
    cleaned = "".join(ch if ch.isalnum() else " " for ch in lowered)
    return " ".join(cleaned.split())
```

- [ ] **Step 5: Lancer le test pour vérifier qu'il passe (+ coverage)**

Run: `uv run pytest tests/domain/test_normalization.py -q`
Expected: PASS, et le gate coverage ne bloque pas pour ce module (toutes les branches de `normalize` sont exercées par les cas paramétrés).

- [ ] **Step 6: Vérifier types + lint**

Run: `uv run ruff check . && uv run mypy`
Expected: aucune erreur.

- [ ] **Step 7: Commit**

```bash
git add src/emule_indexer/domain/normalization.py tests
git commit -m "feat(domain): add normalize() with NFKD accent folding"
```

---

## Task 3: `tokenize()` en TDD

**Files:**
- Modify: `tests/domain/test_normalization.py` (ajout de tests)
- Modify: `src/emule_indexer/domain/normalization.py` (ajout de `tokenize`)

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter dans `tests/domain/test_normalization.py` :
```python
from emule_indexer.domain.normalization import tokenize


def test_tokenize_splits_normalized_string() -> None:
    assert tokenize("N°062A « Les demoiselles »") == ["n", "062a", "les", "demoiselles"]


def test_tokenize_empty_string_yields_no_tokens() -> None:
    assert tokenize("   ") == []
```
*(Garder l'import existant de `normalize` ; ajouter cet import de `tokenize` en haut du fichier avec les autres imports.)*

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/test_normalization.py -q`
Expected: FAIL — `ImportError: cannot import name 'tokenize'`.

- [ ] **Step 3: Implémenter `tokenize`**

Ajouter dans `src/emule_indexer/domain/normalization.py` :
```python
def tokenize(value: str) -> list[str]:
    """Tokens significatifs d'une chaîne, après normalisation."""
    return normalize(value).split()
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest -q`
Expected: PASS (tous les tests), coverage ≥ 100 % (gate `--cov-fail-under=100` satisfait).

- [ ] **Step 5: Vérifier types + lint**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: aucune erreur.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/normalization.py tests/domain/test_normalization.py
git commit -m "feat(domain): add tokenize()"
```

---

## Task 4: CI (GitHub Actions)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Créer le workflow**

`.github/workflows/ci.yml` :
```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run pytest
```

- [ ] **Step 2: Reproduire localement exactement ce que fait la CI**

Run: `uv sync --dev && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: chaque commande sort en code 0 ; `pytest` affiche les tests verts + coverage 100 %.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint, format-check, type-check, tests + coverage gate"
```

---

## Task 5: Hook de pré-push + installation des hooks

**Files:**
- Create: `.githooks/pre-push`
- Create: `scripts/setup-dev.sh`

- [ ] **Step 1: Créer le hook de pré-push**

`.githooks/pre-push` :
```bash
#!/usr/bin/env bash
# Pré-push : refuse le push si un check échoue (mêmes checks que la CI).
set -euo pipefail

echo "[pre-push] ruff check…";          uv run ruff check .
echo "[pre-push] ruff format --check…"; uv run ruff format --check .
echo "[pre-push] mypy…";                uv run mypy
echo "[pre-push] pytest…";              uv run pytest
echo "[pre-push] OK"
```

- [ ] **Step 2: Rendre le hook exécutable**

Run: `chmod +x .githooks/pre-push`

- [ ] **Step 3: Créer le script d'installation dev**

`scripts/setup-dev.sh` :
```bash
#!/usr/bin/env bash
# Setup dev : active les hooks Git du repo + installe l'environnement.
set -euo pipefail

git config core.hooksPath .githooks
uv sync --dev
echo "Environnement dev prêt. Hooks Git activés (core.hooksPath=.githooks)."
```

- [ ] **Step 4: Rendre le script exécutable**

Run: `chmod +x scripts/setup-dev.sh`

- [ ] **Step 5: Activer les hooks et installer l'environnement**

Run: `./scripts/setup-dev.sh`
Expected: règle `core.hooksPath` sur `.githooks`, `uv sync` réussit, message de fin affiché.

- [ ] **Step 6: Vérifier que le hook s'exécute et passe**

Run: `bash .githooks/pre-push`
Expected: exécute les 4 checks, tous verts, sort en code 0 et affiche `[pre-push] OK`.

- [ ] **Step 7: Vérifier que `core.hooksPath` est bien configuré**

Run: `git config --get core.hooksPath`
Expected: affiche `.githooks`.

- [ ] **Step 8: Commit**

```bash
git add .githooks/pre-push scripts/setup-dev.sh
git commit -m "chore: add pre-push hook running CI checks + dev setup script"
```

---

## Task 6: README multi-audience

**Files:**
- Create: `README.md`

- [ ] **Step 1: Écrire le README** *(fence à 4 backticks car le contenu contient des blocs ```bash```)*

`README.md` :
````markdown
# emule-indexer

Retrouver le *lost media* **Keroro mission Titar (VF)** en surveillant eMule en continu,
et cataloguer un maximum de métadonnées au passage.

## Pourquoi ce projet

Une grande partie du doublage français de *Keroro mission Titar* (diffusé sur Teletoon en 2008)
est perdue. Les épisodes réapparaissent **par intermittence** sur le réseau eMule, quand un
détenteur se connecte ; une recherche manuelle ponctuelle les rate presque toujours.
**emule-indexer** transforme ce hasard en **surveillance permanente et distribuée** : plusieurs
chercheurs font tourner un nœud, chacun cherche en continu, catalogue ce qu'il voit, et alerte
quand un épisode manquant apparaît.

> Éthique : le sujet du catalogue est **le fichier**, pas la personne. Pas de pistage ni de
> désanonymisation — uniquement retrouver des épisodes perdus.

## Pour les chercheurs (faire tourner un nœud)

Le mode **observer** ne télécharge rien : il cherche, catalogue et notifie (avec un lien
`ed2k://` pour récupérer un fichier d'un clic). Portable (Linux, macOS, Windows via Docker
Desktop), sans configuration réseau particulière.

> ⚙️ Le packaging `docker compose` arrive dans un incrément ultérieur (voir la feuille de route).
> Le projet en est aux **fondations** (voir « Pour les développeurs »).

## Pour les curieux (comment ça marche)

1. Le nœud parle le protocole eMule (eD2k + Kad) via un client aMule piloté en interne.
2. Il lance en continu des recherches dérivées d'une liste d'épisodes cibles.
3. Chaque résultat est **scoré** contre cette liste (titres, numéros, dates de diffusion…).
4. Selon la confiance : on catalogue, on notifie, ou (mode complet) on télécharge dans un
   environnement **isolé**, sans jamais re-partager ni exécuter le contenu.
5. Les catalogues de plusieurs chercheurs **fusionnent** sans conflit (chaque fichier est
   identifié par son empreinte de contenu).

## Pour les développeurs

- **Stack** : Python (`uv`), architecture Clean/Hexagonal, `mypy --strict`, `ruff`, `pytest`.
- **TDD strict** : les tests sont la spec ; aucun code de prod avant les tests ; **coverage 100 %
  imposé** (branch).

### Démarrer
```bash
git clone <repo> && cd emule-indexer
./scripts/setup-dev.sh   # active les hooks Git (core.hooksPath) + installe l'env (uv sync)
uv run pytest
```

> Les **hooks Git ne sont pas activés automatiquement au clone** (sécurité Git) : `setup-dev.sh`
> règle `core.hooksPath=.githooks`. Le hook **pré-push** rejoue les checks de la CI (ruff,
> format, mypy, pytest) — un push ne part pas si un check échoue.

### Conception
- Spec : [`docs/superpowers/specs/2026-06-10-crawler-mvp-design.md`](docs/superpowers/specs/2026-06-10-crawler-mvp-design.md)
- Plans d'implémentation : [`docs/superpowers/plans/`](docs/superpowers/plans/)

## Statut

🚧 En construction — fondations posées (toolchain, CI, normalisation). Feuille de route
détaillée dans la spec et les plans.
````

- [ ] **Step 2: Vérifier que le lien vers la spec est valide**

Run: `test -f docs/superpowers/specs/2026-06-10-crawler-mvp-design.md && echo "spec link ok"`
Expected: affiche `spec link ok`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add multi-audience README"
```

---

## Self-Review (rempli par l'auteur du plan)

- **Couverture de la spec (périmètre Plan 1)** : stack & outillage §15 → Task 1 ✓ ; coverage imposé §16 → `--cov-fail-under=100` (Task 1) ✓ ; normalisation NFKD §8.1 → Tasks 2-3 ✓ ; squelette Clean Archi §4 (domaine pur) → `domain/` ✓ ; TDD §16 → toutes les tâches en cycle test-d'abord ✓ ; **hooks pré-push (exigence utilisateur, mêmes checks que la CI, installés via `core.hooksPath`)** → Task 5 ✓ ; **README multi-audience** → Task 6 ✓. *(Hors périmètre, couvert par les plans 2-8 : ports/adapters, EC, DB, scheduler, download/verifier, observabilité, packaging.)*
- **Placeholders** : aucun « TBD/TODO » ; tout le code et toutes les commandes sont explicites. *(`<repo>` dans le README est volontairement l'URL de clone à substituer par l'utilisateur.)*
- **Cohérence des types** : `normalize(value: str) -> str` et `tokenize(value: str) -> list[str]` cohérents entre tâches et tests ; noms de modules/fonctions identiques partout. Les checks du hook pré-push (Task 5) sont identiques à ceux de la CI (Task 4).
