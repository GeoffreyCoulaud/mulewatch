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
appear in the repository's **Security > Code scanning** tab as SARIF findings, one category
per image (`grype-crawler`, `grype-verifier`). The scan never fails the workflow: findings
are triaged through VEX.

## Triage process (VEX)

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

```sh
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
```

Verify the suppression applies before opening the PR, using a **Syft-JSON** SBOM:

```sh
syft <image> -o syft-json=/tmp/sbom.syft.json
grype sbom:/tmp/sbom.syft.json --vex security/verifier.vex.openvex.json --show-suppressed | grep <CVE>
```

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
