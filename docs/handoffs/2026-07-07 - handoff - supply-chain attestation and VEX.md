# Handoff: supply-chain artefact posture (SBOM, cosign signing/attestation, Grype + VEX)

> Date: 2026-07-07. Branch `feat/supply-chain-attestation-vex` (PR pending, see "Next step").

## Current state

The CI/artefact supply-chain chain from the sibling project `slskd-lidarr-bridge` is ported
to mulewatch, adapted to its two-image, multi-arch, push-by-digest pipeline. The full gate
(`uv run poe check`) is green (EXIT 0, 100% branch coverage): no Python source or test was
touched. A final whole-branch review returned APPROVE_WITH_NITS, no Critical/Important
findings. The work is committed on the branch and ready for a PR.

Spec: `docs/specs/2026-07-07-supply-chain-attestation-vex.md` (the authoritative design, 12
sections, decisions traced in §11). Plan: `docs/plans/2026-07-07-supply-chain-attestation-vex.md`.

## What was built

- **`.github/workflows/release.yml`** (job `publish-manifest`, per package `crawler`/`verifier`):
  after the multi-arch manifest is pushed, capture the **index digest**
  (`docker buildx imagetools inspect <tag> --format '{{.Manifest.Digest}}'`), then
  `cosign sign --yes --recursive` it (signs index + both child manifests), generate two SBOMs
  (`anchore/sbom-action`: CycloneDX + Syft-JSON), and `cosign attest` three predicates
  (`cyclonedx`, `https://syft.dev/bom`, `openvex`). Everything binds to the digest, never a tag.
  Added `id-token: write` (keyless OIDC) and a `checkout` (to read the VEX file).
- **`.github/workflows/grype-scan.yml`** (new, daily cron + `workflow_dispatch`, matrix
  `crawler`/`verifier`): checks nothing out, `cosign verify-attestation` pulls the attested
  Syft-JSON SBOM + OpenVEX (identity = `release.yml`, GitHub OIDC issuer), runs
  `anchore/scan-action` (VEX applied, `fail-build: false`), uploads SARIF with a distinct
  `category` per image. Permissions `security-events: write` + read scopes.
- **`security/crawler.vex.openvex.json`, `security/verifier.vex.openvex.json`**: well-formed
  OpenVEX v0.2.0, `statements: []` for now (products `pkg:oci/mulewatch-crawler` / `-verifier`
  once triaged).
- **`SECURITY.md`** (English): signing/attestation, daily scan, VEX triage via `vexctl`
  (image-scoped, versionless subcomponent, PR-reviewed), private reporting.
- **`docs/runbooks/administration.md`**: new French section "Vérifier l'authenticité d'une
  image" (`cosign verify` + `cosign verify-attestation` with the release identity).
- **`AGENTS.md`**: one subsystems-table row pointing at the chain and `SECURITY.md`.

## Decisions (see spec §11)

- **SBOM is single-platform (linux/amd64).** Grype matches CVEs by `(package, version)`, which
  is identical across amd64/arm64 on the shared alpine base + same `uv.lock`, so an amd64 SBOM
  is an exact representative. `anchore/sbom-action` cannot target a platform anyway. Revisit
  ONLY if a future arch uses a different base/package manager.
- **`cosign sign --recursive`** (index + children) but attestations on the index only (`attest`
  has no reliable recursive form; consistent with single-platform SBOM).
- **Empty VEX at start**: no CVEs are known until the first scan. First triage is follow-up.
- **Documentation-only** authenticity verification (no runtime admission control), operator call.

## Learned pitfalls

- `anchore/sbom-action` scans ONE image reference per run, with no platform input: per-platform
  SBOMs would mean the Syft CLI + a multi-predicate loop in grype-scan, for ~zero extra CVE
  detection. Not worth it here.
- Capture the multi-arch **index** digest with `imagetools inspect --format '{{.Manifest.Digest}}'`;
  sign/attest that, so verification "just works" by tag (tag resolves to the index).
- The repo is **public**, so `github/codeql-action/upload-sarif` does NOT need `actions: read`;
  `security-events: write` at workflow scope suffices.
- `cosign verify-attestation` in grype-scan uses `jq -rs '.[0]'` (first attestation of a type):
  fine because each release produces a fresh digest with one attestation set. If a single index
  digest ever accumulated multiple same-type attestations (re-running release on an unchanged
  build), `.[0]` could pick a stale one. Not a current-state bug; noted for awareness.
- Project rule (clarified 2026-07-07): no em-dashes/en-dashes even in developer prose (comments,
  step labels, specs), boy-scout on touched files. Applied to every file this branch created or
  modified; the workflows' pre-existing step labels in the touched job were converted too.

## NOT validated against real hardware/CI (structural, not an oversight)

The real chain (`cosign sign` -> `attest` -> `verify-attestation` -> `grype`) needs GitHub OIDC
and pushed images; it CANNOT run locally. A PR does not exercise it either: signing lives in
`release.yml`, which runs on `main` pushes and `v*` tags, not on PRs. So the chain is unexercised
until the first real release.

On the first release (first `main` push after merge), then a manual `workflow_dispatch` of
`grype-scan.yml`, confirm:
1. `cosign attest --type openvex` accepts a `statements: []` predicate (should wrap it fine).
2. `grype --vex` parses the empty-statements document without error (zero suppressions).
3. `cosign verify-attestation` in grype-scan succeeds against the `release.yml` OIDC identity.
4. The captured digest is indeed the index digest (verification by tag resolves to it).
5. SARIF lands in Security > Code scanning under two categories (`grype-crawler`, `grype-verifier`).

## Suggested next step

1. Open the PR for this branch; let CI's `validate / gate` run (it does not touch the new chain).
2. After merge, the first `main` push triggers `release.yml` -> first real signing/attestation.
3. Manually `workflow_dispatch` `grype-scan.yml` and walk the 5 confirmation points above.
4. First VEX triage: read the Grype findings, add `not_affected` statements per `SECURITY.md`
   (image-scoped, versionless subcomponent) to the matching `security/<image>.vex.openvex.json`.
