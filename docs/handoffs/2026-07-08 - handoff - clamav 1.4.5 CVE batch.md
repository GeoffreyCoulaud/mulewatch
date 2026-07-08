# Handoff: ClamAV 1.4.5 CVE batch (verifier image)

## Current state

Branch `fix/clamav-1.4.5-cve-batch`, one commit (`28fcfdd`), full gate green
(ruff, format, mypy over 316 files, sqlfluff, templates, all four test suites:
239 + 990 + 176 + 73). PR not yet opened at time of writing.

## What triggered this

The daily Grype scan (`grype-scan.yml`) raised **42 open Code scanning alerts**
on the **verifier** image: 7 distinct ClamAV CVEs, each duplicated across the 6
`clamav*` apk sub-packages (`clamav`, `clamav-clamdscan`, `clamav-daemon`,
`clamav-libs`, `clamav-scanner`, `freshclam`).

The 7 CVEs are the July 2026 ClamAV security batch fixed in **ClamAV 1.4.5**:
CVE-2026-20213 through -20217, plus -20243 and -20244. They are real defects in
`clamscan`'s file parsers (PE/Aspack overflow, FSG underflow, InstallShield and
ALZ archive bugs). The verifier runs `clamscan` on untrusted files pulled from
eMule, so they are squarely in our execute path. **Fix-not-VEX**: an upstream
fix exists and the code is reachable, so claiming `not_affected` would be
dishonest. Alpine 3.24 packages the fix as `clamav 1.4.5-r0` (community repo).

## What was built

- `packages/verifier/Dockerfile`: `apk add ... "clamav>=1.4.5-r0" ...` (was bare
  `clamav`). Comment block translated to English with the rationale.
- `security/verifier.vex.openvex.json`: the CVE-2016-1405 impact statement no
  longer names the exact version ("ships ClamAV 1.4.4" -> "ships ClamAV >= 0.99")
  so it never needs bumping again.
- The `freshclam` sidecar (`clamav/clamav:1.4`) was intentionally left untouched
  (third-party, not scanned by us, only runs `freshclam` never `clamscan`, and
  its floating tag picks up 1.4.5 on redeploy).

## Learned pitfalls (the non-obvious part)

1. **"Just relaunch the Grype scan" does nothing.** `grype-scan.yml` is a reader:
   it pulls the *already published* `:latest` SBOM (still `clamav 1.4.4-r0`) and
   diffs it against the vuln DB. Re-running it reports the same 42 alerts. The
   image has to be rebuilt+republished first.
2. **A plain rebuild also keeps 1.4.4.** The build (`validate.yml` ->
   `docker-image` action) uses `cache-from/to: type=gha`. The `FROM
   python:3.14-alpine@sha256:2673...` digest is already the *latest* tag, and the
   `RUN apk add ...` line was unchanged, so buildx treats the layer as a cache
   hit and re-serves the old `clamav 1.4.4-r0` layer. The `--no-cache` on the RUN
   is apk's *index* cache, unrelated to the buildx *layer* cache. Flooring the
   apk constraint (`>=1.4.5-r0`) changes the RUN string -> busts the layer cache
   -> forces a fresh apk resolve to the fixed package.
3. **The clamav version is a function of build *time*, not the base digest.**
   `apk add clamav` fetches the current Alpine 3.24 package at build; the pinned
   base digest only fixes the pre-baked layers. So there is no base-digest bump
   to make here (already latest); the lever is invalidating the apk layer.

## Next step (finish the rollout)

1. Open the PR, wait for `validate / gate` green, merge (squash/rebase, linear
   history required).
2. The merge to `main` fires `release.yml` -> rebuilds and republishes the
   verifier `:latest` with `clamav 1.4.5-r0`. **Wait for that run to finish.**
3. **Then** manually trigger the Grype scan: `gh workflow run grype-scan.yml`
   (it has `workflow_dispatch`). The rebuilt `:latest` now shows 1.4.5-r0 -> the
   42 alerts auto-transition to *fixed*. Confirm with
   `gh api repos/{owner}/{repo}/code-scanning/alerts -q '[.[]|select(.state=="open")]|length'`.

## Not yet validated against real hardware

- The rebuilt image actually shipping `clamav 1.4.5-r0` and the 42 alerts
  closing: only observable after merge -> release -> scan. Could not build the
  image locally.
- The `"clamav>=1.4.5-r0"` apk constraint resolving in CI's Alpine 3.24 build.
  It is expected to (Alpine 3.24 serves 1.4.5-r0, confirmed via the Alpine
  package index and Grype's `alpine:3.24` fix record), but the first proof is the
  `validate` build in the PR.

## Follow-up idea (not in this PR)

Geoffrey noted the apk floor is not a full pin, so builds stay non-reproducible.
Making them reproducible would mean pinning *every* apk dep (ffmpeg, libseccomp
too) to a frozen Alpine snapshot, a separate initiative. Left as a possible
future issue "reproducible apk builds".
