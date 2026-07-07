# Supply-chain attestation + VEX: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every published image (`crawler`, `verifier`) carries a keyless-signed signature + three signed attestations (CycloneDX SBOM, Syft-JSON SBOM, OpenVEX), and a daily Grype scan surfaces VEX-filtered findings into GitHub Code scanning.

**Architecture:** Sign + attest the **multi-arch index digest** inside `release.yml`'s `publish-manifest` job (the only step that creates a consumable tag), per package. A new `grype-scan.yml` pulls the signed Syft-JSON SBOM + OpenVEX **from the image attestations** (nothing checked out) and scans daily. Two image-scoped OpenVEX documents, empty at start.

**Tech Stack:** GitHub Actions, `sigstore/cosign` (keyless OIDC), `anchore/sbom-action` (Syft), `anchore/scan-action` (Grype), `github/codeql-action/upload-sarif`, OpenVEX v0.2.0.

**Spec:** `docs/specs/2026-07-07-supply-chain-attestation-vex.md` (approved 2026-07-07).

## Global Constraints

- **No Python source is touched.** `uv run poe check` (per-package 100% branch coverage, ruff, mypy, sqlfluff, template check) must stay **green with no new unit test**: coverage is unaffected. This is the final gate (Task 6).
- **Validation is syntactic + structural**, not runtime: YAML parse/lint of workflows, JSON parse + OpenVEX-shape check of the VEX files. The real chain (sign → attest → scan) is **only exercisable after a real release + a `workflow_dispatch`** of `grype-scan.yml`, recorded in the handoff, not validated here.
- **Third-party actions are SHA-pinned + a trailing `# vX.Y.Z` comment.** Resolve REAL SHAs (`gh api repos/<owner>/<repo>/commits/<tag> --jq '.sha'`). Never invent one. Reference versions below are the known-good minimum; bump to the latest release if newer.
- **Sign/attest always target the index digest**, never a tag (a tag inherits the signature).
- **OCI refs are lowercase.** `IMAGE_PREFIX` (`ghcr.io/geoffreycoulaud/mulewatch`) is already lowercase.
- **Language:** SECURITY.md is English; the runbook addition stays French (the doc keeps its language); CI step names are English; commit messages are English (Conventional Commits).
- **Commit frequently**, one focused commit per task.

**Reference action versions (resolve to SHAs at implementation):**
| Action | Reference version | Note |
|---|---|---|
| `sigstore/cosign-installer` | `v4.1.2` | new |
| `anchore/sbom-action` | `v0.24.0` | new |
| `anchore/scan-action` | `v7.4.0` | new |
| `github/codeql-action/upload-sarif` | `v4.36.2` | new (sub-path of `github/codeql-action`) |
| `docker/login-action` | `c99871dec2022cc055c062a10cc1a1310835ceb4 # v4.3.0` | **already pinned in this repo: reuse verbatim** |

---

## Task 1: OpenVEX documents (two, image-scoped, empty)

**Files:**
- Create: `security/crawler.vex.openvex.json`
- Create: `security/verifier.vex.openvex.json`

**Interfaces:**
- Produces: the two predicate files that Task 2's `cosign attest --type openvex` reads at `security/<package>.vex.openvex.json`, and that Task 4 (SECURITY.md) + Task 3 (grype-scan) reference.

**Why two, both empty:** the two images have different dependency surfaces; a statement is image-scoped by `@id: pkg:oci/mulewatch-<pkg>`. No CVEs are known until the first scan, so both start at `statements: []` (a valid OpenVEX degenerate case: Grype applies zero suppressions). The product PURL only appears once the first triage adds a statement. The two files are near-identical now and diverge at first triage.

- [ ] **Step 1: Create `security/crawler.vex.openvex.json`**

```json
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://github.com/GeoffreyCoulaud/mulewatch/blob/main/security/crawler.vex.openvex.json",
  "author": "GeoffreyCoulaud",
  "timestamp": "2026-07-07T00:00:00Z",
  "version": 1,
  "statements": []
}
```

- [ ] **Step 2: Create `security/verifier.vex.openvex.json`**

```json
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://github.com/GeoffreyCoulaud/mulewatch/blob/main/security/verifier.vex.openvex.json",
  "author": "GeoffreyCoulaud",
  "timestamp": "2026-07-07T00:00:00Z",
  "version": 1,
  "statements": []
}
```

- [ ] **Step 3: Validate JSON + OpenVEX shape**

Run:
```bash
python3 - <<'PY'
import json, sys
for p in ("security/crawler.vex.openvex.json", "security/verifier.vex.openvex.json"):
    d = json.load(open(p))
    assert d["@context"] == "https://openvex.dev/ns/v0.2.0", p
    assert d["@id"].endswith(f"{p.split('/')[-1]}"), p
    assert d["author"] and d["timestamp"] and isinstance(d["version"], int), p
    assert d["statements"] == [], p
    print("OK", p)
PY
```
Expected: `OK security/crawler.vex.openvex.json` and `OK security/verifier.vex.openvex.json`. If `vexctl` happens to be installed, `vexctl` reads them without error too (optional).

- [ ] **Step 4: Commit**

```bash
git add security/crawler.vex.openvex.json security/verifier.vex.openvex.json
git commit -m "feat(security): add empty image-scoped OpenVEX docs (crawler, verifier)"
```

---

## Task 2: Sign + SBOM + attest in `release.yml` / `publish-manifest`

**Files:**
- Modify: `.github/workflows/release.yml` (replace the `publish-manifest` job)

**Interfaces:**
- Consumes: `security/<package>.vex.openvex.json` (Task 1); the existing `steps.meta.outputs.version` tag; `IMAGE_PREFIX`.
- Produces: on the pushed index digest per package: one recursive cosign signature and three attestations (`cyclonedx`, `https://syft.dev/bom`, `openvex`) that Task 3 verifies.

**Depends on Task 1** (the OpenVEX predicate files must exist).

- [ ] **Step 1: Resolve the two new action SHAs**

Run (bump the tag if a newer release exists; record the resolved SHA):
```bash
gh api repos/sigstore/cosign-installer/commits/v4.1.2 --jq '.sha'
gh api repos/anchore/sbom-action/commits/v0.24.0 --jq '.sha'
```

- [ ] **Step 2: Replace the `publish-manifest` job** in `.github/workflows/release.yml`

Replace the entire `publish-manifest:` job with the block below. Changes vs current: `id-token: write` added; a first `actions/checkout`; a `Capture pushed index digest` step; cosign install + sign; two SBOM steps; the attest step. Substitute the two `<SHA>` placeholders with Step 1's values.

```yaml
  publish-manifest:
    needs: validate
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
      id-token: write  # keyless OIDC for cosign sign/attest
    strategy:
      fail-fast: false
      matrix:
        package: [crawler, verifier]
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
      - name: Download digests (${{ matrix.package }})
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          pattern: digest-${{ matrix.package }}-*
          path: /tmp/digests
          merge-multiple: true
      - uses: docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c # v4.2.0
      - name: Log in to ghcr.io
        uses: docker/login-action@c99871dec2022cc055c062a10cc1a1310835ceb4 # v4.3.0
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Image tags (${{ matrix.package }})
        id: meta
        uses: docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302 # v6.2.0
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
      # The signature/attestations bind to the pushed index digest (not a tag): every
      # tag pointing at this digest inherits them. imagetools inspect resolves the tag
      # to the multi-arch index descriptor; .Manifest.Digest is that index's digest.
      - name: Capture pushed index digest
        id: digest
        run: |
          ref="${{ env.IMAGE_PREFIX }}-${{ matrix.package }}:${{ steps.meta.outputs.version }}"
          digest=$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}')
          echo "ref=${{ env.IMAGE_PREFIX }}-${{ matrix.package }}@${digest}" >> "$GITHUB_OUTPUT"
      - name: Install cosign
        uses: sigstore/cosign-installer@<SHA> # v4.1.2
      # --recursive signs the index AND each child manifest, so verification works both
      # by tag (-> index) and by arch digest (-> child). attest has no recursive form;
      # the three attestations below bind to the index only (consistent with the
      # single-platform SBOM, see spec 2026-07-07-supply-chain-attestation-vex §11).
      - name: Sign the image (keyless OIDC, recursive over the index)
        run: cosign sign --yes --recursive "${{ steps.digest.outputs.ref }}"
      - name: Generate CycloneDX SBOM (portable, external consumers)
        uses: anchore/sbom-action@<SHA> # v0.24.0
        with:
          image: ${{ steps.digest.outputs.ref }}
          format: cyclonedx-json
          output-file: /tmp/sbom.cyclonedx.json
          upload-artifact: "false"
          registry-username: ${{ github.actor }}
          registry-password: ${{ secrets.GITHUB_TOKEN }}
      - name: Generate Syft-JSON SBOM (native, for the VEX-aware daily scan)
        uses: anchore/sbom-action@<SHA> # v0.24.0
        with:
          image: ${{ steps.digest.outputs.ref }}
          format: syft-json
          output-file: /tmp/sbom.syft.json
          upload-artifact: "false"
          registry-username: ${{ github.actor }}
          registry-password: ${{ secrets.GITHUB_TOKEN }}
      - name: Attest SBOMs and VEX (keyless OIDC)
        run: |
          ref='${{ steps.digest.outputs.ref }}'
          cosign attest --yes --type cyclonedx \
            --predicate /tmp/sbom.cyclonedx.json "$ref"
          cosign attest --yes --type https://syft.dev/bom \
            --predicate /tmp/sbom.syft.json "$ref"
          cosign attest --yes --type openvex \
            --predicate security/${{ matrix.package }}.vex.openvex.json "$ref"
      - name: Inspect
        run: docker buildx imagetools inspect ${{ env.IMAGE_PREFIX }}-${{ matrix.package }}:${{ steps.meta.outputs.version }}
```

- [ ] **Step 3: Validate the workflow YAML**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('release.yml OK')"
```
Expected: `release.yml OK`. If `actionlint` is available (`command -v actionlint`), also run `actionlint .github/workflows/release.yml` and expect no errors; otherwise note it was skipped.

- [ ] **Step 4: Confirm no `<SHA>` placeholder remains**

Run:
```bash
! grep -n '<SHA>' .github/workflows/release.yml && echo "no placeholder left"
```
Expected: `no placeholder left`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): sign image + attest SBOMs/VEX on the multi-arch index"
```

---

## Task 3: `grype-scan.yml` (daily VEX-aware scan)

**Files:**
- Create: `.github/workflows/grype-scan.yml`

**Interfaces:**
- Consumes: the attestations Task 2 produces (`https://syft.dev/bom`, `openvex`), verified by the `release.yml` OIDC identity.
- Produces: SARIF into Code scanning, one `category` per image.

- [ ] **Step 1: Resolve the two new action SHAs**

Run (bump if newer; record SHAs):
```bash
gh api repos/anchore/scan-action/commits/v7.4.0 --jq '.sha'
gh api repos/github/codeql-action/commits/v4.36.2 --jq '.sha'
```
(Reuse the `sigstore/cosign-installer` SHA resolved in Task 2, and the repo's existing `docker/login-action@c99871…` pin.)

- [ ] **Step 2: Create `.github/workflows/grype-scan.yml`**

Substitute the three `<SHA>` placeholders.

```yaml
name: Grype daily SBOM scan

on:
  schedule:
    # Every morning at 06:00 UTC
    - cron: '0 6 * * *'
  workflow_dispatch:

permissions:
  contents: read
  packages: read
  security-events: write  # required to upload SARIF to GitHub code scanning

env:
  IMAGE_PREFIX: ghcr.io/geoffreycoulaud/mulewatch

jobs:
  scan:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        package: [crawler, verifier]
    steps:
      - name: Install cosign
        uses: sigstore/cosign-installer@<SHA> # v4.1.2

      - name: Log in to ghcr.io
        uses: docker/login-action@c99871dec2022cc055c062a10cc1a1310835ceb4 # v4.3.0
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      # Everything the scan needs comes from the image's own signed attestations, so this
      # workflow checks nothing out. verify-attestation (not merely fetch) proves the SBOM
      # and VEX were produced by OUR release.yml for THIS image digest. jq decodes the DSSE
      # payload and extracts the raw predicate.
      #   - Syft-JSON SBOM (type https://syft.dev/bom): scanned instead of CycloneDX because
      #     it preserves the image source identity image-scoped OpenVEX matching needs.
      #   - OpenVEX (type openvex): applied via Grype's --vex.
      - name: Extract SBOM and VEX from image attestations
        env:
          CERT_REGEXP: "^${{ github.server_url }}/${{ github.repository }}/.github/workflows/release.yml@refs/"
        run: |
          IMAGE="${IMAGE_PREFIX}-${{ matrix.package }}:latest"
          verify() {  # $1 = predicate type -> stdout: the raw predicate JSON
            cosign verify-attestation \
              --type "$1" \
              --certificate-identity-regexp "${CERT_REGEXP}" \
              --certificate-oidc-issuer https://token.actions.githubusercontent.com \
              "${IMAGE}" \
              | jq -rs '.[0].payload | @base64d | fromjson | .predicate'
          }
          verify "https://syft.dev/bom" > /tmp/sbom.syft.json
          verify openvex                > /tmp/vex.openvex.json

      # Scans the SBOM (not the live image) so results are reproducible, applying the
      # OpenVEX pulled from the image. fail-build is false: findings are surfaced via
      # SARIF, not a job failure; triage happens through VEX.
      - name: Run Grype against the attested SBOM
        uses: anchore/scan-action@<SHA> # v7.4.0
        id: scan
        with:
          sbom: /tmp/sbom.syft.json
          vex: /tmp/vex.openvex.json
          fail-build: "false"
          output-format: sarif

      # Distinct category per image: without it, the two matrix legs' SARIF uploads
      # overwrite each other in Code scanning.
      - name: Upload SARIF to GitHub code scanning
        uses: github/codeql-action/upload-sarif@<SHA> # v4.36.2
        with:
          sarif_file: ${{ steps.scan.outputs.sarif }}
          category: grype-${{ matrix.package }}
```

- [ ] **Step 3: Validate the workflow YAML**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/grype-scan.yml')); print('grype-scan.yml OK')"
! grep -n '<SHA>' .github/workflows/grype-scan.yml && echo "no placeholder left"
```
Expected: `grype-scan.yml OK` then `no placeholder left`. Run `actionlint .github/workflows/grype-scan.yml` if available, else note skipped.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/grype-scan.yml
git commit -m "ci(security): add daily Grype scan of attested SBOMs with VEX (crawler, verifier)"
```

---

## Task 4: `SECURITY.md`

**Files:**
- Create: `SECURITY.md`

**Interfaces:**
- Consumes: names of the two images, the two VEX files (Task 1), and the workflows (Tasks 2, 3).

- [ ] **Step 1: Create `SECURITY.md`** (English)

```markdown
# Security Policy

mulewatch publishes two images to GHCR: `mulewatch-crawler` and `mulewatch-verifier`.
This policy applies to both.

## Signing & attestations

Every image pushed to GHCR is **signed** (keyless, OIDC-based, via
[cosign](https://github.com/sigstore/cosign)) and carries three **signed attestations**:

- a **CycloneDX** SBOM: the standard, portable [Syft](https://github.com/anchore/syft)
  bill of materials for external consumers;
- a **Syft-JSON** SBOM: Syft's native format, consumed by the daily scan because it
  preserves the image source identity that image-scoped VEX matching needs (see below);
- an **[OpenVEX](https://openvex.dev/)** document (`security/<image>.vex.openvex.json`):
  the triage marking non-exploitable CVEs as `not_affected`.

Signing and attestation happen in `.github/workflows/release.yml` (job `publish-manifest`),
bound to the multi-arch **index digest** of each image; the signature is `--recursive`, so
each per-arch child manifest is signed too. See `docs/runbooks/administration.md`
(section "Vérifier l'authenticité d'une image") for how to verify a pulled image.

## Vulnerability scanning

A [Grype](https://github.com/anchore/grype) scan runs daily against each image's attested
Syft-JSON SBOM (`.github/workflows/grype-scan.yml`), applying that image's VEX. Results
appear in the repository's **Security → Code scanning** tab as SARIF findings, one category
per image (`grype-crawler`, `grype-verifier`). The scan never fails the workflow. Findings
are triaged through VEX.

## Triage process: VEX

[OpenVEX](https://openvex.dev/) statements tell Grype which CVEs are **not exploitable** in
this deployment context, so they are filtered out of scan results automatically. Each image
has its own file (`security/crawler.vex.openvex.json`, `security/verifier.vex.openvex.json`),
versioned here (the source of truth) and attached to the released image as a signed OpenVEX
attestation: the daily scan pulls it **from the image**. For a local run, point Grype at the
file explicitly with `--vex security/<image>.vex.openvex.json`.

### Statements are image-scoped, and the scan reads the Syft-JSON SBOM

Grype resolves a VEX statement by the **image** identity (`pkg:oci/...`) then by the
vulnerable **package** PURL. We use the **image-scoped** form (product
`pkg:oci/mulewatch-<image>` with the vulnerable package as a `subcomponent`) because it is
the only form safe to attach and redistribute: it is scoped to *this* image, so a downstream
consumer's unrelated packages are never suppressed by our statements. Use the subcomponent
PURL **without a version** so a statement survives package bumps.

For that to work the scan must expose the image identity to Grype. A **CycloneDX** SBOM drops
it (the image-scoped product then matches nothing); Syft's native **Syft-JSON** preserves it.
That is why the daily scan reads the attested Syft-JSON SBOM, and why local verification must
use Syft-JSON too, not CycloneDX.

### Adding a `not_affected` statement

When a CVE is triaged and found not exploitable, open a PR adding an OpenVEX statement with
[`vexctl`](https://github.com/openvex/vexctl) to the **matching image's** file:

\`\`\`sh
go install github.com/openvex/vexctl@latest

# --product is THIS image; --subcomponents is the vulnerable package's PURL
# (from the Grype finding / SBOM), WITHOUT a version. Comma-separate several.
vexctl add \
  --in-place \
  --file security/verifier.vex.openvex.json \
  --product "pkg:oci/mulewatch-verifier" \
  --subcomponents "pkg:apk/alpine/<package>" \
  --vulnerability CVE-YYYY-NNNNN \
  --status not_affected \
  --justification vulnerable_code_not_in_execute_path \
  --impact-statement "Brief explanation of why this CVE does not affect this image"
\`\`\`

Verify the suppression applies before opening the PR, using a **Syft-JSON** SBOM:

\`\`\`sh
syft <image> -o syft-json=/tmp/sbom.syft.json
grype sbom:/tmp/sbom.syft.json --vex security/verifier.vex.openvex.json --show-suppressed | grep <CVE>
\`\`\`

Valid `--justification` values (OpenVEX vocabulary): `component_not_present`,
`vulnerable_code_not_present`, `vulnerable_code_cannot_be_controlled_by_adversary`,
`vulnerable_code_not_in_execute_path`, `inline_mitigations_already_exist`.

The PR description must explain the triage rationale, and a reviewer must approve. Do **not**
add CVEs to ignore lists without a VEX statement: this keeps all suppressions auditable and
signed.

## Reporting a security issue

To report a vulnerability privately, use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
feature for this repository.
```

Note: in the file, the three ` \`\`\` ` fences shown escaped above must be plain ```` ``` ```` fences.

- [ ] **Step 2: Validate Markdown links/fences**

Run:
```bash
grep -c '```' SECURITY.md   # expect an even count (all fences closed)
python3 -c "print('has both images:', 'mulewatch-crawler' in open('SECURITY.md').read() and 'mulewatch-verifier' in open('SECURITY.md').read())"
```
Expected: an even number, then `has both images: True`.

- [ ] **Step 3: Commit**

```bash
git add SECURITY.md
git commit -m "docs(security): add SECURITY.md (signing, scanning, VEX triage, reporting)"
```

---

## Task 5: Runbook section (French) + AGENTS.md invariant line

**Files:**
- Modify: `docs/runbooks/administration.md` (new `##` section, French)
- Modify: `AGENTS.md` (one line in the subsystems table)

**Interfaces:**
- Consumes: image names + the `release.yml` OIDC identity (for the verify commands).

- [ ] **Step 1: Add the runbook section**. Insert a new `##` section into `docs/runbooks/administration.md`, after the `## WebUI (consultation du catalogue)` section and before `## Limites connues / follow-ups`:

```markdown
## Vérifier l'authenticité d'une image

Chaque image publiée est signée et attestée par la CI (cosign, keyless OIDC). Avant de
lancer une image tirée de GHCR, on peut vérifier qu'elle vient bien de notre pipeline.

Prérequis : [cosign](https://github.com/sigstore/cosign) installé.

L'identité attendue est le workflow de release du dépôt :

\`\`\`sh
IMAGE=ghcr.io/geoffreycoulaud/mulewatch-crawler:latest   # ou mulewatch-verifier
IDENTITY='^https://github.com/GeoffreyCoulaud/mulewatch/.github/workflows/release.yml@refs/'
ISSUER=https://token.actions.githubusercontent.com
\`\`\`

Vérifier la **signature** de l'image :

\`\`\`sh
cosign verify \
  --certificate-identity-regexp "$IDENTITY" \
  --certificate-oidc-issuer "$ISSUER" \
  "$IMAGE"
\`\`\`

Vérifier une **attestation** (SBOM ou VEX ; `--type` parmi `cyclonedx`,
`https://syft.dev/bom`, `openvex`) :

\`\`\`sh
cosign verify-attestation \
  --type openvex \
  --certificate-identity-regexp "$IDENTITY" \
  --certificate-oidc-issuer "$ISSUER" \
  "$IMAGE"
\`\`\`

Une commande qui réussit prouve que ce digest a été signé/attesté par notre CI : un digest
substitué (image malveillante) n'aurait pas d'attestation signée par notre identité OIDC.
La signature étant `--recursive`, la vérification fonctionne aussi bien par tag (index) que
par digest d'architecture. Le détail de la chaîne et du triage VEX est dans `SECURITY.md`.
```

(In the file, the escaped ` \`\`\` ` fences are plain ```` ``` ```` fences.)

- [ ] **Step 2: Add the AGENTS.md subsystems-table line**. In `AGENTS.md`, under "Where the code lives", add a row to the subsystems table (after the `Packaging` row):

```markdown
| Supply-chain artefacts | `security/` + `.github/workflows/grype-scan.yml` + `release.yml` (`publish-manifest`) | keyless cosign signature + 3 signed attestations (CycloneDX/Syft-JSON SBOM, OpenVEX) per image on the multi-arch index; daily Grype scan → Code scanning. See `SECURITY.md`. |
```

- [ ] **Step 3: Validate**

Run:
```bash
grep -q "Vérifier l'authenticité d'une image" docs/runbooks/administration.md && echo "runbook OK"
grep -q "Supply-chain artefacts" AGENTS.md && echo "agents OK"
grep -c '```' docs/runbooks/administration.md   # expect an even count
```
Expected: `runbook OK`, `agents OK`, even fence count.

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/administration.md AGENTS.md
git commit -m "docs(runbook): document image authenticity verification; note supply-chain chain in AGENTS.md"
```

---

## Task 6: Final gate + holistic review

**Files:** none (verification only).

- [ ] **Step 1: Run the full gate** (must be green, no Python touched)

Run:
```bash
uv run poe check
```
Expected: PASS (per-package 100% branch coverage, ruff, mypy, sqlfluff, templates), unchanged from before this branch.

- [ ] **Step 2: Re-validate all new artefacts together**

Run:
```bash
python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ('.github/workflows/release.yml','.github/workflows/grype-scan.yml')]; print('workflows OK')"
python3 -c "import json; [json.load(open(f)) for f in ('security/crawler.vex.openvex.json','security/verifier.vex.openvex.json')]; print('vex OK')"
! grep -rn '<SHA>' .github/workflows/ && echo "no placeholder left"
command -v actionlint >/dev/null && actionlint .github/workflows/release.yml .github/workflows/grype-scan.yml || echo "actionlint not installed: skipped"
```
Expected: `workflows OK`, `vex OK`, `no placeholder left`, and either actionlint success or the skip note.

- [ ] **Step 3: Holistic review**. Read the full diff (`git diff main...HEAD`) with fresh eyes. Check: every `<SHA>` replaced with a real pinned commit + `# vX.Y.Z` comment; the `cosign verify-attestation` identity regexp in `grype-scan.yml` matches where signing happens (`release.yml`); the `--predicate` VEX path matches the file names from Task 1; image refs lowercase; `id-token: write` present on `publish-manifest`; no Python or test file changed.

- [ ] **Step 4: No commit** (review only). Proceed to the Verify/Wrap phase (handoff + PR) per AGENTS.md.

---

## Self-Review (author)

**Spec coverage:** §5 → Task 2; §6 → Task 3; §7 → Task 1; §8 → Task 4; §9 → Task 5 (runbook) + AGENTS.md line; §10 pinning → Steps in Tasks 2/3 (resolve SHAs) + Task 6 placeholder check; §11 traced decisions → encoded as comments in the release.yml block + this plan's rationale notes; §12 validation → Task 6. All sections covered.

**Placeholder scan:** the only intentional `<SHA>` tokens are resolved in Tasks 2/3 Step 1 and asserted gone in Task 6 Step 2. No TBD/TODO. Fence-escaping notes call out the `\`\`\`` rendering in Tasks 4/5.

**Type consistency:** the digest ref output (`steps.digest.outputs.ref`) is defined once in Task 2 and reused in the sign/sbom/attest steps; `grype-scan.yml`'s `verify()` predicate types (`https://syft.dev/bom`, `openvex`) match the `cosign attest --type` values in Task 2; VEX file paths (`security/<package>.vex.openvex.json`) are identical across Tasks 1, 2, 4.

## Execution ordering (for the subagent-driven phase)

- **Task 1 → Task 2** are sequential (Task 2 reads Task 1's files).
- **Tasks 3, 4, 5** are independent of each other and of Task 2's *writing* (they only depend on it at runtime), so they can run in parallel after Task 1.
- **Task 6** runs last, after all others merge into the branch.
