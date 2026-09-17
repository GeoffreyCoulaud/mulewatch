# VEX maintainability guardrails

> Date: 2026-07-07. Companion to the supply-chain artefact posture
> (`2026-07-07-supply-chain-attestation-vex.md`). This spec covers (a) authoring the first
> real OpenVEX triage for both images and (b) the automated guarantees that keep those
> `not_affected` assertions honest over time.

## 1. Motivation

The first Grype scan of the two published images surfaced 11 distinct advisories (8 on
`mulewatch-crawler`, 11 on `mulewatch-verifier`, overlapping on the shared Alpine base and
CPython interpreter). Each was triaged against primary sources and found **not exploitable in
this deployment**, for a concrete, verifiable reason (module not used, binary absent, encoder
never invoked, Windows-only bug on a Linux image, or a decade-old bug long fixed upstream).

A signed `not_affected` VEX statement is a **claim** we make to the scanner: ignore this CVE,
here is why. A claim can rot silently in four concrete ways, and this spec adds one automated
check per way (section 6):

- its reason becomes false in **our code** (we start using code we said we never touch);
- its reason becomes false in the **built image** (a package we said is absent reappears, or a
  patched-version floor regresses);
- it **outlives its finding** (the scanner stops reporting the CVE, leaving a dead entry);
- it exists **without a check** behind it (a claim was added but its guard was not).

Each check is its own executable and its own CI step, so a red step names the exact failure. The
honesty is self-policing rather than dependent on memory.

## 2. Scope

In scope:

1. Author the 19 OpenVEX statements (8 crawler + 11 verifier) into the two existing, currently
   empty `security/*.vex.openvex.json` files.
2. Build the guardrail tooling as a fourth workspace package `packages/vex_guards/`: a guard
   registry and four checks (`check_source_claims`, `check_claim_coverage`, `check_image_claims`,
   `check_stale_claims`), plus a shared VEX/SBOM loader, an injectable Grype runner, and a SARIF
   emitter.
3. Wire the guardrails into CI: a **non-blocking** PR check, a SARIF surface in the daily
   Grype scan, and a **hard-fail** gate at release (before attestation).

Out of scope (non-goals):

- Making any of this a required merge gate. Vulnerability state is externally driven; a new
  CVE or a Grype DB update must never block an unrelated PR. See section 10.
- A strict "every Grype finding must be triaged" policy (would break CI on every new CVE).
- Anti-ReDoS or other posture changes unrelated to the VEX lifecycle.
- Transitive-dependency import auditing. The source checks are scoped to our own application
  source, matching the scope of the VEX claim (see section 4, honesty caveat).

## 3. The VEX statements (data)

Both files are image-scoped OpenVEX v0.2.0. Products are `pkg:oci/mulewatch-crawler` and
`pkg:oci/mulewatch-verifier`. Subcomponents are the vulnerable package PURLs **without a
version** (so a statement survives package bumps), per `SECURITY.md`.

`mulewatch-crawler` (8 statements) and `mulewatch-verifier` (11 statements = the same 8 plus
clamav, nghttp2, rand). The shared 8: the 7 CPython interpreter CVEs plus the busybox one.

| CVE / ID | Image(s) | Status | Justification | Subcomponent PURL(s) |
|---|---|---|---|---|
| CVE-2026-11940 | both | not_affected | vulnerable_code_not_in_execute_path | `pkg:generic/python` |
| CVE-2026-11972 | both | not_affected | vulnerable_code_not_in_execute_path | `pkg:generic/python` |
| CVE-2026-4360 | both | not_affected | vulnerable_code_not_in_execute_path | `pkg:generic/python` |
| CVE-2026-0864 | both | not_affected | vulnerable_code_not_in_execute_path | `pkg:generic/python` |
| CVE-2025-15366 | both | not_affected | vulnerable_code_not_in_execute_path | `pkg:generic/python` |
| CVE-2025-15367 | both | not_affected | vulnerable_code_not_in_execute_path | `pkg:generic/python` |
| CVE-2026-12003 | both | not_affected | vulnerable_code_not_in_execute_path | `pkg:generic/python` |
| CVE-2025-60876 | both | not_affected | vulnerable_code_not_in_execute_path | `pkg:apk/alpine/busybox`, `pkg:apk/alpine/busybox-binsh`, `pkg:apk/alpine/ssl_client` |
| CVE-2016-1405 | verifier | not_affected | vulnerable_code_not_present | `pkg:apk/alpine/clamav`, `-clamdscan`, `-daemon`, `-libs`, `-scanner`, `pkg:apk/alpine/freshclam` |
| CVE-2026-58055 | verifier | not_affected | vulnerable_code_not_present | `pkg:apk/alpine/nghttp2-libs` |
| GHSA-cq8v-f236-94qc | verifier | not_affected | vulnerable_code_not_in_execute_path | `pkg:cargo/rand` |

Each statement carries a short `impact_statement` recording the concrete reason:

- **tarfile (11940, 11972, 4360):** the application never imports `tarfile` and never extracts
  archives; on the verifier, the confined child runs only `ffprobe` and `clamscan` as argv
  subprocesses, with no Python-side extraction.
- **configparser (0864):** configuration is operator-owned YAML; `configparser` is never used
  and no config value is written from untrusted input.
- **imaplib (15366) / poplib (15367):** the module is not used by the application.
- **VPATH (12003):** the advisory affects Windows installs only; these images are built on
  Alpine Linux, where the VPATH landmark escape does not apply.
- **busybox wget (60876):** the busybox `wget` applet is present but the application never
  invokes it; an exploitable request-target injection requires a `wget` call built from untrusted
  input, which our source never makes.
- **clamav (2016-1405):** the 2016 libclamav OLE2 defect was fixed upstream in ClamAV 0.99
  (Debian fixed `0.99+dfsg-1`); this image ships ClamAV 1.4.4, a decade past the fix. The NVD
  record uses an unbounded `clamav:clamav:*` CPE that over-matches modern releases.
- **nghttp2 (58055):** the flaw is in the `nghttpx` proxy binary (upstream fix touches only
  `src/shrpx_*`, zero `lib/` code); this image ships only `libnghttp2.so` (`nghttp2-libs`), not
  the `nghttpx` binary (the `nghttp2` package).
- **rand / rav1e (cq8v):** the `rand` crate is embedded in `librav1e` (AV1 encoder) pulled by
  ffmpeg; the verifier runs only `ffprobe` (analysis), never the AV1 encoder, so the affected
  code path is never executed.

**Authoring mechanics.** The files are hand-authored valid OpenVEX v0.2.0 (deterministic, no
toolchain install) matching the schema `vexctl` produces, so a future `vexctl add` appends
cleanly. `SECURITY.md` keeps `vexctl` as the documented human workflow. Each statement carries
a `timestamp` (the authoring date) and the document carries `@id`, `author`, `version`.

**Empirical suppression check (mandatory in Act).** The exact subcomponent PURL form Grype
matches on (base PURL vs. one carrying `?arch=&distro=` qualifiers) is verified by re-running
Grype against each image's SBOM with the VEX applied and confirming every finding is
suppressed. The base PURLs above are the intent; the Act phase reconciles them against Grype's
actual matching and records the outcome.

## 4. Guard registry

One file in the tooling package, `packages/vex_guards/src/vex_guards/registry.py` (NOT the
attested OpenVEX; we never add non-standard fields to the signed artefact). It maps each VEX
vulnerability id to exactly one declarative guard descriptor:

```python
GUARDS: dict[str, Guard] = {
    # not_in_execute_path  ->  source family, evaluated offline over packages/*/src
    "CVE-2026-11940": ModuleNotImported("tarfile"),
    "CVE-2026-11972": ModuleNotImported("tarfile"),
    "CVE-2026-4360":  ModuleNotImported("tarfile"),
    "CVE-2026-0864":  ModuleNotImported("configparser"),
    "CVE-2025-15366": ModuleNotImported("imaplib"),
    "CVE-2025-15367": ModuleNotImported("poplib"),
    "CVE-2025-60876": BinaryNotInvoked("wget"),
    "GHSA-cq8v-f236-94qc": SubprocessDenies("ffmpeg"),  # AV1 encoder never run; only ffprobe
    "CVE-2026-12003": BaseImageIsAlpine(),              # Windows-only bug; we build on Alpine

    # not_present  ->  image family, evaluated against the syft-json SBOM
    "CVE-2026-58055": PackageAbsent("nghttp2"),         # ships nghttpx; we carry only nghttp2-libs
    "CVE-2016-1405":  PackageMinVersion("clamav", "0.99"),  # OLE2 bug fixed upstream in 0.99
}
```

Descriptors are pure data; generic evaluators interpret them. Adding a future guard is one line
(and, rarely, a new descriptor type + evaluator arm).

**Honesty caveat (documented, not hidden).** The `not_in_execute_path` guards scan our own
application source (`packages/*/src`), not the dependency closure in the venv. This matches the
scope of the VEX claim: exploitation requires attacker-controlled input to reach the vulnerable
API, and our data flows do not route untrusted input into these modules. A dependency importing
`tarfile` internally does not make the claim false unless our flows feed it a malicious archive,
which they do not.

## 5. Descriptor semantics

Source family (evaluated by `check_source_claims`, offline):

- **`ModuleNotImported(module)`**: AST-parse every `.py` under `packages/*/src`; fail if any
  `import module`, `from module import ...`, `import module as ...`, or a string-literal
  argument to `importlib.import_module` / `__import__` naming the module is found.
- **`BinaryNotInvoked(name)`**: scan `packages/*/src` for an invocation of `name`; fail if it
  appears. Used for `wget`. Scoped to our application source on purpose: an exploitable call (a
  `wget` command built from untrusted input) can only originate there. A container healthcheck or
  entrypoint uses a fixed local URL, not the attacker-controlled request-target the CVE needs, so
  deployment artifacts are out of scope.
- **`SubprocessDenies(name)`**: scan the verifier source for a subprocess invocation whose
  program is exactly `name`; fail if present. Used for `ffmpeg` (the encoder). `ffprobe` and
  `clamscan` remain allowed; `ffmpeg` is not a substring of `ffprobe`, so no false match.
- **`BaseImageIsAlpine()`**: parse the two Dockerfiles' runtime `FROM`; fail if the base does
  not reference Alpine. Guards the Windows-only VPATH statement structurally.

Image family (evaluated by `check_image_claims`, against a syft-json SBOM):

- **`PackageAbsent(name)`**: fail if the SBOM contains an apk artifact whose name is exactly
  `name`. `nghttp2-libs` is a different name, so it does not trip `PackageAbsent("nghttp2")`.
- **`PackageMinVersion(name, minimum)`**: fail if any apk artifact named `name` has a version
  below `minimum`, using **semantic** version comparison (`packaging.version.Version`), never a
  string compare (`"1.4.4" >= "0.99"` must be true).

## 6. The four checks

A VEX `not_affected` entry is a **claim**. A claim can rot in four concrete ways, along two axes:
the evidence that would contradict it (our **repo** vs the built **image**), and whether the
check is a per-claim predicate or a whole-set comparison. Each cell is one check, one executable,
one CI step, so a red step names the failure.

|  | per-claim predicate | whole-set comparison |
|---|---|---|
| reads the **repo** | `check_source_claims` | `check_claim_coverage` |
| reads the **image** | `check_image_claims` | `check_stale_claims` |

The semantic axis (predicate vs set) crosses the evidence axis (repo vs image), so the checks can
only be grouped for execution by evidence: the two repo checks run where no image exists (PR +
release), the two image checks run where the built image's SBOM is available (daily scan +
release). Grouping by semantics would produce an executable that needs the image at PR time,
where it does not exist.

**`check_source_claims`** (repo, predicate). Evaluates every source-family guard (section 5)
against `packages/*/src` and the Dockerfiles' `FROM`. **Fails when** our code imports or invokes
an excluded API (`tarfile`, `imaplib`, `poplib`, `configparser`, `wget`, the `ffmpeg` encoder) or
the runtime base image is no longer Alpine. Reads only the repo. Runs at PR and release.

**`check_claim_coverage`** (repo, set). Asserts `set(GUARDS) == set(not_affected entries across
both VEX files)`, and that each guard's family matches its statement's justification (source
family ↔ `vulnerable_code_not_in_execute_path`; image family ↔ `vulnerable_code_not_present`).
**Fails when** a claim has no guard, a guard has no claim, or a family mismatch. Reads only the
repo. Runs at PR and release.

**`check_image_claims`** (image, predicate). Evaluates every image-family guard against the
image's syft-json SBOM. **Fails when** a package declared absent reappears (`PackageAbsent`) or a
patched-version floor regresses (`PackageMinVersion`): e.g. the `nghttp2` package (which ships
`nghttpx`) becomes installed, or clamav drops below 0.99. Reads the built image. Runs at daily
scan and release.

**`check_stale_claims`** (image, set). Runs Grype on the SBOM **without** the VEX to get the raw
reported set `R`, and asserts every VEX entry is in `R`. **Fails when** a VEX entry references a
CVE Grype no longer reports (a stale claim). Independent of the guard registry: pure VEX vs
scanner. Reads the built image. Runs at daily scan and release.

**Which VEX the image checks read.** `check_image_claims` and `check_stale_claims` take the VEX as
input (`--vex`), so the caller decides which one, always against that image's own SBOM: the daily
scan passes the image's own **attested** VEX; the release passes the **repo** VEX it is about to
attest. Each is self-consistent (a VEX compared to Grype's findings on the same image), which
removes any repo/release-lag false positive.

## 7. How the checks report

The two **repo** checks (`check_source_claims`, `check_claim_coverage`) have only a pass/fail exit
code: visible-but-non-blocking on PRs, hard-fail at release.

The two **image** checks (`check_image_claims`, `check_stale_claims`) report differently by
context:

- **Daily scan (surface):** each emits a SARIF 2.1.0 document (`sarif.py`) with a `vex-consistency`
  tool driver and its own rule (`unsatisfied-image-claim` / `stale-vex-entry`), one result per
  violation, each locating `security/<image>.vex.openvex.json`. Uploaded to Code scanning; the
  check exits 0, so the scan never fails. A clean run uploads an empty SARIF, which auto-resolves
  that rule's prior alerts.
- **Release (block):** each runs in fail mode; any violation exits non-zero and stops the release
  before attestation, so an inconsistent VEX is never attested.

## 8. CI wiring

- **`.github/workflows/pr.yml` (modify).** Add a `vex-checks` job alongside the existing
  `validate` job (which calls the gate). Two steps, one per repo check (`check_source_claims`,
  `check_claim_coverage`), each its own step so a red step names the failure. **No path filter:**
  the checks are cheap and stay green on PRs that touch neither our source nor the VEX; filtering
  one job would need a third-party action, and a workflow-level `paths:` filter is impossible here
  because it would also gate the required `validate` job (a required check that never runs blocks
  the PR). The job is **not** the required `validate / gate` check, so red is visible on the PR but
  does **not** block merge. The repo checks also run at release (hard-fail, on every `main` push),
  so `main` is covered without a `push` trigger here.
- **`.github/workflows/grype-scan.yml` (modify).** Add `actions/checkout` (the image checks need
  the repo registry). After the existing SBOM+VEX extraction, add two steps, `check_image_claims`
  and `check_stale_claims`, each in SARIF mode against the extracted (attested) SBOM and the
  extracted **attested** VEX, each `upload-sarif` under its own category
  (`vex-image-claims-${{ matrix.package }}`, `vex-stale-claims-${{ matrix.package }}`). The main
  Grype SARIF upload keeps `if: always()` so findings still land. Job stays green.
- **`.github/workflows/release.yml` (modify).** In `publish-manifest`, after generating the
  syft-json SBOM and **before** the attest step, run all **four** checks as four hard-fail steps:
  the two repo checks (the repo is already checked out) and the two image checks in fail mode
  against the fresh SBOM and the **repo** VEX about to be attested (needs Grype; run
  `anchore/grype` via its container on the SBOM). Any failure stops the release before
  attestation.

## 9. Placement, tooling, testing

- `vex_guards` is a **fourth uv workspace member**, `packages/vex_guards/` (package
  `vex_guards`, dist `vex-guards`), mirroring the existing packages' `pyproject.toml`
  (build-system, its own `[tool.pytest.ini_options]` + coverage config). It is registered in the
  root: `[tool.uv.sources]` (`vex-guards = { workspace = true }`) and the dev dependency group,
  so it installs into the dev environment and `python -m vex_guards.*` just works. No shipped
  package imports it, so the package-boundary invariant is unaffected.
- **100% branch coverage**, TDD, negative-path mandatory: every guard descriptor has a positive
  test (current tree/SBOM passes) and a negative test (an injected violation fails). Its suite
  runs in the per-package gate: `poe test` gains a fourth entry
  (`{ cmd = "pytest", cwd = "packages/vex_guards" }`). These tests are deterministic (fixtures:
  fake source trees, fake syft-json SBOMs, sample Grype output, sample VEX), so blocking on them
  is correct.
- Root `ruff` (`[tool.ruff].src`) and `mypy --strict` (`[tool.mypy].files`) extend to
  `packages/vex_guards/{src,tests}`; both remain in the gate (deterministic).
- Only the **four live checks** (`python -m vex_guards.check_*`, run in CI) stay out of
  `poe check`; the two repo checks are also exposed as `poe` tasks for local use. The subprocess
  call to Grype (in `check_stale_claims`) goes through an **injectable runner** (prod = real
  `subprocess`; tests = a fake), the same seam the verifier uses for `ffprobe`/`clamscan`, so the
  suite reaches 100% without Grype installed.
- **Cost accepted for now:** as a workspace member, its `pyproject.toml` must be present for
  `uv sync --locked`, so both image Dockerfiles gain a bind-mount for
  `packages/vex_guards/pyproject.toml` in their dependency layer (the package is never installed
  into the images: it is not named in any `uv sync --package`). Removing this by moving the
  dependency layer to `uv sync --frozen` (per uv's Docker guidance, which also drops the
  pre-existing sibling-pyproject mounts) is a tracked follow-up, recorded in the handoff and
  deliberately out of scope here to avoid changing the proven prod builds in the same change.

## 10. Non-blocking posture (decision)

Vulnerability state changes for reasons unrelated to any given PR (a new CVE is published, the
Grype DB is updated, a base image is rebuilt). Coupling merges to that state would let external
events block unrelated work. Therefore:

- **PR:** the two repo checks run as a non-required `vex-checks` job in `pr.yml`, visible but
  **not** a required check. Nothing blocks merge.
- **Daily scan:** the two image checks surface violations as SARIF alerts; the scan never fails.
- **Release:** the only hard gate. Blocking a deliberate publish on an inconsistent VEX is
  correct and does not impede routine development.

## 11. Decisions traced

- **Keep everything** (reachability + bijection + stale + `not_present` image predicates +
  SARIF), rather than a lean core. The optional layer (`not_present` predicates) is small
  (~20-line SBOM evaluator + `packaging.Version`) and completes the guarantee uniformly.
- **clamav as `PackageMinVersion("clamav", "0.99")`, not a documented exemption.** The min
  version faithfully encodes the justification (fix landed in 0.99, we ship 1.4.4) and trips on
  a downgrade below 0.99, mechanizing the re-triage trigger. No exemption allowlist is needed;
  the bijection stays total with zero manual carve-outs.
- **Stale surfaced via SARIF** in the daily scan (consistent with the existing findings-as-SARIF
  posture), and hard-failed only at release.
- **Guards live in a Python registry, not inside the OpenVEX files.** The attested artefact
  stays standard OpenVEX; the guard mapping is repo-owned tooling.
- **grype-scan now checks out the repo** (previously it checked nothing out). Justified: the
  consistency check needs the repo registry + VEX as the source of truth.
- **`vex_guards` is a fourth workspace member, not code in `security/`** (which holds only the
  signed VEX artefacts). It gets the project's full per-package test + coverage + type rigor,
  and its unit tests land in the required gate as deterministic checks. The price is a bind-mount
  of its `pyproject.toml` in both Dockerfiles' dependency layer (uv requires every member's
  `pyproject.toml` present for `uv sync --locked`; this is also why `crawler`'s build already
  binds `verifier`'s). Accepted for now; the `uv sync --frozen` cleanup that removes it (and the
  pre-existing sibling mounts) is deferred to a follow-up session and recorded in the handoff.
  AGENTS.md moves from three packages to four.
- **Four checks, one per 2x2 cell, not two combined runners.** Each check is its own executable
  and CI step, so a red step names the exact failure. The semantic axis (per-claim predicate vs
  whole-set comparison) crosses the evidence axis (repo vs image), so the only decomposition where
  each check runs in a single CI context is by evidence source; that is why the two image checks
  share the daily-scan/release context though they are semantically distinct.
- **Repo checks run as a non-required job in `pr.yml`; image checks never run on PRs.** The two
  repo checks are a `vex-checks` job in `pr.yml`, non-required (does not block merge), with no path
  filter: they are cheap and stay green on unrelated PRs, filtering one job would need a
  third-party action, and a workflow-level filter would wrongly gate the required `validate` job.
  The two image checks are SBOM-sensitive, but a PR-time SBOM build was judged unnecessary: a
  dependency bump that breaks them is surfaced by the daily scan and hard-blocked at release.

## 12. Not validated until real CI (structural)

- The release-time hard-fail path only runs on a `main` push / `v*` tag, not on PRs, so the
  first real exercise is the next release after merge.
- The SARIF surface in `grype-scan.yml` is first exercised by a manual `workflow_dispatch` of
  that workflow after merge. Confirm: a clean run uploads empty `vex-image-claims-*` and
  `vex-stale-claims-*` SARIF and no alert appears; an injected stale entry produces exactly one
  alert under `vex-stale-claims-*`.
- The empirical subcomponent-PURL suppression check (section 3) is done locally in Act against
  the real images before the PR; the CI only re-confirms it.
