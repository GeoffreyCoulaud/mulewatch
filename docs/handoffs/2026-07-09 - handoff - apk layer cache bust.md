# Handoff: automatic apk-layer cache-bust (verifier image)

Branch `fix/apk-layer-cache-bust`, full gate green, verified with real `docker build`s
(Docker Desktop, amd64). Ready to push + PR. Touches `deploy`/CI, so it goes through a PR.

## What this replaces

The `2026-07-08` handoff floored `clamav>=1.4.5-r0` in the verifier Dockerfile purely to
change the `RUN` string and bust the buildx layer cache, so a rebuild would re-run `apk` and
pick up the July 2026 CVE-batch fix. That was manual toil: one version bump per CVE, per
package, and only for the package you remembered to floor. This handoff removes that floor and
replaces it with a generic mechanism that busts the `apk add` layer whenever the resolved
package versions change, for **any** of the runtime packages, automatically.

## The mechanism (Option B: probe + version fingerprint)

Docker never re-invalidates a `RUN` on its own; with the CI `cache-from: type=gha` cache, an
unchanged `apk add` string re-serves the stale layer forever (that was the bug). We inject an
external signal:

- **`packages/verifier/Dockerfile`** now has a light `apk-plan` stage that runs
  `apk add --no-cache --simulate $APK_PACKAGES | grep -i installing | sort | sha256sum`
  into `/apk-plan.sha256`. `--simulate --no-cache` fetches only the **live** Alpine index and
  resolves the full closure **without downloading packages**. That fingerprint is `COPY`ed into
  the runtime stage right before the real `apk add`, so the expensive install layer busts **iff**
  the resolved version set changed (an upstream fix, a new transitive dep).
- **`ARG APK_PROBE_NONCE`** (passed unique per build) forces the cheap probe to re-run every
  build, so it always re-resolves the live index. The expensive install is gated purely by the
  fingerprint content, not the nonce, so an unchanged version set stays `CACHED`.
- **`ARG APK_PACKAGES="ffmpeg clamav libseccomp"`** is a global ARG (declared before the first
  `FROM`, re-declared in the probe and runtime stages). The probe and the install both reference
  `$APK_PACKAGES`, so the fingerprinted list and the installed list **cannot** drift: it is one
  variable, not two lists to keep aligned.
- **CI wiring:** `.github/actions/docker-image/action.yml` gained a `build-args` passthrough;
  `.github/workflows/validate.yml` sets `APK_PROBE_NONCE=${{ github.run_id }}-${{ github.run_attempt }}`
  on the **verifier** build only (the crawler has no `apk add`, so passing it there would warn
  "build arg not consumed").

## Learned pitfalls

- **Any indirection on the last `FROM` breaks `vex_guards.source_scan`.** The
  `BaseImageIsAlpine` guard (claim CVE-2026-12003) reads the **last `FROM` textually** and
  requires the substring `alpine`. A `runtime-base` stage alias (and equally a shared
  `ARG RUNTIME_BASE`) makes the last `FROM` read `FROM runtime-base` / `FROM ${...}` with no
  `alpine` → the gate fails (`test_real_source_tree_has_no_source_claim_violations`). So the base
  digest is a **literal in both stages**, kept in sync by comment. We deliberately did NOT make
  the guard alias-aware (kept the PR focused; the guard's simple last-FROM design is intentional).
- **Removing the floor is safe.** `apk add --no-cache` resolves against the live index, so bare
  `clamav` already installs `1.4.5-r0` (confirmed in the control build). The VEX claim was already
  generalized to `ships ClamAV >= 0.99` and the `PackageMinVersion("clamav", "0.99")` guard still
  holds; the `1.4.4-r0` strings in `vex_guards` tests are independent fixtures, not the image.
- **buildx expands ARGs in the plain-progress step header** (`RUN apk add ... ffmpeg clamav
  libseccomp`, not `$APK_PACKAGES`) — grep the expanded form when inspecting cache status.
- `set -eo pipefail` in the probe is deliberate: fail the build **loudly** if a package vanishes
  or apk's output format drifts, rather than silently freezing the fingerprint (which would
  resurrect the stale-cache bug, invisibly).

## Verified locally (amd64, Docker Desktop 29.5.3)

- Full gate green (ruff, format, mypy 316 files, sqlfluff, templates, 239+990+176+73 tests).
- Probe re-runs on a nonce change (`apk-plan RUN ... DONE 0.6s`), index-only, no downloads.
- Nonce change alone leaves the fingerprint byte-identical → `COPY` + `apk add` install layers
  `CACHED` (no re-download).
- A package-set change (`APK_PACKAGES=... curl`) flips the fingerprint → install would bust.
- Full image builds; installs `clamav 1.4.5-r0`, `ffmpeg 8.1.2-r0`, `libseccomp 2.6.0-r2`.

## NOT yet validated against real hardware / CI

- **The `type=gha` cache path.** Local runs used the daemon's local build cache, not CI's
  `cache-from/to: type=gha`. buildx keys layers the same regardless of backend, so it should
  behave identically, but the first real CI run should confirm: verifier builds, the `apk-plan`
  probe re-runs each run, and the install layer is served from the gha cache when versions are
  unchanged.
- **arm64.** Only the native amd64 leg was built locally. The arm64 matrix leg should behave
  identically (same literal digest, a multi-arch manifest) but is unverified locally.

## Next step

Push, open the PR, wait for `validate / gate` (includes the compose smoke on both arches), then
squash/rebase-merge and tag the verifier subsystem.
