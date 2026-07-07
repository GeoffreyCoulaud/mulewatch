# Spec — supply-chain artefact posture: SBOM, cosign signing/attestation, Grype + VEX

> Status: proposed (2026-07-07). Operator review before the implementation plan.

## 1. Context & motivation

Operator goal: bring mulewatch to the **same CI/artefact supply-chain posture** as the sibling
project [`slskd-lidarr-bridge`](https://github.com/GeoffreyCoulaud/slskd-lidarr-bridge), i.e. every
published image carries a **signed SBOM + a signed VEX triage**, and a **daily Grype scan** surfaces
vulnerabilities (VEX-filtered) into GitHub Code scanning.

A gap analysis shows the *hardening baseline is already in place here* (§4), sometimes stricter than
the sibling. The genuine delta is the **Anchore + cosign + OpenVEX chain**: SBOM generation, keyless
signing, signed attestations, and a VEX-aware daily scan. This spec covers only that delta.

This is **not** a copy-paste from the sibling: that project is a *single* image built in one QEMU job
and pushed *by tag*, so it attests right after the build. mulewatch publishes **two** images
(`crawler`, `verifier`), built **per-arch on native runners**, pushed **by digest**, then fanned into
a **multi-arch manifest per package** in `publish-manifest` (the only step that creates a consumable
tag). "Where to attest" and "how many VEX documents" are therefore real design questions, resolved in
§5–§7.

## 2. Decision

1. **Sign + attest the multi-arch index digest**, inside `release.yml` → job `publish-manifest`
   (matrix `[crawler, verifier]`). Not the per-arch child digests (Option A, rejected — §11).
2. **`cosign sign --recursive`** the index (signs the index *and* both child manifests). Keyless OIDC.
3. **Two SBOM formats per image**: CycloneDX (portable, external consumers) + Syft-JSON (preserves
   image source identity, required for image-scoped VEX). **Single-platform (linux/amd64)** — traced
   decision, §11.
4. **Three signed attestations per image** via `cosign attest`: CycloneDX, Syft-JSON, OpenVEX.
5. **Two OpenVEX documents**, one per image (`security/crawler.vex.openvex.json`,
   `security/verifier.vex.openvex.json`), image-scoped, **empty (`statements: []`) at start** (§11).
6. **New daily workflow** `grype-scan.yml` (cron + `workflow_dispatch`), matrix `[crawler, verifier]`:
   pull the signed Syft-JSON SBOM + OpenVEX **from the image attestations**, run Grype, upload SARIF to
   Code scanning. Never fails the job.
7. **`SECURITY.md`** documenting the scan + VEX triage + private reporting (adapted to two images).
8. **Docs**: a French section "Vérifier l'authenticité d'une image" in
   `docs/runbooks/administration.md`; one invariant line in `AGENTS.md` pointing at the chain.
9. **Pin** the four new third-party actions to commit SHAs (+ `# vX.Y.Z` comment), consistent with the
   existing posture; Dependabot already covers them.

## 3. Scope

**In scope** — the artefact chain and its documentation:

- `.github/workflows/release.yml` (extend `publish-manifest`).
- `.github/workflows/grype-scan.yml` (new).
- `security/crawler.vex.openvex.json`, `security/verifier.vex.openvex.json` (new).
- `SECURITY.md` (new, English).
- `docs/runbooks/administration.md` (new section, French — the doc keeps its language).
- `AGENTS.md` (one invariant line).

**Out of scope** (already done — see §4, do not redo): base-image digest pinning, third-party action
SHA pinning, Dependabot, non-root users, least-privilege `permissions:`, container hardening. Also out:
deployment-side admission control (runtime signature enforcement) — the operator chose documentation
only. And the *first VEX triage* (no CVEs are known until the first scan runs — §11).

## 4. Baseline already in place (delta boundary)

The gap analysis established that the following already exist in mulewatch and are **not** part of this
work:

| Measure | State in mulewatch |
|---|---|
| Base images digest-pinned (`@sha256:`) | ✅ builder `ghcr.io/astral-sh/uv:python3.14-alpine` + runtime `python:3.14-alpine` |
| Third-party actions SHA-pinned + `# vX.Y.Z` | ✅ stricter than the sibling (even `actions/*` are SHA-pinned) |
| Dependabot (`github-actions`, `docker`, `uv`) | ✅ grouped weekly |
| Non-root runtime user | ✅ `USER nonroot` in both Dockerfiles |
| Least-privilege `permissions:` | ✅ `contents: read` default, per-job escalation |
| Container hardening (cap_drop, read_only, seccomp) | ✅ documented in AGENTS.md |
| Push-by-digest → manifest fan-in, integration gate before tag publish | ✅ existing pipeline |

## 5. Signing & attestation — `release.yml` / `publish-manifest`

The `publish-manifest` job already: downloads per-arch digests, sets up buildx, logs into ghcr, and
runs `docker buildx imagetools create` to build+push the multi-arch manifest with all tags. It is the
**only** step that creates a consumable tag, so it is the correct place to sign and attest.

Per matrix leg (`crawler`, `verifier`), added steps:

1. **`actions/checkout`** — new. Required to read the per-package VEX file from the repo (the current
   job checks nothing out).
2. **Capture the index digest** after `imagetools create`:
   `docker buildx imagetools inspect <tag> --format '{{.Manifest.Digest}}'`. All subsequent steps
   operate on `ghcr.io/geoffreycoulaud/mulewatch-<pkg>@<index-digest>`, **never on a tag** — signing a
   digest once means every tag pointing at it inherits the signature.
3. **Install cosign** (`sigstore/cosign-installer`, SHA-pinned).
4. **`cosign sign --recursive --yes <image>@<digest>`** — keyless OIDC. `--recursive` signs the index
   *and* each child manifest, so verification works both by tag (→ index) and by arch digest (→ child).
5. **Generate SBOMs** (`anchore/sbom-action`, SHA-pinned), single-platform (linux/amd64, §11):
   - CycloneDX JSON → `/tmp/sbom.cyclonedx.json`
   - Syft-JSON → `/tmp/sbom.syft.json`
   Both scan `<image>@<digest>`; `upload-artifact: false`; registry creds passed for the ghcr pull.
6. **Three attestations** (`cosign attest --yes ... <image>@<digest>`):
   - `--type cyclonedx --predicate /tmp/sbom.cyclonedx.json`
   - `--type https://syft.dev/bom --predicate /tmp/sbom.syft.json`
   - `--type openvex --predicate security/<pkg>.vex.openvex.json`

Job `permissions`: `contents: read`, `packages: write` (existing), **`id-token: write`** (new, for
keyless OIDC).

Note on `--recursive` vs `attest`: `--recursive` is a `cosign sign` notion only; `cosign attest` has
no reliable recursive form, so the three attestations attach to the **index digest** only. This is
consistent with the single-platform SBOM (§11): the attested SBOM describes one platform, and the
image signature (recursive) covers all of them.

## 6. Daily scan — `grype-scan.yml` (new)

Triggers: `schedule:` (daily cron, morning UTC) + `workflow_dispatch:`. Matrix `[crawler, verifier]`.
It **checks nothing out** — every input comes from the image's own signed attestations.

Per matrix leg:

1. Install cosign (SHA-pinned); log into ghcr.
2. **`cosign verify-attestation`** for `--type https://syft.dev/bom` and `--type openvex`, extracting
   each predicate (base64-decode the DSSE payload, `jq '.predicate'`). Identity constraints:
   - `--certificate-identity-regexp "^${{ github.server_url }}/${{ github.repository }}/.github/workflows/release.yml@refs/"`
   - `--certificate-oidc-issuer https://token.actions.githubusercontent.com`
   Verifying (not merely fetching) proves the SBOM/VEX were produced by *our* release workflow for
   *this* image digest. The image ref is `ghcr.io/${GITHUB_REPOSITORY,,}-<pkg>:latest` (OCI refs must
   be lowercase; `github.repository` preserves owner case).
3. **`anchore/scan-action`** (SHA-pinned): `sbom:` the extracted Syft-JSON, `vex:` the extracted
   OpenVEX, `fail-build: "false"`, `output-format: sarif`. Findings are a signal, not a gate.
4. **`github/codeql-action/upload-sarif`** (SHA-pinned) with a **distinct `category` per image**
   (`grype-crawler` / `grype-verifier`) — without it, the two matrix legs' SARIF uploads overwrite each
   other in Code scanning.

Job `permissions`: `contents: read`, `packages: read`, `security-events: write`.

Why the *Syft-JSON* SBOM (not CycloneDX) drives the scan: image-scoped OpenVEX statements match by the
image identity (`pkg:oci/...`); CycloneDX drops that identity, so the statements would no-op. Syft-JSON
preserves it (`.source` carries tags + repoDigests). Same reason the sibling project reads Syft-JSON.

## 7. VEX documents (two, image-scoped, empty at start)

Two files under `security/`, one per image, because the two images have very different dependency
surfaces (the `verifier` embeds ffprobe/clamav; the `crawler` does not), and image-scoped VEX products
differ:

- `security/crawler.vex.openvex.json` → product `pkg:oci/mulewatch-crawler`
- `security/verifier.vex.openvex.json` → product `pkg:oci/mulewatch-verifier`

Each is a well-formed OpenVEX v0.2.0 document with `statements: []` (a frozen `timestamp`, an `author`,
a stable `@id`). A statement is added later, after triage, using the **image-scoped** form — product
`pkg:oci/mulewatch-<pkg>` with the vulnerable package as a `subcomponent` PURL **without a version** —
so it is safe to attach and redistribute (a downstream consumer's unrelated packages are never
suppressed) and survives package bumps. This is the only VEX form the sibling project uses, for the
same reasons.

## 8. `SECURITY.md` (new, English)

Adapted from the sibling, for **two** images. Documents: the three signed attestations per image; that
the daily Grype scan reads the attested Syft-JSON SBOM and applies the VEX; how to add a `not_affected`
statement via `vexctl` (image-scoped, versionless subcomponent, PR-reviewed, no bare ignore lists);
local verification with `--vex`; and GitHub private vulnerability reporting. Names both images and both
VEX files.

## 9. Runbook — image authenticity verification (French)

A new `##` section in `docs/runbooks/administration.md` (kept French), "Vérifier l'authenticité d'une
image", showing operators how to verify a pulled image:

- **Signature**: `cosign verify ghcr.io/geoffreycoulaud/mulewatch-<pkg>:<tag>` with
  `--certificate-identity-regexp` (the release workflow) + `--certificate-oidc-issuer` (GitHub OIDC).
- **Attestations (SBOM/VEX)**: `cosign verify-attestation --type ...` with the same identity flags.

This is documentation only — no runtime admission control is added (operator decision).

## 10. Action pinning

The four new third-party actions are SHA-pinned with a trailing `# vX.Y.Z` comment, matching the
existing convention: `sigstore/cosign-installer`, `anchore/sbom-action`, `anchore/scan-action`,
`github/codeql-action/upload-sarif`. Exact SHAs are resolved at implementation time (real, verified
values — never invented). Dependabot's existing `github-actions` group (`patterns: ["*"]`) already
covers them.

## 11. Traced decisions & non-goals

**Single-platform SBOM (linux/amd64).** The SBOM covers only amd64 of the multi-arch manifest.
Rationale: Grype matches CVEs by **PURL = (package name, version)**; across amd64 and arm64 of the same
`python:3.14-alpine` base built from the same `uv.lock`, the apk packages and wheels carry **identical
names and versions** (only the compiled binaries differ, which Grype does not inspect). An amd64 scan
is therefore an exact representative of the CVE surface of both arches. Confirmed constraint:
`anchore/sbom-action` cannot target a platform (one image per run, no `platform` input), so "per
platform" would mean the Syft CLI + a multi-predicate loop in `grype-scan.yml` (2×2 SBOMs, doubled
SARIF categories) for ~zero additional CVE detection. **Revisit only if** a future arch uses a
different base or package manager (e.g. a non-Alpine variant) — then package sets genuinely diverge and
per-platform SBOMs become necessary.

**`cosign sign --recursive` but attestations on the index only.** `--recursive` (cheap: index + two
children = three signatures) buys arch-digest verifiability. `cosign attest` has no reliable recursive
form; attestations stay on the index, consistent with the single-platform SBOM.

**Empty VEX at start.** No CVEs are known until the first scan runs; inventing statements would be
dishonest. The first triage is follow-up work, not part of this deliverable, and follows the
`SECURITY.md` process.

**Non-goals**: per-arch attestation in `build-and-verify` (Option A — attestations on child digests are
invisible to `cosign verify <tag>`, and the daily scan would quadruple); a separate SLSA build
provenance attestation (the sibling has none); a `.grype.yaml` (VEX comes from the attestation in CI
and via explicit `--vex` locally, matching the sibling's final state); runtime admission control.

## 12. Validation & what stays unvalidated

**No Python source is touched.** The gate `uv run poe check` (per-package 100% branch coverage, ruff,
mypy, sqlfluff, template check) must stay green **without any new unit test** — coverage is unaffected.
Local validation of the new artefacts is **syntactic**: YAML lint / parse of the two workflows, JSON
parse of the two OpenVEX documents (valid OpenVEX shape).

**What cannot be validated locally** (structural, not an oversight): the real chain
(SBOM → `cosign sign` → `cosign attest`, then `grype-scan` verifying and scanning) only exercises once
a **real release has run** (images pushed + GitHub OIDC available), followed by a `workflow_dispatch` of
`grype-scan.yml`. This is recorded as "not validated until a release + a manual scan dispatch have run"
in the handoff.
