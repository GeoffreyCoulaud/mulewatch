# Handoff: git-driven versioning implemented

Branch `feat/git-driven-versioning`, full gate green (`uv run poe check`: ruff, format, mypy
strict, sqlfluff, templates, and all four package suites at 100% branch coverage: matching 239,
crawler 1001, verifier 177, vex_guards 73). Touches package config, two runtime entry points,
both Dockerfiles, and `validate.yml`, so it goes through a PR. This implements the approved spec
`docs/specs/2026-07-10-git-driven-versioning.md` (now marked IMPLEMENTED, with two revisions
recorded in its section 9).

## What this builds (five commits)

1. **`feat(build)` - the four members derive their version from the git tag** (hatch-vcs on every
   `packages/*/pyproject.toml`, `uv.lock` re-locked). Static `version = "..."` is gone; each
   declares `dynamic = ["version"]` + `[tool.hatch.version]` (`source = vcs`, `tag-pattern` on
   pure semver) + `[tool.hatch.version.raw-options]`. uv records the members as `(dynamic)` with
   no pinned version, so the lock does NOT churn per commit (verified).

2. **`feat(observability)` - startup version log on both services.** Crawler `run()` logs
   `mulewatch version <v>`; verifier `main()` logs `download-verifier version <v>`. Both read
   `importlib.metadata.version(...)` (the number baked into the wheel). TDD, one test each.

3. **`feat(deploy)` - the version is stamped into both images.** A `VERSION` build-arg feeds
   `SETUPTOOLS_SCM_PRETEND_VERSION` (hatch-vcs stamps the wheel with no `.git` in context) AND
   `LABEL org.opencontainers.image.version`. Validated on real `docker build` (see below).

4. **`ci(validate)` - compute the version and inject it.** `build-and-verify` gets
   `fetch-depth: 0`, a step computing `VERSION` via `uvx --quiet --with hatch-vcs hatch version`,
   and passes it to both `docker-image` calls. lint/test stay shallow.

5. **`docs(spec)` - the spec is updated** (IMPLEMENTED, section 9 records the two revisions).

## Learned pitfalls (the hard part was the config, not the plumbing)

- **`tag-pattern` + `fallback-version` alone silently gives `0.0.0`.** Two independent causes,
  both empirically verified against this repo with setuptools-scm 10.2.0 / vcs-versioning 2.2.2:
  1. **Root detection.** hatch-vcs passes `root = <member dir>`; the `.git` is at the repo root.
     setuptools-scm refuses to search parents by default -> "version missing" -> `fallback-version`
     swallows it into `0.0.0`. This is why passing `fallback_version` *looked like* it forced the
     fallback: derivation was failing at the root step. Fix: `raw-options.search_parent_directories
     = true`.
  2. **Tag selection.** `git describe` returns the nearest tag, which is ALWAYS a milestone
     `vX.Y.Z-<name>` here; the anchored `tag-pattern` cannot parse it and setuptools-scm raises.
     Fix: `raw-options.git_describe_command` with `--exclude '*-*'` so describe falls back to the
     last pure-semver tag (`v0.19.1` -> `0.19.2.devN+g<sha>`).
- **The injected `VERSION` must be valid PEP 440.** A `docker build --build-arg VERSION=9.9.9-test`
  fails with `InvalidVersion` inside the wheel build. Harmless in practice: `hatch version` always
  emits valid PEP 440 (`1.0.0` on a tag, `0.19.2.devN+g<sha>` on main). Use a clean value if you
  ever build by hand.
- **Only `build-and-verify` needs `fetch-depth: 0`.** `search_parent_directories` lets a shallow
  lint/test checkout still derive a throwaway `0.0.1.devN` without crashing `uv sync`, so those
  jobs (and `publish-manifest`, PR `vex-checks`) stay shallow.
- **The `build_info` Prometheus gauge was dropped** (operator decision 2026-07-12). The startup
  log + OCI label cover "which version is running"; the observability pipeline is untouched.

## Validated end-to-end (locally)

- `uv sync` stamps `0.19.2.devN+g<sha>` on all four members in sync; `importlib.metadata` agrees.
- Real `docker build --build-arg VERSION=9.9.9` on BOTH images: the OCI label reads `9.9.9` and
  the in-image `importlib.metadata.version(...)` reads `9.9.9` (crawler + its `catalog-matching`
  workspace dep, and the verifier). `SETUPTOOLS_SCM_PRETEND_VERSION` overrides git as designed.

## Shipped: merged + v1.0.0 released (2026-07-12)

Rebase-merged to `main`; both release runs green (main push + the `v1.0.0` tag push). Confirmed
on the published, signed, attested images:

- `:latest` (main) carries the OCI label `0.19.2.dev225+g7d8832d` on both arches.
- `:1.0.0` (tag) carries the OCI label `1.0.0` on both arches; image tag and label agree, clean
  `1.0.0` with no dev suffix. **v1.0.0 is the first git-driven release**, superseding the `v0.19.1`
  base.
- CI shallow lint/test/vex-checks jobs passed on real runners (shallow `uv sync` with dynamic
  versioning does not break), and both `build-and-verify` legs (amd64/arm64) built with the injected
  `VERSION`.

## Residual (minor)

- The SBOM/VEX gates ran green against the versioned images in both releases, so the SBOM cascade
  from the OCI label is in place; the attestation's `component.version` field was not downloaded and
  read directly (label presence is the direct cause). Verify by pulling the attestation only if a
  consumer ever needs the SBOM component version specifically.
