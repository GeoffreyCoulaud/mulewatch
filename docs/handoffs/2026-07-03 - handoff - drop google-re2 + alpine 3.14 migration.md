# Handoff — drop google-re2 + migrate base images to Python 3.14 Alpine

> Spec + plan: `docs/specs/2026-07-03-drop-google-re2-alpine-migration.md`,
> `docs/plans/2026-07-03-drop-google-re2-alpine-migration.md`.
> **Lot A merged to `main`** (local FF). **Lot B on branch `chore/alpine-3.14-migration`** —
> ready to integrate. No tag (packaging/tooling, not a versioned subsystem).

## Goal

Reduce the images' CVE surface by moving the Docker bases to `python:3.14-alpine` (musl),
digest-pinned. This was blocked by `google-re2` (no musllinux wheels, and itself a native C++
dep), so the matching engine was moved to the stdlib `re` module first.

## Current state

- **Lot A (merged to `main`, gate green):** `google-re2` → stdlib `re`. `main` is 3 commits ahead
  of `origin/main`, **not pushed** (operator chose local merge); Lot B adds 5 more once integrated.
- **Lot B (branch `chore/alpine-3.14-migration`, gate green):** 5 commits — Python 3.14 bump, the
  three Dockerfiles → Alpine, a verifier seccomp fix. Full gate green on Python **3.14.6**
  (matching 220 / crawler 731 / verifier 176 / webui 105, all 100% branch; ruff/format/mypy/
  sqlfluff/check_templates OK).
- **All 3 images × 2 arches built AND smoked locally** (amd64 native + arm64 via Docker Desktop
  QEMU): crawler, webui, verifier. Verifier amd64 proves the **seccomp blocklist is active**
  (`socket()` → EPERM under `--security-opt no-new-privileges`).

## What was built

**Lot A — matching engine (`packages/matching`):**
- `re2.compile(p)` → `re.compile(p, re.ASCII)` in `matchers.py` and `validation.py`; `re2.escape` →
  `re.escape` in `interpolation.py`. `re.ASCII` mirrors RE2's ASCII `\b \d \s \w` default so the
  **golden corpus stays green with no fixture change** (parity proof). `re.error` replaces
  `re2.error`; the compile-check message is now "regex not compilable".
- Removed `google-re2` from `packages/matching/pyproject.toml`, the `re2` mypy override from root
  `pyproject.toml`, and regenerated `uv.lock`.
- Retired RE2 as an invariant in `CLAUDE.md` (Architecture + Gotchas) and cross-referenced the MVP
  design spec §8.2. **Consequence:** lookaround/backreferences are now syntactically permitted in
  `matcher.yml` (loosening); **no anti-ReDoS guardrail** — accepted residual risk (operator-owned,
  reviewed config; attacker controls the filename, never the pattern).

**Lot B — Python 3.14 + Alpine:**
- Whole project bumped to **Python 3.14, floor `>=3.14`**: `.python-version`, root mypy
  `python_version`, the four packages' `requires-python`, lock re-resolved (dropped cp312/cp313
  wheels, −307 lines). Dev/CI/lint/runtime now all 3.14 → tests validate the shipped version.
- Three Dockerfiles → `python:3.14-alpine` + `ghcr.io/astral-sh/uv:python3.14-alpine`,
  **digest-pinned on the multi-arch index** (amd64 + arm64):
  - `python:3.14-alpine` → `sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92`
  - `uv:python3.14-alpine` → `sha256:e852e37cfaffb287f2d78de0d4f27e28bf0722ccbf0d6948dbdf19a0b4de7bc7`
- `verifier`: `apt-get … ffmpeg clamav libseccomp2` → `apk add --no-cache ffmpeg clamav libseccomp`.
- libseccomp discovery on musl handled at the **environment layer**: a `sitecustomize.py` on
  `PYTHONPATH` in the verifier image (`packages/verifier/docker/sitecustomize.py`) points
  `ctypes.util.find_library("seccomp")` at the soname at interpreter startup. `confine.py` stays
  free of the workaround and only adds `RuntimeError` to its fail-open catch.

## Learned pitfalls (Alpine / musl)

1. **`google-re2` has no musllinux wheels** — ever. That was the whole blocker. Every other runtime
   dep is fine on musl: `rapidfuzz` and `pyyaml` ship cp314 musllinux wheels; `pyseccomp` is
   `py3-none-any`. So **no C toolchain is needed in the Alpine builder**.
2. **Alpine occupies GID 999** with the (unused-here) `ping` group → `groupadd/addgroup -g 999`
   fails with "gid in use". The compose hardening hardcodes `user: "999:999"`, so we must keep
   999:999. Fix: `delgroup ping 2>/dev/null;` before `addgroup -S -g 999 nonroot`.
3. **busybox user tools**: `addgroup`/`adduser` (not `groupadd`/`useradd`); `-h /home/nonroot`
   reproduces `--create-home`.
4. **musl breaks `ctypes.util.find_library`**: it resolves via gcc/ld (absent from the minimal
   image) and returns `None` even though `/usr/lib/libseccomp.so.2` is present and
   `ctypes.CDLL("libseccomp.so.2")` works. `pyseccomp` calls `find_library("seccomp")` at import →
   raised `RuntimeError` (previously **uncaught** in `ProdConfiner` → a crash, not the documented
   fail-open). Fix = an image-level `sitecustomize.py` (on `PYTHONPATH`) that points find_library at
   the soname at startup — an environment concern kept out of the app code — plus catching
   `RuntimeError`. Installing `binutils` + `libseccomp-dev` also makes find_library work natively but
   was **rejected** (adds a linker toolchain to the runtime = against the CVE-reduction goal).
   Verified active: `socket()` → EPERM under `no-new-privileges`.
5. **`.python-version` drives the Docker interpreter** after `COPY . /app`: the first `uv sync`
   (bind-mounted files, no `.python-version`) used the image's Python; the second (post-COPY) failed
   on the `3.12` pin (`UV_PYTHON_DOWNLOADS=0`). Bumping the file to `3.14` aligned it.
6. **apk installs no "recommends"** — the old `--no-install-recommends` intent is the default.

## Suggested next step

1. Integrate Lot B (`finishing-a-development-branch`): local merge to `main` or a PR (a PR lets CI
   run the multi-arch build + smoke on **native** arm64 runners, which is the real validation the
   sandbox can't give).
2. Push `main` (Lot A + Lot B) when ready — currently unpushed.
3. Run the on-demand integration suites on real hardware (see below).

## Validation status

**Green on CI** (PR #11, `validate.yml`, run 28633458280): lint + the 4 unit suites on Python 3.14,
the **3 images × 2 arches built on native runners** (arm64 native, not emulated), and
**`compose_integration` (assembled-stack smoke) passing on native amd64 AND arm64**. So the Alpine
3.14 stack assembles and runs on both published arches.

**Still NOT validated:**
- **`analysis_integration`** — the authoritative verifier check: real per-file confined analysis
  (seccomp `load()` on a real kernel + `ffprobe`/`clamscan` on actual media). Not in CI; run on
  hardware via `!`. Locally I proved seccomp *active* on amd64 (`socket()` → EPERM under
  `no-new-privileges`); on arm64 the filter only *constructs* under QEMU (`load()` not exercised).
- **Production deployment** on a real kernel/host (VPN, volumes, freshclam sidecar ↔ clamav DB
  volume interplay — unchanged but not re-tested e2e).

## Observation (follow-up, out of scope)

webui unit tests emit ~165 `ResourceWarning: unclosed database` (sqlite connections not closed in
test fixtures) — pre-existing test hygiene, surfaced more under Python 3.14; **non-blocking** (gate
green). Candidate cleanup: close connections in the webui fixtures.
