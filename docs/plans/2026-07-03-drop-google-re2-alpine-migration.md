# Drop google-re2 + Alpine 3.14 Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `google-re2` with the stdlib `re` module (behaviour-identical) in the matching engine, then migrate the three Docker base images to Python 3.14 Alpine, digest-pinned — to cut the CVE surface.

**Architecture:** Two sequenced lots sharing one spec. Lot A is a pure, behaviour-preserving refactor inside `packages/matching`, guarded by the existing golden corpus (its green state is the non-regression proof). Lot B is a `FROM`-line + `apt→apk` change across the three Dockerfiles, unblocked by Lot A (no non-musl dependency remains). Spec: `docs/specs/2026-07-03-drop-google-re2-alpine-migration.md`.

**Tech Stack:** Python 3.12+ (images target 3.14), `uv` workspace, `re` (stdlib), `mypy --strict`, `ruff`, pytest (100% branch), Docker (Alpine, multi-arch, digest-pinned).

## Global Constraints

- **100% BRANCH coverage per package**, gated (`--cov-fail-under=100`, `branch=true`). Integration suites stay deselected/excluded. Never lower the threshold.
- **`mypy --strict`** over **both `src` and `tests`**. **`ruff`** selects `E,F,I,UP,B,SIM`, line-length **100**; `ruff format`.
- **All code in English** — identifiers, comments, docstrings, runtime messages, commit messages. Specs/plans/handoffs in English (decided 2026-07-03).
- **Conventional commits** (`refactor(...)`, `chore(...)`, `docs(...)`).
- **Behaviour-identical swap**: the golden corpus must stay green **with no fixture change**. Compile every regex with `re.ASCII` to mirror RE2's ASCII default for `\b \d \s \w`.
- **No anti-ReDoS guardrail** (accepted residual risk — operator-owned, reviewed `matcher.yml`).
- **Base images digest-pinned on the multi-arch index** (covers amd64 + arm64 under one digest).
- **`deploy/config/` is operator-owned** — `matcher.yml` is not modified by this work.
- The gate is **per package**: `( cd packages/<pkg> && uv run pytest -q )`. Full matching gate for Lot A: `( cd packages/matching && uv run pytest -q )` + root `uv run ruff check .` + `uv run mypy`.

---

## Lot A — domain: `google-re2` → `re`

### Task 1: Swap `re2` for `re` across the matching engine

Single atomic refactor (one commit): all `re2` call sites move to `re` (with `re.ASCII`), the two `re2`-referencing tests follow, and the dependency + mypy override + lockfile are cleaned up. Every intermediate commit stays green, so this is one reviewable unit.

**Files:**
- Modify: `packages/matching/src/catalog_matching/matchers.py:3,44`
- Modify: `packages/matching/src/catalog_matching/interpolation.py:5,32-40`
- Modify: `packages/matching/src/catalog_matching/validation.py:11,379-381`
- Modify: `packages/matching/tests/test_interpolation.py:2,28-29`
- Modify: `packages/matching/tests/test_validation.py:494`
- Modify: `packages/matching/pyproject.toml` (remove `google-re2`)
- Modify: `pyproject.toml` (remove the `re2` mypy override block)
- Regenerate: `uv.lock`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `RegexMatcher(pattern, flags="i")` unchanged public signature; `interpolate(pattern, target) -> str` unchanged; `validate_config(...)` unchanged. Only the internal engine (`re2` → `re`) and the `_check_regexes_compile` error message change. No import of `re2` remains anywhere.

- [ ] **Step 1: Establish the baseline — run the full matching gate, confirm green**

Run:
```bash
( cd packages/matching && uv run pytest -q )
```
Expected: PASS (this green state is the regression harness for the whole task).

- [ ] **Step 2: Swap `interpolation.py` to `re.escape`**

`import re2` is removed; the module already imports `re as _re`. Replace every `re2.escape(...)` with `_re.escape(...)`.

In `packages/matching/src/catalog_matching/interpolation.py`, delete line 5 (`import re2`) and change the body of `replace`:
```python
        if name == "season":
            return str(_re.escape(str(target.season)))
        if name == "seasonal_number":
            return str(_re.escape(str(target.seasonal_number)))
        if name == "absolute_number":
            return str(_re.escape(str(target.absolute_number)))
        if name == "segment":
            return str(_re.escape(target.segment.upper()))
        if name == "title":
            return str(_re.escape(target.title))
        if name == "mono_gate":
            return "" if target.sole_segment else r"[^\s\S]"
```
Also update the docstring: "inserted ``re2.escape``-d" → "inserted ``re.escape``-d", and "empty RE2 class" → "empty regex class".

- [ ] **Step 3: Swap `matchers.py` to `re` + `re.ASCII`**

In `packages/matching/src/catalog_matching/matchers.py`, change line 3 `import re2` → `import re`, and line 44:
```python
        self._re = re.compile(pattern, re.ASCII)
```
Update the `RegexMatcher` docstring: replace the two "RE2"/"``re2``" mentions with `re`, and note case-insensitivity is applied via the leading `(?i)` (unchanged), classes are ASCII (`re.ASCII`) to match the prior RE2 semantics. The `matches` body (`self._re.search(fold(candidate.filename))`) is unchanged.

- [ ] **Step 4: Swap `validation.py`'s compile-check to `re`**

In `packages/matching/src/catalog_matching/validation.py`, change line 11 `import re2` → `import re`, and `_check_regexes_compile`:
```python
        try:
            re.compile(pattern, re.ASCII)
        except re.error as exc:
            raise ConfigError(f"token {name!r}: regex not compilable: {exc}") from exc
```
Update the module docstring's "the RE2 compile-check" reference to "the regex compile-check".

- [ ] **Step 5: Update `test_interpolation.py`**

In `packages/matching/tests/test_interpolation.py`, change line 2 `import re2` → `import re`, and lines 28-29:
```python
    assert result == r"prefix " + re.escape("C++ (demo)") + r" suffix"
    assert re.compile(result, re.ASCII).search("prefix C++ (demo) suffix") is not None
```
(`re.escape("C++ (demo)")` == `re2.escape("C++ (demo)")` == `C\+\+\ \(demo\)`, so the assertion is unchanged in value.)

- [ ] **Step 6: Update `test_validation.py`'s error-message assertion**

In `packages/matching/tests/test_validation.py:494`, the message no longer contains "RE2":
```python
    with pytest.raises(ConfigError, match="not compilable"):
```

- [ ] **Step 7: Remove the `google-re2` dependency**

In `packages/matching/pyproject.toml`, delete the `"google-re2>=1.1.20251105",` line from `dependencies`. `rapidfuzz` stays.

- [ ] **Step 8: Remove the `re2` mypy override**

In root `pyproject.toml`, delete the block (currently lines 62-64):
```toml
[[tool.mypy.overrides]]
module = "re2"
ignore_missing_imports = true
```
Leave the `pyseccomp` and `testcontainers.*` override blocks intact.

- [ ] **Step 9: Regenerate the lockfile**

Run:
```bash
uv sync
```
Expected: `uv.lock` updated; `google-re2` (and its being the only `re2` provider) removed from the graph. Confirm:
```bash
grep -c 'name = "google-re2"' uv.lock
```
Expected: `0`.

- [ ] **Step 10: Run the full matching gate — confirm still green (parity proof)**

Run:
```bash
( cd packages/matching && uv run pytest -q )
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
Expected: PASS on all. The golden corpus (real 62a fixtures, etc.) green under `re`+`re.ASCII` is the non-regression proof. Any red here is a real semantic divergence — stop and investigate, do not touch fixtures.

- [ ] **Step 11: Confirm no `re2` references remain**

Run:
```bash
grep -rn 're2\|google-re2\|google_re2' packages pyproject.toml --include='*.py' --include='*.toml' | grep -v '\.pyc'
```
Expected: empty output.

- [ ] **Step 12: Commit**

```bash
git add packages/matching pyproject.toml uv.lock
git commit -m "refactor(matching): replace google-re2 with stdlib re

RE2 shipped no musllinux wheels (blocking Alpine) and was itself a native
C++ dependency (CVE surface). Patterns are already a re-compatible subset;
compile with re.ASCII to mirror RE2's ASCII class semantics so the golden
corpus stays green. No anti-ReDoS guardrail (operator-owned, reviewed config).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Lot B — packaging: Python 3.14 Alpine, digest-pinned

> Depends on Lot A being merged to `main`. Do not start until the matching gate is green there.

### Task 2: Resolve digests + migrate `crawler` and `webui` Dockerfiles

**Files:**
- Modify: `packages/crawler/Dockerfile:3,26`
- Modify: `packages/webui/Dockerfile:3,28`

**Interfaces:**
- Consumes: nothing.
- Produces: two resolved multi-arch **index** digests — `UV_ALPINE_DIGEST` (for `ghcr.io/astral-sh/uv:python3.14-alpine`) and `PY_ALPINE_DIGEST` (for `python:3.14-alpine`) — reused verbatim by Task 3.

- [ ] **Step 1: Verify the tags exist and resolve the multi-arch index digests**

> Requires network + Docker. If the sandbox has no Docker, the operator runs these via `!` and pastes the digests back.

Run:
```bash
docker buildx imagetools inspect python:3.14-alpine --format '{{.Manifest.Digest}}'
docker buildx imagetools inspect ghcr.io/astral-sh/uv:python3.14-alpine --format '{{.Manifest.Digest}}'
```
Expected: two `sha256:…` index digests. Record them as `PY_ALPINE_DIGEST` and `UV_ALPINE_DIGEST`. If the `uv` tag name differs, list candidates with:
```bash
docker buildx imagetools inspect ghcr.io/astral-sh/uv:python3.14-alpine || \
  echo "check https://github.com/astral-sh/uv/pkgs/container/uv for the exact python3.14-alpine tag"
```

- [ ] **Step 2: Update `packages/crawler/Dockerfile`**

Line 3 (builder):
```dockerfile
FROM ghcr.io/astral-sh/uv:python3.14-alpine@sha256:<UV_ALPINE_DIGEST> AS builder
```
Line 26 (runtime):
```dockerfile
FROM python:3.14-alpine@sha256:<PY_ALPINE_DIGEST>
```
Nothing else changes: the crawler runtime has no system package, the venv copy and `nonroot` user (already created via… see note) stand. **Note:** the crawler currently creates its user with `groupadd`/`useradd` (lines 29-30) — those are glibc/Debian tools absent on Alpine. Replace lines 29-30 with the busybox form:
```dockerfile
RUN addgroup -S -g 999 nonroot \
    && adduser -S -u 999 -G nonroot -h /home/nonroot nonroot
```
(`-h /home/nonroot` reproduces `--create-home`; the later `mkdir -p /data/... && chown` and `COPY --chown=nonroot:nonroot` are unchanged.)

- [ ] **Step 3: Update `packages/webui/Dockerfile`**

Line 3 (builder) and line 28 (runtime): same two `FROM` lines as Task 2 Step 2. Then replace the `groupadd`/`useradd` block (lines 31-32) with the busybox form:
```dockerfile
RUN addgroup -S -g 999 nonroot \
    && adduser -S -u 999 -G nonroot -h /home/nonroot nonroot
```

- [ ] **Step 4: Sanity-build both images locally (amd64)**

> Requires Docker. Operator via `!` if needed.

Run:
```bash
docker build -f packages/crawler/Dockerfile -t emule-crawler:alpine-test .
docker build -f packages/webui/Dockerfile -t emule-webui:alpine-test .
```
Expected: both build; `uv sync` pulls only musl wheels (no `google-re2`, no C build — `rapidfuzz`/`pyyaml` have cp314 musllinux wheels). Smoke the entrypoints:
```bash
docker run --rm --entrypoint python emule-crawler:alpine-test -c "import catalog_matching, emule_indexer; print('ok')"
docker run --rm --entrypoint python emule-webui:alpine-test -c "import catalog_webui; print('ok')"
```
Expected: `ok` from each.

- [ ] **Step 5: Commit**

```bash
git add packages/crawler/Dockerfile packages/webui/Dockerfile
git commit -m "chore(docker): migrate crawler+webui base images to python 3.14 alpine

Digest-pinned on the multi-arch index (amd64+arm64). Busybox user creation
(addgroup/adduser) replaces groupadd/useradd. Unblocked by re2 removal.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3: Migrate the `verifier` Dockerfile (apt → apk)

**Files:**
- Modify: `packages/verifier/Dockerfile:3,28,35-41`

**Interfaces:**
- Consumes: `UV_ALPINE_DIGEST`, `PY_ALPINE_DIGEST` from Task 2.
- Produces: a working verifier Alpine image with `ffprobe` (ffmpeg), `clamscan` (clamav) and `libseccomp.so.2` present.

- [ ] **Step 1: Update the builder and runtime `FROM` lines**

Line 3:
```dockerfile
FROM ghcr.io/astral-sh/uv:python3.14-alpine@sha256:<UV_ALPINE_DIGEST> AS builder
```
Line 28:
```dockerfile
FROM python:3.14-alpine@sha256:<PY_ALPINE_DIGEST>
```

- [ ] **Step 2: Replace the apt install block with apk**

Replace the `RUN apt-get …` block (lines 35-37) with:
```dockerfile
# ffmpeg provides ffprobe (D-analysis); clamav provides clamscan (opt-in signature check);
# libseccomp provides libseccomp.so.2 loaded at runtime by pyseccomp (seccomp-bpf kernel ring).
# freshclam is NOT run here: the signature DB comes from a RO volume populated by the sidecar,
# so this image stays network-free (internal). Purge the empty DB apk drops: the real DB mounts
# on /clamav-db.
RUN apk add --no-cache ffmpeg clamav libseccomp \
    && rm -rf /var/lib/clamav/*
```
(apk installs no "recommends", so the `--no-install-recommends` intent is the default. `clamav` on Alpine provides `clamscan`; `libseccomp` provides the shared lib — its `-dev` is not needed since `pyseccomp` loads `libseccomp.so.2` at runtime via ctypes.)

- [ ] **Step 3: Replace the busybox user creation**

Replace the `groupadd`/`useradd` block (lines 40-41) with:
```dockerfile
RUN addgroup -S -g 999 nonroot \
    && adduser -S -u 999 -G nonroot -h /home/nonroot nonroot
```

- [ ] **Step 4: Build and smoke the verifier image (amd64)**

> Requires Docker. Operator via `!` if needed.

Run:
```bash
docker build -f packages/verifier/Dockerfile -t emule-verifier:alpine-test .
docker run --rm --entrypoint sh emule-verifier:alpine-test -c \
  "ffprobe -version >/dev/null && clamscan --version && python -c 'import pyseccomp, download_verifier; print(\"ok\")'"
```
Expected: clamscan version line + `ok` (confirms ffprobe, clamscan, libseccomp-backed pyseccomp all import/run).

- [ ] **Step 5: Commit**

```bash
git add packages/verifier/Dockerfile
git commit -m "chore(docker): migrate verifier base image to python 3.14 alpine

apk (ffmpeg/clamav/libseccomp) replaces apt; busybox user creation. libseccomp
package provides libseccomp.so.2 for pyseccomp. Digest-pinned multi-arch index.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 4: Validate on real hardware — multi-arch build + smoke (operator)

> No code change. Cannot run in the sandbox (no veth / Docker socket constraints). Operator runs via `!` or CI.

- [ ] **Step 1: Full gate (all packages) still green**

Run:
```bash
( cd packages/matching && uv run pytest -q ) && ( cd packages/crawler && uv run pytest -q ) \
  && ( cd packages/verifier && uv run pytest -q ) && ( cd packages/webui && uv run pytest -q )
uv run ruff check . && uv run ruff format --check . && uv run mypy \
  && uv run sqlfluff lint packages/crawler/src \
  && uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates
```
Expected: all green.

- [ ] **Step 2: Multi-arch build on native runners (amd64 + arm64)**

Push the Lot B branch and let CI (`validate.yml`, orthogonal `package × arch` matrix on native runners) build the three images on both arches. Expected: green build for all 3 × 2. Locally, the operator may also run the compose smoke:
```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
```
Expected: assembled stack smoke passes (no VPN). Record any arch-specific surprise (esp. `verifier` clamav/libseccomp on arm64) in the handoff.

### Task 5: Docs updates (Wrap)

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/specs/2026-06-10-crawler-mvp-design.md`
- Create: `docs/handoffs/<ISO date> - handoff - drop google-re2 + alpine 3.14.md`

- [ ] **Step 1: Retire RE2 as an invariant in `CLAUDE.md`**

In the *Architecture — the matching engine* section and the *Gotchas* section, replace the RE2-specific guidance (RE2 imports as `re2`, no lookaround/backreferences, `re2.error`, `\b` vs `(?:^|[^0-9])`) with the new reality: patterns compile under stdlib `re` with `re.ASCII` (ASCII class semantics preserved); lookaround/backreferences are now permitted; the anti-ReDoS posture is now an **accepted residual risk** (operator-owned, reviewed `matcher.yml`), not a structural guarantee. Keep the invariant that RE2/`re` decisions are order-independent and that `MatchDecision`'s three fields map to columns.

- [ ] **Step 2: Cross-reference the MVP design spec**

In `docs/specs/2026-06-10-crawler-mvp-design.md` §8.2, add a one-line note: the regex engine moved from RE2 to stdlib `re`+`re.ASCII` (see `docs/specs/2026-07-03-drop-google-re2-alpine-migration.md`).

- [ ] **Step 3: Write the milestone handoff (English)**

Create `docs/handoffs/<ISO date> - handoff - drop google-re2 + alpine 3.14.md`: current state, what was built (both lots), learned pitfalls (musl wheel matrix, busybox user, apk package names), suggested next step, and what is **NOT validated against real hardware** (multi-arch Alpine build + verifier clamav/libseccomp on arm64 if CI wasn't run).

- [ ] **Step 4: Commit the docs**

```bash
git add CLAUDE.md docs/specs docs/handoffs
git commit -m "docs: record re2->re swap + alpine 3.14 migration (handoff, invariants)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** §4.1 → Task 1 steps 2-6; §4.2 → steps 7-9; §4.3 → steps 5-6,10; §5 (accepted risk, no guardrail) → Task 1 commit message + Task 5 step 1; §6 (Dockerfiles) → Tasks 2-3; §7 (verification) → Task 4; §8 (sequencing) → Lot A/B split; §9 (docs) → Task 5. All covered.
- **Placeholders:** `<UV_ALPINE_DIGEST>` / `<PY_ALPINE_DIGEST>` are resolved in Task 2 Step 1 (real values, not TBD). `<ISO date>` is the handoff filename convention.
- **Type consistency:** public signatures unchanged (`RegexMatcher`, `interpolate`, `validate_config`); the only behavioural change is the `_check_regexes_compile` message ("not compilable"), matched by the updated assertion in Task 1 Step 6.
