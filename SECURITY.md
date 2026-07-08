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

### Keeping the VEX claims honest: the four consistency checks

A `not_affected` claim is a promise about how the image is built and run. Over time source
and base images drift, so four checks (the `vex_guards` package) assert those promises still
hold. They run as a non-blocking PR job (only `validate / gate` is required to merge) and, for
the three that catch a genuinely false claim, as hard-fail steps in the release before anything
is signed or attested:

- **`check_source_claims`** (PR job, release hard-fail): fails if our own source starts
  reaching code a `vulnerable_code_not_in_execute_path` claim says we never execute, for
  example importing `tarfile`, `configparser`, `imaplib`, or `poplib`, invoking `wget` or
  `ffmpeg`, or a runtime base image that is not Alpine.
- **`check_claim_coverage`** (PR job, release hard-fail): fails if a VEX `not_affected` claim
  has no guard in the registry, a guard has no claim, or a justification does not match its
  guard family. It keeps the VEX and the guard registry in bijection.
- **`check_image_claims`** (daily Grype scan as SARIF, release hard-fail): fails if the built
  image's SBOM contradicts an image-scoped claim, for example a package that should be absent
  is present, or is below the minimum version a claim relies on.
- **`check_stale_claims`** (daily Grype scan as SARIF, non-blocking): flags VEX entries Grype no
  longer reports for the image, so obsolete suppressions get pruned. Staleness never blocks a
  release: a suppressed CVE that Grype stops reporting has been fixed upstream, which does not
  make the image less safe, so it is surfaced for cleanup rather than gated.

In the daily scan `check_image_claims` and `check_stale_claims` run in SARIF mode: drift surfaces
in Code scanning without failing the workflow. In the release the first three hard-fail, so a
false source, coverage, or image claim stops the image from being tagged, signed, or attested;
staleness is left to the daily scan.

## Reporting a security issue

To report a vulnerability privately, use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
feature for this repository.
