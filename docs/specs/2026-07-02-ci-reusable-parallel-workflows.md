# Spec — Split CI into reusable workflows and parallelize the pipeline

- Date: 2026-07-02
- Status: approved
- Scope: `.github/` only (workflows + composite actions). No package/source/test change.
  The compose-smoke test already supports the prebuilt-image path (`IMAGE_TAG`) — see
  "What does NOT change".

## Problem

CI is a single `.github/workflows/ci.yml` with two jobs:

- `pipeline` — one monolithic job running **strictly sequentially**: setup → lint
  (ruff check, ruff format, mypy, sqlfluff, check_templates) → unit tests (matching,
  crawler, verifier, webui, fail-fast) → build the 3 images amd64 `--load` (gha cache)
  → integration (compose smoke consuming the `ci-<sha>` images).
- `publish` — `needs: pipeline`, gated `if:` on push to `main`/`v*` tags; builds all 3
  images **multi-arch amd64+arm64 in a single QEMU-emulated job** and pushes to ghcr.

Three problems, now that the repository is public:

1. **No parallelism.** Independent work (lint, unit tests, image builds) runs in series.
   The 4 package test suites run one after another; the 3 image builds run one after
   another; the whole `pipeline` job is a single critical path.
2. **One file, two concerns.** PR validation and `main`/tag publication live in the same
   file, with the publish job showing as a permanently "skipped" check on every PR.
3. **arm64 is emulated.** `publish` builds arm64 under QEMU on an amd64 runner — slow —
   and arm64 is never exercised on PRs, so an arm-only build break (e.g. an apt package
   missing on arm64, a native wheel that fails to compile) is only discovered *after*
   merge, when `main` goes red.

## Goals

- **Clarity**: PR checks show only validation; publication is a separate file with its
  `packages: write` permission isolated there.
- **Speed via parallelism**: run lint, unit tests, and image builds concurrently where the
  dependency graph allows; fan out the 4 test suites and the per-package/per-platform image
  builds into matrices.
- **Native multi-arch**: build amd64 and arm64 on their **native** GitHub-hosted runners
  (arm64 runners are free and unlimited for public repositories), replacing the single
  QEMU-emulated multi-arch job with a per-platform matrix that merges into a manifest list.
- **No duplication**: the validation pipeline has exactly one definition, reused by both the
  PR workflow and the publish workflow via `workflow_call`.
- **Gate image work behind checks**: no image is built until lint *and* unit tests are green;
  nothing is published until validation (including the compose smoke) is green.

## Non-goals

- Path-filtered / per-package-triggered CI (YAGNI; would complicate required-check config
  and the per-package 100 % coverage gate). All packages are validated on every run.
- Changing the gha cache strategy, the Dockerfiles, the smoke stack, or any test code.
- Registry-based build cache (`type=registry`). We keep `type=gha`, scoped per
  `(package, platform)`.

## Design

### File layout

```
.github/
  workflows/
    validate.yml     on: workflow_call            — the reusable validation pipeline
    pr.yml           on: pull_request             — calls validate.yml, nothing else
    release.yml      on: push [main, tags v*]      — calls validate.yml, then publishes
  actions/
    setup-uv-env/    (unchanged behavior; description translated to English)
    docker-image/    generalized to a raw `outputs:` passthrough (see below)
```

Two consumers of `validate.yml` (PR and release) are exactly what makes a `workflow_call`
reusable workflow — rather than three independent jobs — pay for itself: the pipeline is
defined once.

### `validate.yml` — the parallel pipeline (`on: workflow_call`)

Permissions: `contents: read` only.

DAG:

```
lint ──┐
       ├──▶ build [matrix: package × arch]  ──▶ integration [matrix: arch]
test ──┘        needs: [lint, test]                    needs: build
(matrix ×4)                                             (compose smoke on amd64 AND arm64)
fail-fast:false
```

**`lint`** — single job (root-scoped tools are fast; splitting them would only multiply the
`uv sync` cost). Steps: checkout → `setup-uv-env` → ruff check → ruff format --check → mypy
→ sqlfluff → check_templates. (Same 5 lint commands as today.)

**`test`** — matrix over the 4 packages `[matching, crawler, verifier, webui]`,
`fail-fast: false` so all 4 verdicts are reported in one run. Each leg: checkout →
`setup-uv-env` → `( cd packages/<pkg> && uv run pytest )`.

**`build`** — `needs: [lint, test]` (no image work until checks pass). An **orthogonal matrix**
`package × arch` — GitHub computes the 3×2 = 6-leg product; nothing is enumerated by hand. The
runner is **derived** from `arch` (`runs-on: ${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm'
|| 'ubuntu-latest' }}`), and `platform: linux/${{ matrix.arch }}` /
`cache-scope: <package>-<arch>` derive from it too. All legs `outputs: type=cacheonly`:

| dimension | values |
|---|---|
| `package` | `crawler`, `verifier`, `webui` |
| `arch` | `amd64` (→ `ubuntu-latest`), `arm64` (→ `ubuntu-24.04-arm`) |

- Every leg builds a single platform on its **native** runner with `outputs: type=cacheonly`
  and, since these are the cache **warmers**, `write-cache: true` →
  `cache-to: type=gha, scope=<package>-<arch>, mode=max`. No image leaves the leg — each is a
  "does it compile on this arch" check that warms the gha cache so both the smoke
  (`integration`) and `release.yml` build from a cache hit. arm64 is thus validated pre-merge.
- **Only the build legs export the cache.** Read-only consumers (`integration`,
  `release.build-push`) run `cache-from` only (`write-cache` stays `false`). Two jobs exporting
  `cache-to` to the **same** gha scope race and fail the export with `not_found`; letting a
  single writer per scope own it removes both the redundant export and that flake.
- **No tarballs, no artifacts**: the gha cache is the single transport (one storage mechanism
  instead of gha-cache + artifact store, and the heavy verifier image never transits the
  artifact store).
- All 6 legs run concurrently.

**`integration`** — `needs: build`, **matrix over `arch` ∈ {amd64, arm64}**, each leg on its
**native** runner (`ubuntu-latest` / `ubuntu-24.04-arm`). The smoke needs that arch's crawler +
verifier images in the local Docker daemon; it gets them by **rebuilding from the warm gha
cache** — a cache-hit assembly of a few seconds, not a full rebuild. Steps: checkout →
`setup-uv-env` → `setup-buildx-action` → `docker-image` for crawler (`platform: linux/<arch>`,
`outputs: type=docker` i.e. `--load`, tag `${IMAGE_PREFIX}-crawler:ci-${sha}`,
`cache-from: type=gha,scope=crawler-<arch>`) → same for verifier → run
`( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )` with
`IMAGE_TAG=ci-${sha}`. Each runner is single-arch native, so `docker compose` selects the
matching arch for the third-party `amuled` image (which is multi-arch, arm64 included) and for
the loaded crawler/verifier images. webui is not in the smoke stack, so it is never loaded
(its build leg alone validates it). The test consumes the loaded images without rebuilding (it
keys on `IMAGE_TAG`; see below).

Running the smoke on **both** arches (not just amd64) is a deliberate confidence upgrade: the
`build` legs only prove the images *compile* on arm64, whereas the arm64 `integration` leg
proves the *assembled stack actually runs* on arm64 (ffprobe/clamav in the verifier, the
crawler runtime, the compose wiring) — worthwhile since arm64 images are published. This
requires `uv sync --dev` on arm64; all native deps (`google-re2`, `rapidfuzz`, `ruff`, …) ship
`aarch64` wheels, so it resolves without compilation.

> **Redundancy note (option a).** For each arch, crawler+verifier are built in their matrix
> `build` leg *and* reassembled from that arch's cache in the `integration` leg. Accepted
> deliberately: the second pass is a cache-hit assembly of a few seconds, and a perfectly
> uniform build matrix is worth that. It is no more work than a tarball design, where
> `integration` would still do an equivalent `docker load` — we merely swap "download+load" for
> "pull-cache+load".

### `pr.yml` (`on: pull_request`)

```yaml
concurrency:
  group: pr-${{ github.ref }}
  cancel-in-progress: true          # superseded PR pushes cancel the stale run
permissions:
  contents: read
jobs:
  validate:
    uses: ./.github/workflows/validate.yml
```

### `release.yml` (`on: push` — `main` + `v*` tags)

Calls `validate.yml`, then a two-stage publish (per-platform push-by-digest → merge
manifest). No `cancel-in-progress` (never cancel an in-flight publish).

DAG:

```
validate (reusable) ──▶ build-push [matrix: package × arch, 6 jobs] ──▶ merge [matrix: package, 3 jobs]
```

**`build-push`** — orthogonal matrix `package × arch`, `needs: validate`, runner derived from
`arch` (same ternary as `build`; native, no QEMU). Each leg builds a single platform
(`linux/<arch>`) and pushes **by digest** (no tag):
`outputs: type=image,name=${IMAGE_PREFIX}-<pkg>,push-by-digest=true,name-canonical=true,push=true`,
`cache-from: type=gha,scope=<pkg>-<arch>` only (a cache-hit from validate on both arches;
`write-cache` stays false — validate's build legs already own that scope's export).
Each leg writes its digest to a per-`(pkg,arch)` artifact.

**`merge`** — matrix over `package`, `needs: build-push`. Downloads the 2 digests for the
package, computes tags with `docker/metadata-action` (`type=ref,event=branch`, `type=sha`,
`type=semver,pattern={{version}}`, `type=raw,value=latest,enable={{is_default_branch}}` — the
current tag set), and runs `docker buildx imagetools create` to assemble the multi-arch
manifest list carrying those tags. Permissions: `contents: read`, `packages: write`; logs in
to ghcr.

### `docker-image` composite action — generalization

Today the action juggles `push`/`load` booleans and always sets `load: ${{ push=='false' }}`.
Replace the boolean gymnastics with a **raw `outputs:` passthrough**, keeping `package`
(→ Dockerfile path), `platforms`, `tags` (optional), `cache-scope`, and a `write-cache` flag.
`cache-from: type=gha,scope=<scope>` is unconditional; `cache-to` is emitted **only when
`write-cache: true`** (default false) — so exactly one job owns each scope's export. The three
call-sites become:

- validate build (all 6 legs) → `outputs: type=cacheonly`, `write-cache: true` (the warmers)
- validate integration (crawler, verifier) → `outputs: type=docker` (i.e. `--load` into the
  daemon) + `tags: ${IMAGE_PREFIX}-<pkg>:ci-${sha}`; `write-cache` false (read-only)
- release build-push → `outputs: type=image,name=…,push-by-digest=true,name-canonical=true,push=true`;
  `write-cache` false (read-only)

(The `--load` mode does not disappear — it moves from the old monolithic build step into the
`integration` job, its sole consumer.) The `merge` step is a raw `run:` (`imagetools create`),
not the action.

### Cross-cutting

- **`IMAGE_PREFIX`** (`ghcr.io/geoffreycoulaud/emule-indexer`) is needed in `validate.yml`
  (build tags) and `release.yml` (build-push name + metadata). Define `env: IMAGE_PREFIX` at
  the top of each file (small, explicit duplication — preferred over a `workflow_call` input
  for a constant).
- **Permissions** stay least-privilege: `validate.yml` is `contents: read`; only
  `release.yml`'s publish jobs get `packages: write`.
- **Setup tax**: splitting into jobs re-runs checkout + `uv sync` per job/leg;
  `astral-sh/setup-uv`'s cache keeps that cheap. Accepted — the parallelism win dominates.
- **Action pinning**: every third-party action is pinned to a **full commit SHA** with a
  `# vX.Y.Z` trailing comment (supply-chain hardening + Dependabot-readable), each at its
  latest `node24` release — checkout `v7.0.0`, setup-uv `v8.2.0`, setup-buildx `v4.2.0`,
  build-push `v7.3.0`, login `v4.3.0`, metadata `v6.2.0`, upload-artifact `v7.0.1`,
  download-artifact `v8.0.1`. This also removes the Node-20 deprecation warnings and sidesteps
  the fact that `setup-uv` publishes no moving `v8` major tag. Local composite actions stay
  path-referenced (`./.github/actions/…`), pinned by the repo checkout itself.

## Behavior changes (call out for review)

1. **Unit tests are no longer fail-fast across packages.** `fail-fast: false` reports all 4
   package verdicts in one run (was: stop at the first red package). Intentional — better
   signal.
2. **arm64 is built on every PR** (cache-only). New cost (3 native-arm legs/PR), new benefit
   (arm-only breaks caught pre-merge; publish arm64 becomes a cache hit).
3. **The compose smoke runs on both arches.** `integration` is a matrix over {amd64, arm64} on
   native runners — it proves the *assembled stack runs* on arm64, not merely that the images
   compile. New cost (one native-arm `integration` leg + a `uv sync --dev` on arm64, all deps
   have `aarch64` wheels); new benefit (real runtime confidence for the published arm64 images).
4. **Required-check names change.** Checks are now `validate / lint`,
   `validate / test (crawler)`, `validate / build (crawler, amd64)`,
   `validate / integration (amd64)`, `validate / integration (arm64)`, etc. Branch-protection
   rules (if configured on the newly-public repo) must target the new names.
5. **Wall-clock trade-off.** Gating `build` behind `[lint, test]` means the image phase no
   longer overlaps lint/test (unlike a fully-overlapped layout). Accepted deliberately: don't
   spend build minutes on code that doesn't lint or test.

## What does NOT change

- **`packages/crawler/tests/integration/test_compose_smoke.py`** already supports the
  prebuilt path: when `IMAGE_TAG` is set it drops `--build` (`_BUILD_FLAGS = ()`) and
  `skipif(_USES_PREBUILT)` skips the standalone `build` sub-test. The integration job only
  sets `IMAGE_TAG=ci-<sha>` and loads the crawler+verifier images (buildx `--load` from the
  gha cache). Zero test changes.
- **`tests/smoke/compose.yaml`** (amuled + crawler + verifier; no webui) — unchanged.
- **Dockerfiles**, the per-package coverage gate, the gha cache scoping model, the published
  tag set (`metadata-action`) — unchanged.
- Each arch's crawler+verifier are built more than once (validate `build` leg → `integration`
  load → release push), each pass after the first being a gha cache hit — same spirit as
  today's pipeline/publish split, and the price of the uniform matrix (option a above).

## Validation

The only way to fully exercise this is on GitHub (matrices, native arm64 runners,
`workflow_call`, ghcr push). Plan: open the PR from the feature branch so `pr.yml` runs the
full `validate.yml` on real infrastructure (including the arm64 build legs and the compose
smoke running on **both** amd64 and arm64, each consuming crawler+verifier images assembled
from that arch's gha cache). Locally we can only lint-check the YAML (e.g. `actionlint` if
available) and re-read the DAG. `release.yml`'s publish path (push-by-digest + merge) is only
observable on a `main` push / tag — validate it right after merge and watch the first `main`
run.

## Open points

- Exact native-arm runner label: `ubuntu-24.04-arm` (GitHub-hosted, free for public repos).
  Confirm it resolves at run time; `ubuntu-22.04-arm` is the fallback label.
- `actionlint` is nice-to-have for local YAML validation but is not part of the gate.
