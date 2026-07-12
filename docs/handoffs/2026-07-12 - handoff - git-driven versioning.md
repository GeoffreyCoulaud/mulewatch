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

## Suggested next step

The deferred `v1.0.0` product tag is now meaningful. To ship it (spec 5.5): on `main`, gate green,
`git tag -a v1.0.0 -m "..."` then push. `release.yml` builds, tags the image `1.0.0`, signs and
attests. That first release supersedes the retroactive `v0.19.1` base.

## NOT validated against real hardware / CI

- **SBOM cascade** (spec 5.4/9.5): Syft reads `org.opencontainers.image.version` into the SBOM's
  top-level component. This runs only in `publish-manifest` against a pushed digest. Confirm on the
  first push that the attested CycloneDX/Syft SBOM carries the real version.
- **Release tag path**: on a real `vX.Y.Z` push, confirm the image tag `X.Y.Z` and the OCI label
  agree, and that `hatch version` at the tag yields the clean `X.Y.Z` (no dev suffix).
- **CI shallow lint/test**: derivation-on-shallow was validated via a local `--depth 1 --no-tags`
  clone (`hatch version` -> `0.0.1.devN`, no crash), not yet on an actual runner.
