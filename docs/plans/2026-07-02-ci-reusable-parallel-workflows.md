# CI Reusable + Parallelized Workflows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic `.github/workflows/ci.yml` with a reusable `validate.yml` (`workflow_call`) consumed by `pr.yml` and `release.yml`, parallelizing lint / test / build and publishing multi-arch images via native per-platform runners.

**Architecture:** One reusable validation pipeline (`lint ∥ test-matrix → build-matrix[pkg×arch] → integration`), gated so image builds wait for lint+test. The build matrix is uniform `type=cacheonly` (gha cache is the only transport); the compose smoke runs on both amd64 and arm64, each leg reassembling its own-arch images from that warm cache. `release.yml` publishes with the docker distribute pattern: per-platform `push-by-digest` on native runners, then a `merge` job assembling the manifest list with `metadata-action` tags. See spec `docs/specs/2026-07-02-ci-reusable-parallel-workflows.md`.

**Tech Stack:** GitHub Actions (reusable workflows, matrix strategy, native `ubuntu-24.04-arm` runners), Docker Buildx (`docker/build-push-action@v7`, `imagetools create`), `docker/metadata-action@v6`, existing composite actions.

## Global Constraints

- **Language: English** for all CI prose — step `name:`, `description:`, comments, commit messages (project decision 2026-07-02). Our conversational replies stay French; this constraint is about the files.
- **`IMAGE_PREFIX` = `ghcr.io/geoffreycoulaud/emule-indexer`** (verbatim, as in the current `ci.yml`). Image names are `${IMAGE_PREFIX}-<package>` for `<package>` ∈ {crawler, verifier, webui}.
- **Native runners, no QEMU:** amd64 legs → `ubuntu-latest`; arm64 legs → `ubuntu-24.04-arm`. Each leg builds exactly one platform.
- **gha cache scope** = `<package>-<arch>` with `<arch>` ∈ {amd64, arm64} (no slash — do not use the full `linux/amd64` in a scope).
- **Least privilege:** `validate.yml` is `contents: read`; only `release.yml`'s `build-push`/`merge` jobs get `packages: write`.
- **Conventional commits**, `chore(ci):` / `refactor(ci):` / `docs(ci):`. End commit messages with the `Co-Authored-By` trailer this repo uses.
- **No unit-test cycle exists for CI YAML.** Per-task local validation = a YAML parse (guaranteed) + `actionlint` if installed. The **authoritative** validation is the live PR run (Task 5) — matrices, native arm64 runners, `workflow_call`, and the compose smoke only exist on GitHub. `release.yml`'s publish path is only observable on a `main` push / tag (post-merge).
- **Action pinning (source of truth = `.github/`):** in the shipped files, every third-party action is pinned to a **full commit SHA** with a `# vX.Y.Z` comment, at its latest `node24` release — checkout `v7.0.0`, setup-uv `v8.2.0`, setup-buildx `v4.2.0`, build-push `v7.3.0`, login `v4.3.0`, metadata `v6.2.0`, upload-artifact `v7.0.1`, download-artifact `v8.0.1`. The YAML blocks below show readable version tags for legibility; resolve them to SHAs (`gh api repos/<repo>/commits/<tag> --jq .sha`) when writing the real files.

Local validation snippet used throughout (parses every workflow + action file):

```bash
uv run python - <<'PY'
import glob, sys, yaml
files = glob.glob(".github/workflows/*.yml") + glob.glob(".github/actions/*/action.yml")
bad = 0
for f in files:
    try:
        yaml.safe_load(open(f))
        print("ok  ", f)
    except yaml.YAMLError as e:
        bad = 1; print("FAIL", f, e)
sys.exit(bad)
PY
```

(If `actionlint` is available — `go install github.com/rhysd/actionlint/cmd/actionlint@latest` or a release binary — also run `actionlint` for expression/runner/shellcheck validation. It is not part of the repo gate.)

---

## File Structure

```
.github/
  actions/
    setup-uv-env/action.yml   MODIFY — translate description to English (behavior unchanged)
    docker-image/action.yml   REWRITE — raw `outputs:` passthrough + `platform`/`cache-scope` inputs + `digest` output
  workflows/
    ci.yml                    DELETE — superseded
    validate.yml              CREATE — reusable pipeline (on: workflow_call): lint, test, build, integration
    pr.yml                    CREATE — on: pull_request → uses validate.yml (concurrency: cancel-in-progress)
    release.yml               CREATE — on: push [main, v*] → uses validate.yml, then build-push (matrix) + merge (matrix)
```

Interfaces locked by this structure:

- **`docker-image` action** — inputs `package`, `platform`, `outputs`, `tags` (optional, default `''`), `cache-scope`; output `digest`. Consumed by `validate.yml` (build, integration) and `release.yml` (build-push).
- **`validate.yml`** — `on: workflow_call`, no inputs/secrets. Consumed by `pr.yml` and `release.yml` via `uses: ./.github/workflows/validate.yml`.

---

## Task 1: Composite actions — English `setup-uv-env` + generalized `docker-image`

**Files:**
- Modify: `.github/actions/setup-uv-env/action.yml`
- Rewrite: `.github/actions/docker-image/action.yml`

**Interfaces:**
- Consumes: nothing (leaf).
- Produces: the `docker-image` action interface — inputs `package` (str), `platform` (str, e.g. `linux/amd64`), `outputs` (str, buildx `--output`), `tags` (str, multiline, optional default `''`), `cache-scope` (str, e.g. `crawler-amd64`); output `digest` (str, `steps.build.outputs.digest`).

- [ ] **Step 1: Translate `setup-uv-env/action.yml` to English**

Full new content:

```yaml
name: Setup uv env
description: Install uv (cache enabled) and sync the dev environment (uv sync --dev).
runs:
  using: composite
  steps:
    - uses: astral-sh/setup-uv@v8.2.0
      with:
        enable-cache: true
    - run: uv sync --dev
      shell: bash
```

- [ ] **Step 2: Rewrite `docker-image/action.yml` as an `outputs:` passthrough**

Full new content:

```yaml
name: Build a package image
description: >
  Build one package image for one platform with buildx. A thin passthrough over
  docker/build-push-action: the caller picks the export via `outputs` (type=cacheonly,
  type=docker for a local --load, or type=image,push-by-digest for publication). The gha
  cache is scoped per (package, platform) via `cache-scope`.
inputs:
  package:
    description: Package name (crawler|verifier|webui).
    required: true
  platform:
    description: Single target platform (e.g. linux/amd64 or linux/arm64).
    required: true
  outputs:
    description: >
      buildx outputs, passed through verbatim. Examples: 'type=cacheonly';
      'type=docker'; 'type=image,name=<img>,push-by-digest=true,name-canonical=true,push=true'.
    required: true
  tags:
    description: Image tags (multiline). Optional — omit for cacheonly / push-by-digest.
    required: false
    default: ''
  cache-scope:
    description: gha cache scope, e.g. crawler-amd64.
    required: true
  write-cache:
    description: >
      If 'true', also export the gha cache (cache-to). Only the build legs set this; read-only
      consumers leave it false to avoid a double export to the same scope (which fails with
      "not_found").
    required: false
    default: 'false'
outputs:
  digest:
    description: Image digest (set for push/registry builds; empty otherwise).
    value: ${{ steps.build.outputs.digest }}
runs:
  using: composite
  steps:
    - id: build
      uses: docker/build-push-action@v7
      with:
        context: .
        file: packages/${{ inputs.package }}/Dockerfile
        platforms: ${{ inputs.platform }}
        outputs: ${{ inputs.outputs }}
        tags: ${{ inputs.tags }}
        cache-from: type=gha,scope=${{ inputs.cache-scope }}
        cache-to: ${{ inputs.write-cache == 'true' && format('type=gha,mode=max,scope={0}', inputs.cache-scope) || '' }}
```

- [ ] **Step 3: Validate YAML parses**

Run the Global-Constraints parse snippet. Expected: `ok` for both action files (workflow files not yet present is fine).
If `actionlint` is installed, run it too; expected: no errors on the action files.

- [ ] **Step 4: Commit**

```bash
git add .github/actions/setup-uv-env/action.yml .github/actions/docker-image/action.yml
git commit -m "refactor(ci): generalize docker-image action to an outputs passthrough

setup-uv-env description to English; docker-image now takes platform + outputs +
cache-scope and exposes the build digest, replacing the push/load boolean gymnastics.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `validate.yml` — the reusable parallel pipeline

**Files:**
- Create: `.github/workflows/validate.yml`

**Interfaces:**
- Consumes: `docker-image` action (Task 1); `setup-uv-env` action.
- Produces: reusable workflow `./.github/workflows/validate.yml` (`on: workflow_call`, no inputs/secrets) — consumed by Tasks 3 and 4.

- [ ] **Step 1: Create `validate.yml`**

Full content:

```yaml
name: Validate

on:
  workflow_call:

permissions:
  contents: read

env:
  IMAGE_PREFIX: ghcr.io/geoffreycoulaud/emule-indexer

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: ./.github/actions/setup-uv-env
      - name: ruff check
        run: uv run ruff check .
      - name: ruff format
        run: uv run ruff format --check .
      - name: mypy
        run: uv run mypy
      - name: sqlfluff (SQL migrations)
        run: uv run sqlfluff lint packages/crawler/src
      - name: webui templates guard
        run: uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates

  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        package: [matching, crawler, verifier, webui]
    steps:
      - uses: actions/checkout@v7
      - uses: ./.github/actions/setup-uv-env
      - name: Unit tests — ${{ matrix.package }}
        run: ( cd packages/${{ matrix.package }} && uv run pytest )

  build:
    needs: [lint, test]
    # arm64 → native arm runner; amd64 → ubuntu-latest
    runs-on: ${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-latest' }}
    strategy:
      fail-fast: false
      matrix:
        package: [crawler, verifier, webui]
        arch: [amd64, arm64]
    steps:
      - uses: actions/checkout@v7
      - uses: docker/setup-buildx-action@v4
      - name: Build (cache-only) — ${{ matrix.package }} ${{ matrix.arch }}
        uses: ./.github/actions/docker-image
        with:
          package: ${{ matrix.package }}
          platform: linux/${{ matrix.arch }}
          outputs: type=cacheonly
          cache-scope: ${{ matrix.package }}-${{ matrix.arch }}
          write-cache: true

  integration:
    needs: build
    runs-on: ${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-latest' }}
    strategy:
      fail-fast: false
      matrix:
        arch: [amd64, arm64]
    steps:
      - uses: actions/checkout@v7
      - uses: ./.github/actions/setup-uv-env
      - uses: docker/setup-buildx-action@v4
      - name: Load crawler image (${{ matrix.arch }}, from gha cache)
        uses: ./.github/actions/docker-image
        with:
          package: crawler
          platform: linux/${{ matrix.arch }}
          outputs: type=docker
          tags: ${{ env.IMAGE_PREFIX }}-crawler:ci-${{ github.sha }}
          cache-scope: crawler-${{ matrix.arch }}
      - name: Load verifier image (${{ matrix.arch }}, from gha cache)
        uses: ./.github/actions/docker-image
        with:
          package: verifier
          platform: linux/${{ matrix.arch }}
          outputs: type=docker
          tags: ${{ env.IMAGE_PREFIX }}-verifier:ci-${{ github.sha }}
          cache-scope: verifier-${{ matrix.arch }}
      - name: Integration — compose smoke stack (${{ matrix.arch }})
        run: ( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )
        env:
          IMAGE_TAG: ci-${{ github.sha }}
```

- [ ] **Step 2: Validate YAML parses**

Run the Global-Constraints parse snippet. Expected: `ok` for `validate.yml` (+ the two action files). `actionlint` if available.

- [ ] **Step 3: Reasoning check (no runner to test on locally)**

Confirm by re-reading: `build.needs = [lint, test]`; all 6 build legs are `type=cacheonly` with `cache-scope: <pkg>-<arch>`; `integration.needs = build` and is a matrix over `arch ∈ {amd64, arm64}` on native runners; each leg loads crawler+verifier for its own arch (`cache-scope: <pkg>-<arch>`) with `tags: …-<pkg>:ci-<sha>` and passes `IMAGE_TAG=ci-<sha>` to the smoke (matches `test_compose_smoke.py`'s `IMAGE_TAG` prebuilt path). webui is built but never loaded (not in the smoke stack).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/validate.yml
git commit -m "feat(ci): reusable validate.yml pipeline (lint | test | build → integration)

lint and test (4-package matrix, fail-fast:false) run in parallel; build is a
package×platform matrix (native amd64/arm64 runners, cache-only) gated behind
lint+test; integration reassembles crawler+verifier amd64 from the gha cache and
runs the compose smoke. on: workflow_call, contents: read.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `pr.yml` — pull-request caller

**Files:**
- Create: `.github/workflows/pr.yml`

**Interfaces:**
- Consumes: `validate.yml` (Task 2).
- Produces: PR-triggered workflow.

- [ ] **Step 1: Create `pr.yml`**

Full content:

```yaml
name: PR

on:
  pull_request:

permissions:
  contents: read

concurrency:
  group: pr-${{ github.ref }}
  cancel-in-progress: true

jobs:
  validate:
    uses: ./.github/workflows/validate.yml
```

- [ ] **Step 2: Validate YAML parses**

Run the parse snippet. Expected: `ok` for `pr.yml`. `actionlint` if available.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/pr.yml
git commit -m "feat(ci): pr.yml runs validate on pull_request (cancel superseded runs)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `release.yml` — publish caller + retire `ci.yml`

**Files:**
- Create: `.github/workflows/release.yml`
- Delete: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `validate.yml` (Task 2); `docker-image` action (Task 1); `docker/metadata-action@v6`; `docker/login-action@v4`.
- Produces: push-triggered publish; after this task the branch is fully migrated (no `ci.yml`, so a PR triggers only `pr.yml` — no double run).

- [ ] **Step 1: Create `release.yml`**

Full content:

```yaml
name: Release

on:
  push:
    branches: [main]
    tags: ["v*"]

permissions:
  contents: read

env:
  IMAGE_PREFIX: ghcr.io/geoffreycoulaud/emule-indexer

jobs:
  validate:
    uses: ./.github/workflows/validate.yml

  build-push:
    needs: validate
    # arm64 → native arm runner; amd64 → ubuntu-latest
    runs-on: ${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-latest' }}
    permissions:
      contents: read
      packages: write
    strategy:
      fail-fast: false
      matrix:
        package: [crawler, verifier, webui]
        arch: [amd64, arm64]
    steps:
      - uses: actions/checkout@v7
      - uses: docker/setup-buildx-action@v4
      - name: Log in to ghcr.io
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build and push by digest — ${{ matrix.package }} ${{ matrix.arch }}
        id: build
        uses: ./.github/actions/docker-image
        with:
          package: ${{ matrix.package }}
          platform: linux/${{ matrix.arch }}
          outputs: type=image,name=${{ env.IMAGE_PREFIX }}-${{ matrix.package }},push-by-digest=true,name-canonical=true,push=true
          cache-scope: ${{ matrix.package }}-${{ matrix.arch }}
      - name: Export digest
        run: |
          mkdir -p /tmp/digests
          digest="${{ steps.build.outputs.digest }}"
          touch "/tmp/digests/${digest#sha256:}"
      - name: Upload digest
        uses: actions/upload-artifact@v7
        with:
          name: digest-${{ matrix.package }}-${{ matrix.arch }}
          path: /tmp/digests/*
          if-no-files-found: error
          retention-days: 1

  merge:
    needs: build-push
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    strategy:
      fail-fast: false
      matrix:
        package: [crawler, verifier, webui]
    steps:
      - name: Download digests — ${{ matrix.package }}
        uses: actions/download-artifact@v8
        with:
          pattern: digest-${{ matrix.package }}-*
          path: /tmp/digests
          merge-multiple: true
      - uses: docker/setup-buildx-action@v4
      - name: Log in to ghcr.io
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Image tags — ${{ matrix.package }}
        id: meta
        uses: docker/metadata-action@v6
        with:
          images: ${{ env.IMAGE_PREFIX }}-${{ matrix.package }}
          tags: |
            type=ref,event=branch
            type=sha
            type=semver,pattern={{version}}
            type=raw,value=latest,enable={{is_default_branch}}
      - name: Create manifest list and push
        working-directory: /tmp/digests
        run: |
          docker buildx imagetools create \
            $(jq -cr '.tags | map("-t " + .) | join(" ")' <<< "$DOCKER_METADATA_OUTPUT_JSON") \
            $(printf '${{ env.IMAGE_PREFIX }}-${{ matrix.package }}@sha256:%s ' *)
      - name: Inspect
        run: docker buildx imagetools inspect ${{ env.IMAGE_PREFIX }}-${{ matrix.package }}:${{ steps.meta.outputs.version }}
```

- [ ] **Step 2: Delete `ci.yml`**

```bash
git rm .github/workflows/ci.yml
```

- [ ] **Step 3: Validate YAML parses**

Run the parse snippet. Expected: `ok` for `validate.yml`, `pr.yml`, `release.yml`, both actions; `ci.yml` gone. `actionlint` if available.

- [ ] **Step 4: Reasoning check**

Confirm: `build-push.needs = validate` and `merge.needs = build-push`; build-push legs are single-platform on native runners (no `setup-qemu`), push **by digest** (no `tags:`), name via `outputs` `name=…`; each leg uploads its digest as `digest-<pkg>-<arch>`; `merge` downloads `digest-<pkg>-*` (merge-multiple), computes tags with `metadata-action`, and `imagetools create`s the manifest. Tag set matches the old `publish` job verbatim.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "feat(ci): release.yml publishes multi-arch via per-platform digests + merge

Replaces the QEMU multi-arch publish job: build-push is a package×platform matrix
pushing by digest on native runners, merge assembles the manifest list with
metadata-action tags. Retires the monolithic ci.yml.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Live validation on GitHub (authoritative)

**Files:** none (CI runtime).

**Interfaces:**
- Consumes: everything above, on real infrastructure.
- Produces: a green `pr.yml` run; confidence to merge.

- [ ] **Step 1: Push the branch and open a PR**

```bash
git push -u origin refactor/ci-reusable-parallel-workflows
gh pr create --fill --base main
```

- [ ] **Step 2: Watch `validate` run via `pr.yml`**

```bash
gh pr checks --watch
```

Expected checks (new names — note for branch protection): `validate / lint`,
`validate / test (matching|crawler|verifier|webui)`,
`validate / build (crawler|verifier|webui, amd64|arm64)`, `validate / integration`.
All green. In particular confirm: the 6 arm64/amd64 build legs succeed on their native
runners, and `integration` loads crawler+verifier from cache and the compose smoke passes.

- [ ] **Step 3: Fix red checks and iterate**

If a leg is red, read its log (`gh run view --log-failed`), fix the YAML/action, commit, push; the concurrency group cancels the stale run and a fresh one starts. Repeat until green. Likely first-run culprits to check: the `ubuntu-24.04-arm` runner label resolving, the `imagetools`/`jq` line in `merge` (only exercised post-merge), `type=docker` load producing the `ci-<sha>` tag the smoke expects.

- [ ] **Step 4: Note on `release.yml`**

`release.yml`'s `build-push`/`merge` path only runs on push to `main` / a `v*` tag — it cannot run on the PR. After merge, watch the first `main` run (`gh run watch`) and confirm the 3 multi-arch manifests appear in ghcr with `latest` + `sha` tags. This is called out in the spec's Validation section; do not block the PR on it.

---

## Self-Review

**1. Spec coverage:**
- 3-file layout (validate/pr/release) → Tasks 2, 3, 4. ✓
- Reusable `validate.yml` `workflow_call`, `contents: read` → Task 2. ✓
- lint single job; test matrix ×4 `fail-fast:false` → Task 2. ✓
- build matrix pkg×arch, `needs:[lint,test]`, uniform `type=cacheonly`, scope `<pkg>-<arch>`, native runners → Task 2. ✓
- integration reassembles crawler+verifier amd64 from cache (`type=docker` load), `IMAGE_TAG=ci-<sha>`, webui excluded → Task 2. ✓
- pr.yml + concurrency cancel → Task 3. ✓
- release.yml: build-push by digest (native, no QEMU) + merge manifest with the verbatim tag set; `packages: write` isolated → Task 4. ✓
- docker-image generalized to `outputs:` passthrough, `--load` migrated to integration → Task 1. ✓
- English CI prose (setup-uv-env description, all step names) → Tasks 1–4. ✓
- ci.yml retired, no double-run → Task 4. ✓
- Behavior-change call-outs (fail-fast, arm-on-PR, check-name change) → surfaced in Task 5 Step 2. ✓
- Live validation, release path deferred post-merge → Task 5. ✓

**2. Placeholder scan:** No TBD/TODO; every file is shown in full; commands are concrete. ✓

**3. Type/name consistency:** `docker-image` inputs (`package`, `platform`, `outputs`, `tags`, `cache-scope`) and output (`digest`) are identical across Task 1 (definition) and Tasks 2/4 (call-sites). Cache scope form `<pkg>-<arch>` is consistent everywhere (build legs, integration `crawler-amd64`/`verifier-amd64`, build-push). `IMAGE_PREFIX` identical in validate.yml and release.yml. ✓

No gaps found.
