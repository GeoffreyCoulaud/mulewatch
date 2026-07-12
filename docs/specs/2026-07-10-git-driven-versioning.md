# Git-driven versioning: one pushed tag, every artifact in sync

- Date: 2026-07-10
- Status: IMPLEMENTED (operator sign-off 2026-07-10; built 2026-07-12 on `feat/git-driven-versioning`). Two revisions during implementation, recorded in section 9: the Prometheus `build_info` gauge was dropped, and section 5.2's config proved incomplete (setuptools-scm needs `search_parent_directories` + a milestone-tag-excluding describe command).
- Scope: how the product version is defined, derived, built into images, surfaced at runtime, and where it deliberately does not go
- Related: `.github/workflows/{validate,release}.yml`, `.github/actions/docker-image/action.yml`, every `packages/*/pyproject.toml`, `packages/*/Dockerfile`, `security/*.vex.openvex.json`

## 1. Problem: the version is invented in four places and reaches nothing

Today nothing ties "the version" to a single fact. The concrete state:

1. **Static, meaningless package versions.** Every member pins a literal: `mulewatch`, `catalog-matching`, `download-verifier` all say `version = "0.0.0"`; `vex-guards` says `0.1.0`. They never change and mean nothing.
2. **No product-tag convention.** Milestone tags are annotated `vX.Y.Z-<name>` (e.g. `v0.32.0-webui-files-layout`), local and unpushed, one per subsystem. Exactly one pure-semver tag has ever been pushed (`v0.19.1`). There is no single "this is release X.Y.Z" tag.
3. **The image tag is the only thing that tracks a version, and only on a tag push.** `docker/metadata-action` computes `type=semver,pattern={{version}}`, so pushing tag `vX.Y.Z` tags the image `X.Y.Z`. On a `main` push the image "version" is the branch name.
4. **No OCI version label.** The image build goes through `./.github/actions/docker-image`, which forwards `build-args` but sets no `labels`. The final multi-arch manifest is assembled with `docker buildx imagetools create` using tags only. So `org.opencontainers.image.version` is never stamped on the image (and, in cascade, the SBOM's main component has no version).
5. **No runtime version.** Neither `python -m mulewatch` nor the verifier logs anything about its version, and even reading `importlib.metadata.version(...)` would return the static `0.0.0` baked into the wheel.

The operator's requirement: **git tags determine the version, everywhere, in sync.**

## 2. Inventory: every surface a version could touch

Before the mechanism, the complete list of places a product version appears, could appear, or deliberately does not. This is the answer to "did we miss anywhere?".

| Surface | Today | Under this spec |
|---|---|---|
| 4x `packages/*/pyproject.toml` | static `0.0.0` / `0.1.0` | dynamic, derived from the tag (hatch-vcs) |
| Installed wheel metadata (`importlib.metadata`) | `0.0.0` | the injected version (build-arg) |
| Image tag | `X.Y.Z` on a tag push (`type=semver`) | unchanged, already correct |
| OCI label `org.opencontainers.image.version` | absent (the build action sets no labels) | stamped from the `VERSION` build-arg |
| SBOM (CycloneDX + Syft) main component version | derived from the image, so empty | inherits the OCI label (cascade, no extra work) |
| Crawler runtime | logs nothing | startup log line, `build_info` metric |
| Verifier runtime | logs nothing | startup log line |
| Prometheus `*_build_info{version}` | none | proposed new surface (Grafana-facing) |
| deploy compose `IMAGE_TAG` | operator picks the tag | consumes the image tag `X.Y.Z` (unchanged) |
| `security/*.vex.openvex.json` product id | non-versioned purl, on purpose | stays non-versioned (see 5.6) |
| VEX document `"version": N` | manual doc-revision counter | stays manual (OpenVEX semantics, not the product version) |
| git tags `vX.Y.Z-<name>` | local subsystem milestones | unchanged, excluded from derivation |

Two of these routinely cause confusion and are settled explicitly below: the SBOM (cascades from the label, 5.4) and the VEX (deliberately out, 5.6).

## 3. Goals and non-goals

**Goals**

- One source of truth: a single pushed, pure-semver git tag `vX.Y.Z`.
- The four packages derive their version from that tag (same tag, same version).
- The published image carries the version as its tag *and* as an OCI label (which in turn fixes the SBOM).
- Both running services report their version.
- CI and local builds compute the version the same way (no divergence).

**Non-goals**

- No per-package independent versioning. One product, one number.
- No change to the subsystem milestone tags `vX.Y.Z-<name>`: they stay local, unpushed, and excluded from version derivation. They remain a human bookkeeping device, not a version input.
- No versioning of the VEX products (see 5.6).
- Not implemented in this iteration: this spec is written now, built later.

## 4. Decision (summary)

1. **Source of truth:** a pushed, pure-semver, anchored tag `vX.Y.Z` (no suffix). Distinct from the `vX.Y.Z-<name>` subsystem milestones.
2. **Derivation:** `hatch-vcs` on all four packages. `dynamic = ["version"]`, `source = "vcs"`, a `tag-pattern` anchored on pure semver so the suffixed milestone tags are ignored, and a `fallback-version` for tagless builds.
3. **Docker build (no `.git` in context):** compute the version on the CI runner (where `.git` exists), inject it as a single `VERSION` build-arg. The Dockerfile feeds that one value into both `SETUPTOOLS_SCM_PRETEND_VERSION` (so hatch-vcs stamps the installed wheel without `.git`) and `LABEL org.opencontainers.image.version`.
4. **Runtime:** both services log their version at startup (from installed metadata), plus a `build_info` Prometheus gauge as the Grafana-facing surface. No version field on `/health` (decided 2026-07-10).
5. **Release ritual:** to ship `vX.Y.Z`, push a pure-semver annotated tag `vX.Y.Z` on `main`. `release.yml` (already triggered by `tags: v*`) builds, tags, signs the image at `X.Y.Z`.

## 5. Design

### 5.1 The tag is the fact

A release is a pushed annotated tag matching `^v\d+\.\d+\.\d+$` (optionally a PEP 440 pre-release later, out of scope now). "Pushed" and "pure semver" together separate it cleanly from the local `vX.Y.Z-<name>` milestones, which carry a `-<name>` suffix and never leave the machine.

Note on the existing `v0.19.1`: it is already pushed and pure-semver, so derivation will treat it as the current base. On `main` today `hatch version` would therefore report something like `0.19.2.devN+g<sha>`. That is correct and harmless; the first real release under this regime (expected `v1.0.0`) supersedes it.

### 5.2 Derivation with hatch-vcs

Each `packages/*/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
# drop the static `version = "..."`, declare it dynamic
dynamic = ["version"]

[tool.hatch.version]
source = "vcs"
tag-pattern = '^v(?P<version>\d+\.\d+\.\d+)$'
fallback-version = "0.0.0"

[tool.hatch.version.raw-options]
# The .git is at the shared repo root, not in this member dir: without this, setuptools-scm
# refuses to look in parent directories and reports "version missing".
search_parent_directories = true
# The milestone tags `vX.Y.Z-<name>` are the NEAREST tags on every commit; the default git
# describe would return one and setuptools-scm would fail to parse it. Excluding dashed tags
# makes describe fall back to the last pure-semver tag.
git_describe_command = [
    "git", "describe", "--dirty", "--tags", "--long",
    "--match", "v[0-9]*.[0-9]*.[0-9]*", "--exclude", "*-*",
]
```

> Note (2026-07-12, see section 9.2): the `raw-options` block above is NOT in the original
> spec draft. It was found necessary during implementation. `tag-pattern` alone is insufficient
> because setuptools-scm resolves the member `root` to the package dir (no `.git` there) and,
> even once it finds the root, `git describe` returns the nearest milestone tag `vX.Y.Z-<name>`
> which the anchored pattern cannot parse. Both are empirically verified.

The git tag lives at the repository root and is shared by all members, so a single `vX.Y.Z` gives the four packages the same version. `vex-guards` is dev/CI-only and never shipped, but it follows the same tag for uniformity ("everywhere, in sync").

Compatibility with uv: the version plugin lives on the hatchling build backend, orthogonal to uv as the workspace manager. `uv sync` / `uv build` honor the declared backend, so hatch-vcs works unchanged inside the virtual workspace.

### 5.3 The Docker build has no `.git`: compute on the runner, inject one value

`.dockerignore` excludes `.git/` (and `.github/`, `docs/`) from the build context. hatch-vcs cannot read history inside the build. The `.git` *does* exist on the CI runner. So:

1. **Runner** (CI, `fetch-depth: 0` to fetch tags): compute the version once, e.g. `VERSION=$(uvx hatch version)` in a checkout that has the tags. This applies the exact same hatch-vcs logic a local build would, so CI and local agree.
2. **Inject** via the existing `build-args` plumbing of `./.github/actions/docker-image` (the verifier already passes a build-arg, so the seam exists): `build-args: VERSION=${VERSION}`.
3. **Dockerfile** consumes one arg, uses it twice:

```dockerfile
ARG VERSION=0.0.0
# hatch-vcs / setuptools-scm reads this instead of .git during `uv sync`
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}
# ... existing builder steps (uv sync) ...
LABEL org.opencontainers.image.version=${VERSION}
```

One injected value feeds both the installed wheel's metadata (runtime) and the image label (supply chain). `SETUPTOOLS_SCM_PRETEND_VERSION` is global, so it also covers workspace dependencies pulled into the same `uv sync` (e.g. the crawler pulling `catalog-matching`): they inherit the same version, which is what "in sync" means. The per-dist form (`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>`) is the fallback if we ever need packages to diverge (we do not).

### 5.4 Surfacing the version

The same injected version reaches every consumer-visible surface:

- **Image label (supply chain).** `org.opencontainers.image.version`, stamped from the `VERSION` build-arg above, on BOTH images (crawler and verifier Dockerfiles). Complements the existing `type=semver` image tag rather than replacing it.
- **SBOM (cascade, no extra work).** The CycloneDX and Syft SBOMs are generated from the image by digest. Syft reads the main component's version from `org.opencontainers.image.version`, so stamping the label makes the SBOM's top-level component carry the real version too (today it is empty because the label is absent). To confirm at implementation time; it needs no separate action.
- **Runtime log, both services.** At startup each service logs its version from installed metadata: the crawler `version("mulewatch")` in `python -m mulewatch`, the verifier `version("download-verifier")` in `app.py`. The number reported is exactly the one baked into the wheel by 5.3.
- **Prometheus `build_info` ~~(Grafana-facing)~~ — DROPPED 2026-07-12 (see section 9.1).** The original design added a static gauge `mulewatch_build_info{version="X.Y.Z"} 1` set once at boot (the metrics-side counterpart to the startup log). The operator decided it was not worth the surface: the startup log plus the OCI label cover "which version is running". No `build_info` gauge is emitted by either service. The startup log below is the sole runtime version surface.
- **No version field on `/health`.** Considered and rejected (decided 2026-07-10): the startup log plus `build_info` cover operability; a `/health` field is redundant surface to maintain.

### 5.5 Release ritual

To ship `vX.Y.Z`:

1. On `main`, gate green, create an annotated pure-semver tag: `git tag -a vX.Y.Z -m "..."`.
2. Push the tag. `release.yml` (`on: push: tags: ["v*"]`) runs: build per arch, compute `VERSION` from the tag, inject it, assemble the manifest, tag the image `X.Y.Z`, sign and attest.
3. The subsystem milestone tags `vX.Y.Z-<name>` keep their current role, unchanged and unpushed.

### 5.6 Where the version deliberately does NOT go: the VEX

The two `security/*.vex.openvex.json` documents look like they might carry a version. They must not track the product version:

- Their `"version": 1` is the OpenVEX **document revision counter** (OpenVEX spec semantics), bumped by hand when a statement changes. It is unrelated to the product version and stays a manual counter.
- Their product is identified by a **non-versioned purl**, `pkg:oci/mulewatch-crawler` (and `-verifier`), with no `@tag` or `@sha256`. This is deliberate: every claim is structural and version-independent ("the app never imports tarfile", "busybox wget is never invoked"), so the VEX holds for all versions. Pinning a version in the purl would force re-emitting and re-signing the VEX on every release, and re-running the honesty gates, for zero gain in truth. OpenVEX explicitly allows non-versioned products for exactly this case.

Consequence for the build: stamping `org.opencontainers.image.version` on the image does NOT break the VEX honesty gate (`check_image_claims`), which matches the product by the non-versioned purl, not by a pinned version.

## 6. Tooling choice (decision to confirm): hatch-vcs over uv-dynamic-versioning

Both are hatchling plugins and both work in this workspace.

| | hatch-vcs | uv-dynamic-versioning |
|---|---|---|
| Backend | setuptools-scm (mature, ubiquitous) | dunamai |
| Override without `.git` | `SETUPTOOLS_SCM_PRETEND_VERSION` (standard, widely documented) | `UV_DYNAMIC_VERSIONING_BYPASS` / patch `fallback-version` |
| Importable version file | built-in `version-file` build hook | not offered as cleanly |
| Reputation / benchmark (Context7) | High / 96.7 | High / 65.8 |

**Recommendation: hatch-vcs.** Rationale: setuptools-scm is the most battle-tested option, `SETUPTOOLS_SCM_PRETEND_VERSION` is the canonical "no `.git`" escape hatch that 5.3 relies on, and the `version-file` hook is there if we ever want an importable constant. uv-dynamic-versioning's main draw is the uv name affinity, but the plugin runs at the hatchling layer regardless, so that affinity buys nothing here. This is the one point in the spec that is a genuine either/or; flagged for the operator to confirm.

## 7. Risks and accepted limits

- **Non-release builds report a dev version.** `main` and local builds have no product tag at HEAD, so they resolve to `X.Y.Z.devN+g<sha>` (or `fallback-version` in a truly tagless checkout). This is intended: only a pushed tag yields a clean release number.
- **A shallow CI checkout would break derivation.** Any job that computes the version needs `fetch-depth: 0` (tags fetched). This is a checklist item for the implementation, not a design risk.
- **The static `0.0.0` disappears.** Anything that currently relies on the literal `0.0.0` (there should be nothing) must move to the dynamic value. To be verified during implementation.
- **`v0.19.1` is retroactively the base.** Acceptable (see 5.1); the first `v1.0.0` release supersedes it.

## 8. Decisions (resolved 2026-07-10)

1. **Tooling:** hatch-vcs (over uv-dynamic-versioning).
2. **Product-tag format:** exactly `vX.Y.Z` (pure semver, `v` prefix); pre-release/build-metadata suffixes deferred as out of scope.
3. **Runtime surfaces:** startup log on both services + ~~`build_info` Prometheus gauge~~ (gauge dropped 2026-07-12, see 9.1). No `/health` version field.
4. **VEX:** stays non-versioned (5.6).

## 9. Implementation record (2026-07-12)

Built on `feat/git-driven-versioning`. Two deltas from the approved design, plus the empirical findings that shaped the config.

### 9.1 The `build_info` gauge was dropped

The operator decided the Prometheus `build_info` gauge (decision 8.3, design 5.4) is not worth the surface: the startup log line plus the OCI image label already answer "which version is running". Consequence: **no** new observability event / policy arm / sink metric on the crawler, and no counterpart on the verifier. The observability pipeline (`events.py` / `policy.py` / `prometheus_sink.py`) is untouched by this work.

### 9.2 Section 5.2's config was incomplete (setuptools-scm 10.x / vcs-versioning 2.x)

`tag-pattern` + `fallback-version` alone silently yields `0.0.0` on every build. Two independent causes, both empirically verified against the real repo:

1. **Root detection.** hatch-vcs passes `root = <member dir>` to setuptools-scm; the `.git` is at the repo root. setuptools-scm refuses to search parent directories by default and reports "version missing", which `fallback-version` then swallows into `0.0.0`. Fix: `raw-options.search_parent_directories = true`. (This also explains why passing `fallback_version` looked like it "forced" the fallback: derivation was failing at the root step, not at parse.)
2. **Tag selection.** Even with the root found, `git describe` returns the nearest tag, which is always a milestone `vX.Y.Z-<name>`; the anchored `tag-pattern` cannot parse it and setuptools-scm raises. Fix: `raw-options.git_describe_command` with `--exclude '*-*'`, so describe falls back to the last pure-semver tag (`v0.19.1` today -> `0.19.2.devN+g<sha>`).

Both fixes are in every member's `[tool.hatch.version.raw-options]` (section 5.2 updated). Validated end-to-end: `uv sync` locally stamps `0.19.2.devN+g<sha>` on all four members in sync; a real `docker build --build-arg VERSION=9.9.9` stamps both the OCI label and the installed wheel metadata (crawler and verifier), and `SETUPTOOLS_SCM_PRETEND_VERSION` overrides git as designed.

### 9.3 CI: only the build job needs `fetch-depth: 0` (refines risk 7)

Because `search_parent_directories` lets setuptools-scm find the repo even on a shallow checkout, the shallow lint/test jobs derive a throwaway `0.0.1.devN+g<sha>` without crashing `uv sync` (their version is irrelevant). Only `validate.yml`'s `build-and-verify` job, which computes the injected `VERSION`, gets `fetch-depth: 0`. `publish-manifest` and the PR `vex-checks` job stay shallow (they never build an image nor need the version).

### 9.4 Startup-log placement

Crawler: `CrawlerApp.run()` logs `mulewatch version <v>` on the `mulewatch.composition.app` logger (the design said "in `python -m mulewatch`"; `run()` is the covered startup path). Verifier: `__main__.main()` logs `download-verifier version <v>` right after `configure_logging` (the design said "in `app.py`"; `main()` is the actual process entry point where logging is armed). Both read `importlib.metadata.version(...)`, i.e. the number baked into the wheel by 5.3.

### 9.5 Not yet validated in CI

The SBOM cascade (5.4: Syft reading `org.opencontainers.image.version` into the top-level component) is unverified here because it runs only in `publish-manifest` against a pushed digest. Confirm on the first push that the attested CycloneDX/Syft SBOM carries the real version. Also confirm the released image tag `X.Y.Z` and its OCI label agree on a real `vX.Y.Z` tag push.
