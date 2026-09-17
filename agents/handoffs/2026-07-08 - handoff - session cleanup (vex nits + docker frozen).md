# Handoff: short cleanup session (git prune + vex_guards nits + docker `--frozen`)

Date: 2026-07-08
Branch: `fix/vex-guards-sarif-and-frozen-deps`
Tag: none (this is cleanup/hardening, not a subsystem milestone)

## Why

A deliberately short session to clear three loose ends left open by the VEX-guardrails
milestone (`2026-07-08 - handoff - vex maintainability guardrails.md`), in the operator's
requested order 4 -> 3 -> 2:

4. Stale remote branches lingering in the local git cache.
3. The two Minor nits logged in that milestone's holistic review.
2. The tracked `uv sync --frozen` Dockerfile debt (needed empirical validation; possibly a wall).

## What was done

### 4. Remote-branch cache prune (no code)

The remote branches were already auto-deleted on PR merge; only the local remote-tracking
refs were stale. `git fetch --prune` dropped the 12 dead `origin/*` refs. Nothing left but
`origin/main`. No commit.

### 3. `vex_guards` hardening (commit `79a3e64`)

Two falsifiable-claim gaps closed, both TDD (red first, then minimal fix):

- **SARIF CLIs reject a missing `--output`.** `check_image_claims` and `check_stale_claims`
  in `--format sarif` without `--output` used to `TypeError` on `Path(None)`. They now call
  `parser.error("--output is required with --format sarif")` right after `parse_args`, i.e. a
  clean argparse usage error (exit 2, message on stderr). Our workflows always pass `--output`,
  so this only hardens the misuse path. One new test per CLI asserts `SystemExit(2)` + the
  `--output` message.
- **`source_scan` now flags the bare-name `import_module("x")` alias.** The scanner already saw
  `importlib.import_module("x")` (attribute call) and `__import__("x")` (name call); it now also
  sees `import_module("x")` via `from importlib import import_module` (a name call whose id is
  `import_module`). This closes a possible false negative on a `..._not_in_execute_path`
  source-family claim. `_dynamic_import_target`'s `is_import_module` became an OR of the attribute
  form and the bare-name form; every branch stays covered by the existing dynamic-import cases
  plus the new bare-name case in `IMPORT_FAILS` / `test_imported_modules_...`.

vex_guards is now 73 unit tests (was 70), still 100% branch.

### 2. Dockerfile deps layer -> `uv sync --frozen` (commit `1644c3d`)

Both prod Dockerfiles' deps layer used `uv sync --locked --no-install-workspace --package <pkg>`
and bind-mounted all four member `pyproject.toml`s (crawler/verifier/matching/vex_guards) only so
`--locked` could revalidate the lock. Switched to `uv sync --frozen`, which installs straight from
`uv.lock` without revalidation, and dropped the four member mounts (kept `uv.lock` + root
`pyproject.toml`). The second `uv sync --locked --no-editable` (after `COPY . /app`) is unchanged
and still revalidates the lock against every member. Also translated the verifier's leftover
French deps-layer comment to English (boy-scout, all-English rule).

## Key finding: the wall did not exist

The open question was whether `--frozen` still needs the member `pyproject.toml`s to resolve
`--package <member>`. It does not. Evidence, all reproduced locally (no CI needed):

- **Reproduced the Docker deps layer without Docker.** A dir holding ONLY `uv.lock` + the root
  `pyproject.toml` (no `packages/`), then `uv sync ... --package mulewatch --dry-run`:
  - `--locked` -> `error: Package mulewatch not found in workspace` (the `packages/*` glob finds
    no members). This is exactly why the mounts existed.
  - `--frozen --no-install-workspace` -> resolves fine (`Would install 21 packages` with
    `UV_NO_DEV=1`, matching the Dockerfile env). `--frozen` knows the members from the lock, so it
    never globs `packages/*`.
  - Same for `download-verifier` (10 packages).
- **Real `docker build --target builder` for both images** (amd64): the `--frozen` deps layer runs
  with only the two mounts, then `COPY . /app` + `uv sync --locked` builds the member(s) clean.
  Both builders exited 0.
- context7 confirmed the canonical uv workspace Docker pattern is exactly this: mount only
  `uv.lock` + root `pyproject.toml`, `uv sync --frozen --no-install-workspace`, then a later
  `--locked` after copying the members. "`--frozen` is necessary because uv cannot validate the
  lock without all member `pyproject.toml`s present."

## Learned pitfalls

- **`--locked` vs `--frozen` with `--package <member>` diverge on member presence.** `--locked`
  hard-errors "not found in workspace" if the member dir is absent (glob finds nothing);
  `--frozen` resolves the member from the lock and does not need the dir. This is the whole reason
  the switch removes the mounts.
- **Local `uv sync` reproduction needs the right interpreter.** The dev box defaults to Python
  3.13; the lock requires `>=3.14`, so an unqualified `uv sync` fails on the Python version, not
  on the workspace. Pass `--python /usr/bin/python3.14` (or the image's 3.14) to test the actual
  workspace behavior. Use `--dry-run` to avoid touching the real `.venv`.

## Not validated against real hardware

- The two `docker build --target builder` runs were **amd64 only** (local arch). arm64 is not
  built locally, but the change is pure uv resolution logic (arch-independent), and CI
  `build-and-verify` builds both arches on the PR. No runtime behavior changed (only which files
  the deps layer sees), so no functional hardware validation is owed.

## Suggested next step

The tooling/supply-chain debt is now clear. The next substantive lever on project VALUE is the
deferred **multi-target matching tuning on the real catalog** (odd decisions on some real files;
adjust `matcher.yml` / `targets.yml`), which the operator parked pending deliverable 2. Needs real
catalog data to iterate against.

## State

- Full gate green: `uv run poe check` (lint-all + matching 234 + crawler 990 + verifier 176 +
  vex_guards 73, all 100% branch). Exit 0.
- Two commits on the branch (`79a3e64` vex nits, `1644c3d` docker frozen) + this handoff.
- Not yet: PR opened, CI green, branch cleanup.
