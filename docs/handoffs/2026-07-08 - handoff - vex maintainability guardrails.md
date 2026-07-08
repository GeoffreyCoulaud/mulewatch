# Handoff: VEX maintainability guardrails (`vex_guards` package + CI gates)

Date: 2026-07-08
Branch: `feat/vex-guards`
Tag (suggested): `v0.31.0-vex-guardrails`

## Why

The prior milestone (supply-chain attestation) shipped two OpenVEX files with `not_affected`
claims but left them **empty**, and triaged 11 Grype advisories to `not_affected` by hand. A
`not_affected` claim is a promise about how the image is built and run ("we never import
`tarfile`", "we ship `nghttp2-libs` not the `nghttpx` binary"). Nothing stopped that promise from
silently rotting: a future commit could `import tarfile`, or a VEX entry could linger after the
CVE was fixed, and no one would notice. This milestone authors the 19 statements and builds the
machinery that keeps them honest over time, answering two operator questions:

1. How do we avoid introducing calls to code marked `vulnerable_code_not_in_execute_path` before
   the claim is retired by a bump/fix?
2. How do we avoid keeping VEX entries that Grype no longer reports from our SBOM?

## What was built

A fourth uv-workspace package `packages/vex_guards/` (dist `vex-guards`), **dev/CI tooling only,
never shipped in a prod image**, plus the 19 authored OpenVEX statements and the CI wiring.

### The 19 authored statements (`security/{crawler,verifier}.vex.openvex.json`)
- crawler: 8 statements (7 CPython CVEs on `pkg:generic/python` + busybox `CVE-2025-60876`).
- verifier: the same 8 plus clamav `CVE-2016-1405`, nghttp2 `CVE-2026-58055`, rand
  `GHSA-cq8v-f236-94qc`. Union = 11 distinct advisories = the guard registry (a bijection, which
  `check_claim_coverage` enforces). Image-scoped products `pkg:oci/mulewatch-<image>`; versionless
  subcomponent PURLs so a bump does not break a statement. Full table in
  `docs/specs/2026-07-07-vex-maintainability-guardrails.md` section 3.

### The `vex_guards` package (Clean, pure-data descriptors + generic evaluators)
- `descriptors.py`: 6 frozen descriptor dataclasses, the `SourceGuard`/`ImageGuard`/`Guard` union,
  `family(guard)`, `JUSTIFICATION_BY_FAMILY`, and the `is_source_guard`/`is_image_guard`
  `TypeGuard`s (used to narrow without a `type: ignore`).
- `registry.py`: `GUARDS` maps each of the 11 advisory ids to exactly one descriptor.
- `repo.py`: `source_dirs()` (globs `packages/*/src`, **excludes `vex_guards`** so its data does not
  self-match), `dockerfiles()`, `vex_files()`, and the shared `display_path()`.
- `vex_io.py`, `sbom.py`, `grype.py` (injectable `GrypeRunner` Protocol + `SubprocessGrypeRunner`),
  `sarif.py`, `source_scan.py` (AST import detection + string-word for binaries + last-`FROM` for
  Alpine), and the four check CLIs.

### The four checks (the answer to the two operator questions)
- `check_source_claims` (poe `vex-source-claims`): scans our source + Dockerfiles; fails if we
  start reaching exempted code. Answers question 1.
- `check_claim_coverage` (poe `vex-claim-coverage`): enforces the VEX-registry bijection + family
  <-> justification agreement. Answers question 2 (an entry with no Grype-backed guard cannot exist).
- `check_image_claims`: image-family guards (PackageAbsent / PackageMinVersion) against a syft-json
  SBOM.
- `check_stale_claims`: flags VEX entries Grype no longer reports (injectable runner).

### CI wiring
- `pr.yml`: a non-blocking `vex-checks` job (source-claims + claim-coverage). Branch protection is
  unchanged (only `validate / gate` is required), so a VEX drift never blocks an unrelated PR.
- `grype-scan.yml` (daily): after extracting the attested SBOM/VEX, runs `check_image_claims` and
  `check_stale_claims` in SARIF mode and uploads them under `vex-image-claims-<pkg>` /
  `vex-stale-claims-<pkg>` categories. Non-blocking; drift surfaces in Code scanning.
- `release.yml` `publish-manifest`: **the release is gated on VEX honesty before anything a
  consumer can trust is published** (see the decisions below).

## Key decisions (this milestone)

- **Release gate runs before publish, not after signing (decided 2026-07-08).** The first wiring
  put the checks after `cosign sign`, so a failure blocked only attestation while the image was
  already tagged and signed. Reworked `publish-manifest` to generate both SBOMs from a **per-arch
  image digest** (already in ghcr by digest from `build-and-verify`), run the checks, and only
  then create/push the tags, sign, and attest. On a failed gate nothing consumer-facing (tag,
  signature, attestation) is published; only the unreferenced per-arch digests remain. This is why
  `SECURITY.md` can truthfully say "before anything is signed or attested".
- **Staleness is NOT a release hard-fail (decided 2026-07-08, overrides the plan's "all four
  hard-fail").** A stale VEX entry means Grype stopped reporting a CVE we suppressed, i.e. it was
  fixed upstream: that never makes the image less safe, so blocking a release on it punishes good
  news. `check_stale_claims` stays in the daily grype-scan as a non-blocking SARIF signal that
  prompts a VEX cleanup. The release hard-fails only on the three checks that catch a genuinely
  false claim (source, coverage, image). Removing stale also removed the last Grype consumer at
  release, so the pinned Grype install was dropped from `release.yml`.
- **`vex_guards` uses the hatchling build backend**, mirroring the three existing members (the
  plan's `uv_build` template was overridden to match reality).
- **Grype CLI pinned** to `v0.115.0`, install script pinned to the tag commit
  `fa8b7e2a528cf1f8b098123f256c61db9e5df69c`. Used only in `grype-scan.yml` now.
- **`PackageMinVersion("clamav", "0.99")`** encodes the clamav triage reason (OLE2 bug fixed in
  0.99; we ship 1.4.4) as a checkable guard rather than an unexplained ignore, per the operator's
  call in the prior design discussion.

## Learned pitfalls (do not rediscover these)

- **Run the FULL lint when verifying, not a subset.** `poe type-check` + `poe format-check` are
  not enough: `poe lint` (ruff check) is separate and catches things the other two miss. This bit
  twice: a `sarif.py` committed in unformatted shape (only `poe lint` was run, not format-check),
  and a `setattr(...)` swap that tripped **ruff B010**. Always run `uv run poe check` (or at least
  `lint-all`) before declaring a change green.
- **`ruff B010` vs `mypy` on a frozen-dataclass write are mutually exclusive.** Testing
  `FrozenInstanceError` needs a direct `obj.field = x` (which mypy rejects -> needs
  `# type: ignore[misc]`); `setattr(obj, "field", x)` avoids the ignore but trips ruff B010, whose
  fix is the direct write again. The `# type: ignore[misc]` direct write is the only form that
  satisfies both. See `tests/test_violations.py`.
- **A `TypeGuard` narrows a variable, not a subscript.** `is_image_guard(GUARDS[cve])` does not
  narrow `GUARDS[cve]` in a dict comprehension value position under mypy strict. Iterate
  `GUARDS.items()` and narrow the loop variable instead (both `check_source_claims` and
  `check_image_claims` do this).
- **`--cov-branch` flags a `match` with no wildcard** as a partial branch (the "matched nothing"
  fall-through), even when mypy proves exhaustiveness. Close it with `case _:` `assert_never(x)`
  carrying `# pragma: no cover` (the AGENTS.md idiom); mypy does not need it, coverage does.
- **Generating an SBOM does not require the tagged index.** The per-arch images are already in
  ghcr by digest after `build-and-verify`; scanning one digest lets the release gate run before
  the tag/signature exist. This is the trick that made the "gate before publish" reorder possible.

## What is NOT validated against real hardware

- **The empirical VEX suppression was NOT run this session** (no Docker/syft/grype available on the
  dev box or in the sandbox). Specifically unverified:
  - That `grype v0.115.0 --vex security/<image>.vex.openvex.json` actually suppresses every
    triaged CVE for the built image (the subcomponent PURL forms may need the qualified
    `?arch=&distro=` variant instead of the base PURL; spec section 3 flags this as the Act-phase
    reconciliation that still owes an outcome).
  - That Grype v0.115.0 still reports every one of the 11 CVEs for the freshly built image under
    the SAME id we authored. Riskiest case: `GHSA-cq8v-f236-94qc` (cargo `rand` inside
    `librav1e`) may be reported under a RUSTSEC alias. If so, the daily grype-scan's
    `check_stale_claims` SARIF will flag it (non-blocking) and the VEX entry should be re-keyed.
  - `check_image_claims` is indirectly reassured by the prior session's empirical SBOM (149 apk
    packages, `nghttp2` absent / only `nghttp2-libs`, `clamav 1.4.4-r0`), and the runtime
    Dockerfiles did not change on this branch, so its two guards should pass. Still, the release
    hard-fails on it, so confirm on the first release run.
- **The three workflow additions are exercised only by real CI** (OIDC, a pushed image, the daily
  cron). None can run locally. The first release and the next daily grype-scan are the real proof.
- **The Dockerfile bind-mount** (`packages/vex_guards/pyproject.toml`) is validated only by a host
  `uv sync --locked` resolve; a real image build is the compose integration suite's job.

## Suggested next step

1. **First release / first daily scan is the validation.** Watch the first `release.yml` run: the
   three hard-fail gates must pass (they should, per the above). Watch the next `grype-scan.yml`:
   confirm `vex-image-claims-*` and `vex-stale-claims-*` categories are clean, and that the main
   Grype scan shows our 11 CVEs suppressed by the VEX. If a stale entry or a PURL mismatch shows
   up, reconcile the VEX (re-key an alias, or qualify a PURL) and re-run `poe vex-claim-coverage`.
2. **`uv sync --frozen` Dockerfile cleanup (tracked debt).** Both prod Dockerfiles still bind-mount
   every workspace member's `pyproject.toml` (now including `vex_guards`) only to satisfy
   `uv sync --locked`. Switching the deps layer to `uv sync --frozen` would drop all these mounts,
   including the pre-existing `verifier`-in-`crawler` one. Deferred deliberately (accepted cost).
3. **Optional hardening (Minor, logged in the holistic review):** `check_image_claims` /
   `check_stale_claims` `--format sarif` without `--output` would `TypeError` on `Path(None)` (our
   workflows always pass `--output`, so untested); and `source_scan._imported_modules` does not
   detect the `from importlib import import_module; import_module("x")` alias form (out of scope,
   operator-reviewed tree). Neither blocks anything.

## State

- Full gate green: `uv run poe check` (lint-all + matching 234 + crawler 990 + verifier 176 +
  vex_guards 70, all 100% branch).
- Every task reviewed (task-scoped) and a whole-branch holistic review returned APPROVE_WITH_NITS
  (nits fixed). 18 commits on the branch (2 docs, 16 impl); merge-base `8dd4733`.
- Not yet: PR opened, CI green, tag `v0.31.0-vex-guardrails`, branch cleanup.
