# Pipeline CI consolidé — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer les deux workflows actuels par un pipeline consolidé qui fait tourner lint + unit + build + intégration sur **chaque PR**, publie les **3** images (crawler/verifier/webui) uniquement sur `main`/tags, le tout en composite actions et fail-fast.

**Architecture:** Un workflow `ci.yml`, deux jobs. `pipeline` (toutes triggers, `contents: read`) : un seul job séquentiel fail-fast. `publish` (`needs: pipeline`, `if push && main/tag`, seul à porter `packages: write`) : build-push multi-arch des 3 images, cache amd64 réutilisé depuis `pipeline` via `type=gha`. Réutilisation par 2 composite actions.

**Tech Stack:** GitHub Actions, composite actions, `docker/build-push-action@v6` (cache `type=gha`), `docker/metadata-action@v5`, `astral-sh/setup-uv@v5`, pytest.

## Global Constraints

- Owner d'image ghcr **en minuscules** : préfixe `ghcr.io/geoffreycoulaud/emule-indexer` (la compose le code en dur ; ne PAS utiliser `${{ github.repository }}` brut qui garde la casse `GeoffreyCoulaud`).
- **Jamais de publish sur PR** : job `publish` gated `if: github.event_name == 'push' && (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v'))`, et seul ce job a `packages: write`.
- **Fail-fast** : `pipeline` est un job unique séquentiel ; `publish` dépend de `needs: pipeline`.
- Le smoke d'intégration garde le `--project-directory <repo root>` introduit par la réorg.
- Les 3 images : `crawler`, `verifier`, `webui`. Dockerfiles : `packages/<package>/Dockerfile`, contexte de build = repo root (`.`).
- Tout sur `ubuntu-latest`. QEMU uniquement dans `publish`.

---

### Task 1: Composite action `setup-uv-env`

**Files:**
- Create: `.github/actions/setup-uv-env/action.yml`

**Interfaces:**
- Produces: une action locale appelable `uses: ./.github/actions/setup-uv-env` (aucun input/output). Suppose que `actions/checkout` a déjà tourné dans le job appelant.

- [ ] **Step 1: Créer le fichier**

```yaml
# .github/actions/setup-uv-env/action.yml
name: Setup uv env
description: Installe uv (cache activé) et synchronise l'environnement de dev (uv sync --dev).
runs:
  using: composite
  steps:
    - uses: astral-sh/setup-uv@v5
      with:
        enable-cache: true
    - run: uv sync --dev
      shell: bash
```

- [ ] **Step 2: Valider que le YAML parse**

Run: `python -c "import yaml; yaml.safe_load(open('.github/actions/setup-uv-env/action.yml'))"`
Expected: aucune sortie, exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/actions/setup-uv-env/action.yml
git commit -m "ci: composite action setup-uv-env (uv + sync)"
```

---

### Task 2: Composite action `docker-image`

**Files:**
- Create: `.github/actions/docker-image/action.yml`

**Interfaces:**
- Consumes: un builder buildx doit être disponible (le job appelant pose `docker/setup-buildx-action` ; `publish` pose aussi QEMU).
- Produces: action locale `uses: ./.github/actions/docker-image` avec inputs `package` (str), `push` (`'true'`/`'false'`), `platforms` (str), `tags` (str multiligne). Si `push=='false'` → build `--load` (image locale) ; si `push=='true'` → build-push. Cache `type=gha` scoppé par package.

- [ ] **Step 1: Créer le fichier**

```yaml
# .github/actions/docker-image/action.yml
name: Build (and optionally push) a package image
description: >
  Build l'image d'un package via buildx. Mono-arch --load (tests) ou multi-arch --push
  (publication) selon `push`. Cache partagé type=gha (scope par package).
inputs:
  package:
    description: Nom du package (crawler|verifier|webui).
    required: true
  push:
    description: "'true' = build-push multi-arch ; 'false' = build --load local."
    required: true
  platforms:
    description: Plateformes buildx (ex. linux/amd64 ou linux/amd64,linux/arm64).
    required: true
  tags:
    description: Tags d'image (multiligne).
    required: true
runs:
  using: composite
  steps:
    - uses: docker/build-push-action@v6
      with:
        context: .
        file: packages/${{ inputs.package }}/Dockerfile
        platforms: ${{ inputs.platforms }}
        load: ${{ inputs.push == 'false' }}
        push: ${{ inputs.push == 'true' }}
        tags: ${{ inputs.tags }}
        cache-from: type=gha,scope=${{ inputs.package }}
        cache-to: type=gha,mode=max,scope=${{ inputs.package }}
```

- [ ] **Step 2: Valider que le YAML parse**

Run: `python -c "import yaml; yaml.safe_load(open('.github/actions/docker-image/action.yml'))"`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/actions/docker-image/action.yml
git commit -m "ci: composite action docker-image (build --load | build-push, cache gha)"
```

---

### Task 3: Adapter `test_compose_smoke.py` pour consommer des images pré-buildées

Le smoke doit, **en CI**, utiliser les images déjà construites par l'étape build (via `IMAGE_TAG`) sans rebuild ; **en local** (pas d'`IMAGE_TAG`), garder le comportement actuel (`--build`). C'est piloté par la présence de la variable d'env `IMAGE_TAG`.

**Files:**
- Modify: `packages/crawler/tests/integration/test_compose_smoke.py`

**Interfaces:**
- Consumes: variable d'env `IMAGE_TAG` (posée par le job CI `pipeline`). Absente en local.
- Produces: le test propage `IMAGE_TAG` à `docker compose` et n'ajoute `--build` que si `IMAGE_TAG` est absent. `test_build_succeeds` est sauté quand `IMAGE_TAG` est présent.

- [ ] **Step 1: Ajouter les constantes de mode (après `_SMOKE`)**

Repérer le bloc (vers la ligne 46) :

```python
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SMOKE = _REPO_ROOT / "tests/smoke/compose.yaml"
```

Ajouter juste en dessous :

```python
# En CI, l'étape build pré-construit les images et passe IMAGE_TAG ; le smoke les consomme
# alors SANS rebuild. En local (IMAGE_TAG absent) on rebuild via compose, comme avant.
_IMAGE_TAG = os.environ.get("IMAGE_TAG")
_USES_PREBUILT = _IMAGE_TAG is not None
_BUILD_FLAGS: tuple[str, ...] = () if _USES_PREBUILT else ("--build",)
```

- [ ] **Step 2: Propager `IMAGE_TAG` dans l'env du subprocess (`_run`)**

Repérer dans `_run` :

```python
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **_ENV_STUB},
```

Remplacer par :

```python
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            **_ENV_STUB,
            **({"IMAGE_TAG": _IMAGE_TAG} if _IMAGE_TAG is not None else {}),
        },
```

- [ ] **Step 3: Rendre `--build` conditionnel dans les 3 appels `up`**

Il y a trois appels du type `_run(..., "up", "-d", "--build", ...)` (dans `test_download_verifier_healthy_and_crawler_up`, `test_observer_starts_without_verifier`, `test_download_without_verifier_fails_fast`). Pour chacun, remplacer le littéral `"--build"` par le dépliage `*_BUILD_FLAGS`. Exemples exacts :

```python
    result = _run("--profile", "download", "up", "-d", *_BUILD_FLAGS, files=files, timeout=900)
```
```python
    result = _run("--profile", "observer", "up", "-d", *_BUILD_FLAGS, files=files, timeout=900)
```
```python
    result = _run("up", "-d", *_BUILD_FLAGS, "amuled", "crawler", files=files, timeout=900)
```

- [ ] **Step 4: Sauter `test_build_succeeds` quand les images sont pré-buildées**

Repérer :

```python
def test_build_succeeds(project_files: tuple[Path, ...]) -> None:
    result = _run("--profile", "download", "build", files=project_files, timeout=900)
    assert result.returncode == 0, result.stderr
```

Remplacer par :

```python
@pytest.mark.skipif(_USES_PREBUILT, reason="images pré-buildées en CI (IMAGE_TAG) — rien à builder")
def test_build_succeeds(project_files: tuple[Path, ...]) -> None:
    result = _run("--profile", "download", "build", files=project_files, timeout=900)
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 5: Vérifier le gate (lint/format/mypy) sur le fichier modifié**

Run: `uv run ruff check packages/crawler/tests/integration/test_compose_smoke.py && uv run ruff format --check packages/crawler/tests/integration/test_compose_smoke.py && uv run mypy`
Expected: `All checks passed!` / `... already formatted` / `Success: no issues found`.

(Note : `compose_integration` est désélectionné du gate et exclu de la couverture — le comportement Docker se valide en Task 5, pas ici.)

- [ ] **Step 6: Commit**

```bash
git add packages/crawler/tests/integration/test_compose_smoke.py
git commit -m "test(smoke): consommer les images pré-buildées via IMAGE_TAG (sinon --build local)"
```

---

### Task 4: Réécrire `ci.yml` et supprimer `images.yml`

**Files:**
- Modify (réécriture complète): `.github/workflows/ci.yml`
- Delete: `.github/workflows/images.yml`

**Interfaces:**
- Consumes: les composites `./.github/actions/setup-uv-env` (Task 1) et `./.github/actions/docker-image` (Task 2) ; le smoke IMAGE_TAG-aware (Task 3).

- [ ] **Step 1: Réécrire `ci.yml` intégralement**

```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
  push:
    branches: [main]
    tags: ["v*"]

permissions:
  contents: read

env:
  IMAGE_PREFIX: ghcr.io/geoffreycoulaud/emule-indexer

jobs:
  pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-uv-env

      # --- lint ---
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run sqlfluff lint packages/crawler/src
      - run: uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates

      # --- unit (fail-fast : stoppe au 1er paquet rouge) ---
      - run: ( cd packages/matching && uv run pytest )
      - run: ( cd packages/crawler  && uv run pytest )
      - run: ( cd packages/verifier && uv run pytest )
      - run: ( cd packages/webui    && uv run pytest )

      # --- build (amd64, --load local, cache gha) : 3 images taguées ci-<sha> ---
      - uses: docker/setup-buildx-action@v3
      - uses: ./.github/actions/docker-image
        with:
          package: crawler
          push: 'false'
          platforms: linux/amd64
          tags: ${{ env.IMAGE_PREFIX }}-crawler:ci-${{ github.sha }}
      - uses: ./.github/actions/docker-image
        with:
          package: verifier
          push: 'false'
          platforms: linux/amd64
          tags: ${{ env.IMAGE_PREFIX }}-verifier:ci-${{ github.sha }}
      - uses: ./.github/actions/docker-image
        with:
          package: webui
          push: 'false'
          platforms: linux/amd64
          tags: ${{ env.IMAGE_PREFIX }}-webui:ci-${{ github.sha }}

      # --- integration (compose smoke consomme les images ci-<sha>) ---
      - run: ( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )
        env:
          IMAGE_TAG: ci-${{ github.sha }}

  publish:
    needs: pipeline
    if: ${{ github.event_name == 'push' && (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')) }}
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - id: meta-crawler
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.IMAGE_PREFIX }}-crawler
          tags: |
            type=ref,event=branch
            type=sha
            type=semver,pattern={{version}}
            type=raw,value=latest,enable={{is_default_branch}}
      - uses: ./.github/actions/docker-image
        with:
          package: crawler
          push: 'true'
          platforms: linux/amd64,linux/arm64
          tags: ${{ steps.meta-crawler.outputs.tags }}

      - id: meta-verifier
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.IMAGE_PREFIX }}-verifier
          tags: |
            type=ref,event=branch
            type=sha
            type=semver,pattern={{version}}
            type=raw,value=latest,enable={{is_default_branch}}
      - uses: ./.github/actions/docker-image
        with:
          package: verifier
          push: 'true'
          platforms: linux/amd64,linux/arm64
          tags: ${{ steps.meta-verifier.outputs.tags }}

      - id: meta-webui
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.IMAGE_PREFIX }}-webui
          tags: |
            type=ref,event=branch
            type=sha
            type=semver,pattern={{version}}
            type=raw,value=latest,enable={{is_default_branch}}
      - uses: ./.github/actions/docker-image
        with:
          package: webui
          push: 'true'
          platforms: linux/amd64,linux/arm64
          tags: ${{ steps.meta-webui.outputs.tags }}
```

- [ ] **Step 2: Supprimer l'ancien workflow images**

```bash
git rm .github/workflows/images.yml
```

- [ ] **Step 3: Valider que le workflow parse**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: exit 0.

(Optionnel si `actionlint` est installé : `actionlint` à la racine — doit ne rien remonter.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: pipeline consolidé (lint+unit+build+integration sur PR) + publish isolé des 3 images"
```

---

### Task 5: Validation bout-en-bout sur la PR

C'est **le** vrai test du pipeline (et accessoirement la validation Docker du recâblage compose de la réorg, qui tournera ici pour la première fois en PR).

**Files:** aucun (push + observation).

- [ ] **Step 1: Pousser la branche (met à jour PR #1)**

```bash
git push
```

- [ ] **Step 2: Observer le run CI de la PR**

Run: `gh run watch` (ou `gh pr checks`)
Expected :
- Job `pipeline` **vert** : lint → unit (4 paquets) → build des 3 images (amd64) → `compose_integration` passe (verifier healthy, crawler up, observer up, fail-fast exit≠0).
- Job `publish` **absent/skip** (événement `pull_request`).

- [ ] **Step 3 (si rouge) : diagnostiquer**

Points de rupture probables et où regarder :
- `compose_integration` qui ne trouve pas l'image → vérifier que les tags `ci-<sha>` du build matchent `${IMAGE_PREFIX}-<pkg>` **en minuscules** et que `IMAGE_TAG` est bien passé à l'étape integration.
- Résolution de chemins compose → le `--project-directory` du smoke doit être présent (réorg).
- buildx/cache → `docker/setup-buildx-action` doit précéder le build ; le scope `type=gha` est par package.

- [ ] **Step 4: Pas de commit** — la validation est l'observation du run. Une fois `pipeline` vert, le pipeline est prouvé sur PR ; `publish` ne s'exercera qu'au premier push `main`/tag après merge.

---

## Notes de validation post-merge (hors plan, pour mémoire)

`publish` n'est jamais exécuté avant un push sur `main`/tag. Au premier merge dans `main`, surveiller le job `publish` : login ghcr OK, les 3 images poussées multi-arch (amd64+arm64), `latest` sur la branche par défaut. Le cache `type=gha` doit faire que l'amd64 n'est pas reconstruit.
