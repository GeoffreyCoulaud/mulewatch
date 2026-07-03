# Handoff — CI: poe gate as single source of truth + release grouped by arch

> Branch `chore/ci-poe-gate`, **rebase & merged into `main`** (single commit `3d0d9be`, PR #12).
> Inline spec (conversation, CI plumbing — no `docs/specs/` doc). **No tag** (CI tooling, not a
> versioned subsystem — same precedent as the prior CI handoff). Validated end-to-end on GitHub,
> **ghcr multi-arch push included**.

## Starting point

The CI from the previous handoff (`2026-07-03 - handoff - CI reusable parallel workflows`) worked
but was inefficient. Geoffrey flagged three things:

1. Unit tests ran as a **4-way matrix** (`package: [matching, crawler, verifier, webui]`), paying
   a full `uv sync --dev` per leg for tests that take seconds — the setup cost dwarfed the work.
2. In **release**, the per-`(package, arch)` `build` / `integration` / `build-push` / `merge` jobs
   should be **grouped by arch**; `build-push` was also questionably named.
3. Same grouping ask for the **PR** path.

Root cause was shared: the pipeline fanned out on `package × arch` (validate: 13 jobs; release: 22),
each job re-paying setup, and release built every image **twice** (cache-only in validate, then
push in `build-push`). The composite action carried a `write-cache` toggle to work around a
double-export cache race that the fan-out created.

## What was built

**Gate centralised in poethepoet (single source of truth).**
- `pyproject.toml` gains `[tool.poe.tasks]`; `poethepoet` added to the dev group. The gate is
  `uv run poe check` (= `lint-all` + `test`). The **pre-push hook and CI both call it** — the
  per-package command list is no longer duplicated across the hook and the workflow.
- Task names are **intention-based, kebab-case**, each with a `help` string (so bare `uv run poe`
  self-documents): `lint` `format-check` `type-check` `sql-lint` `template-check` → aggregate
  `lint-all`; `test` (each package's suite in its own process via `cwd`, so per-package coverage
  stays isolated); `check` = `lint-all` + `test`.
- **Fixer tasks**, documented in `CLAUDE.md`: `lint-fix` `format-fix` `sql-fix` → `fix`. `fix` uses
  `ignore_fail = "return_non_zero"` (applies every mechanical fix, then reports non-zero if
  anything unfixable remains). CLAUDE.md tells agents to run `uv run poe fix` before hand-fixing.

**Pipeline restructured (`.github/`).**
- `validate.yml` (reusable) gains a `push: boolean` input. `test` is **one job** (`uv run poe test`);
  `lint` runs `uv run poe lint-all`. The per-`(package, arch)` build/integration/push jobs collapse
  into a single **`image` job matrixed on arch only** (native runners, `needs: [lint, test]`): it
  builds the 3 images (loaded locally as `ci-<sha>`, **plus** pushed by digest when `push=true`, in
  a **single multi-exporter build**), runs the compose smoke against the local images, and uploads
  the digests.
- `release.yml`: the redundant `build-push` job is **gone** (folded into validate's `image` job);
  `validate` is called with `push: true`; `merge` now `needs: validate`. No consumable tag is
  published before the image passes integration — `merge` (the only tag-publisher) is downstream.
- `pr.yml`: calls `validate` with `push: false`.
- `.github/actions/docker-image`: **semantic interface** (`image` / `local-tag` / `push`),
  unconditional `cache-to`, **`write-cache` removed** — one build per scope/run eliminates the
  double-export race the old design worked around.
- `.github/actions/setup-uv-env`: gains a `save-cache` passthrough (see pitfall 3).

**Job count: PR 13 → 4, release 22 → 7.**

## Learned pitfalls (the real, costly ones)

1. **Reusable-workflow permissions are capped by the caller — exceeding = `startup_failure`.**
   The shared `image` job statically declares `packages: write` (it needs it to push on release).
   The PR caller granted only `contents: read`, so the called workflow requested *more* than granted
   → the whole PR run failed **at startup**, before any job. **Fix**: grant `packages: write` on
   `pr.yml`'s `validate` job too; nothing is ever pushed from a PR because the push steps are gated
   `if: inputs.push` (false there). **Any** static `packages: write` in a workflow shared by PR and
   release forces the PR caller to grant it. `actionlint` does **not** catch this (it's a
   server-side check).
2. **A single multi-exporter build needs BuildKit ≥ 0.13** — `type=docker` (local load for the
   integration step) **and** `type=image,push-by-digest,push` in one `buildx` invocation. This is
   what lets the exact image that integration tested be the one pushed, from one build (no
   double-build, no rebuild). Chosen over a two-build variant. Consequence accepted: the push
   happens *during* the build, so on a **failed** release integration an **untested digest blob**
   can transiently land in ghcr — but it is untagged and GC-able, and `merge` (the only
   tag-publisher) runs only after integration, so no consumable tag ever points at an untested image.
3. **Concurrent uv-cache saves emit a warning.** `lint` and `test` (both x86) racing to save the
   same `setup-uv` cache key → *"Unable to reserve cache … another job may be creating this cache."*
   **Fix**: `setup-uv-env` gained a `save-cache` input; **one writer per runner arch** — `lint` owns
   x86 (`save-cache` default true), `image (arm64)` owns arm64 (sole arm64 job); `test` and
   `image (amd64)` are restore-only. Every job still restores. Zero annotations after.
4. **ruff will not auto-fix F401 (unused import) in an `__init__.py`** — it treats those as
   re-exports. A plain `fix` sequence therefore *aborted* at `lint-fix` and skipped `format-fix` /
   `sql-fix`. **Fix**: `fix` uses `ignore_fail = "return_non_zero"`. (Not a gate concern — `check`
   correctly fails and fail-fasts on any error; verified by injecting lint/test/format errors.)
5. **`startup_failure` surfaces no useful annotation via the API** — `gh api …/check-runs` and
   `…/jobs` were empty; only the web UI hinted "workflow file issue". Diagnose by reasoning +
   `go run github.com/rhysd/actionlint/cmd/actionlint@latest` (catches schema errors, not
   cross-workflow permission relationships).

## Validated on real hardware (GitHub)

- **PR run** (`push=false`): 4 jobs green — `lint`, `test`, `image (amd64)`, `image (arm64)` with
  the compose smoke on **both** arches. **Zero annotations.**
- **Release run** on `main` (`push=true`): 7 jobs green — `validate` (`lint`, `test`, `image` ×2 with
  the **multi-exporter push-by-digest**) + `merge` ×3. **Zero annotations.**
- **ghcr**: `emule-indexer-{crawler,verifier,webui}` republished as **OCI image index amd64 + arm64**,
  tags `main` / `latest` / `sha`. Independently inspected `crawler:latest` — both platforms present
  (`unknown/unknown` entries = buildx provenance attestations, harmless, as the prior handoff noted).
- **Red→green locally**: injected a lint error, a failing test, and a formatting error → `poe check`
  / sub-tasks fail non-zero and fail-fast (the `test` sequence stops at the first failing package);
  the fixers repair the fixable ones. Working tree left clean each time.

## Not yet done / to decide

- **Branch protection**: the required-check **names changed again**. New set (PR): `validate / lint`,
  `validate / test`, `validate / image (amd64)`, `validate / image (arm64)`. The names listed in the
  previous handoff (`validate / build (crawler, amd64)`, `validate / integration (amd64)`, …) are now
  **stale**. Update required status checks accordingly.
- No image was **deployed to a real node** here (the smoke validates wiring, not a prod run).
- The accepted multi-exporter consequence (pitfall 2): an untested digest blob may transiently land
  in ghcr on a failed release integration. Never tagged; revisit only if that residual bothers us.

## Suggested next step

Update the branch-protection required checks to the new names, then resume the product thread (the
Keroro lost-media work) where the prior handoff left off.
