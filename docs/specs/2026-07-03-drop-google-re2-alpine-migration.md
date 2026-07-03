# Spec — drop `google-re2`, migrate base images to Python 3.14 Alpine

> Status: proposed (2026-07-03). Operator review before the implementation plan.

## 1. Context & motivation

Operator goal: **reduce the CVE surface** of the images by moving the Docker bases from
`python:3.12-slim-bookworm` (glibc/Debian) to `python:3.14-alpine` (musl), while staying
**digest-pinned**.

Blocker found: the Alpine migration hits **`google-re2`** (the RE2 engine, used by the matching
engine). The pinned version `1.1.20251105` — like every prior one — ships **no `musllinux`
wheels** (only macOS, `manylinux`/glibc and Windows). On musl, `uv` would therefore fall back to
the `sdist` and have to **compile RE2 + abseil from source** (ABI coupling to Alpine's RE2
version, slow builds, must hold on amd64 **and** arm64) — against our "pinned, reproducible
wheels" posture.

Decisive observation: `google-re2` is **itself a native C++ dependency** (RE2 + abseil), hence a
CVE-surface source. Replacing it with the stdlib **`re`** module:

- serves the CVE goal **on two axes** — minimal Alpine base **and** removal of a C++ binary
  dependency;
- **unblocks Alpine**: no remaining non-musl dependency (`re` is stdlib; `rapidfuzz` and
  `pyseccomp` already have musl / `py3-none-any` wheels in `uv.lock`);
- costs almost nothing in code: our patterns are already a **`re`-compatible subset** (no
  lookaround, no backreferences — RE2 supports neither).

The expected volume (niche crawler, periodic searches, filenames ≤ 255 bytes, ~100 targets) is
handled effortlessly by `re` as by RE2; **RE2 was never there for speed** but for a linear-time
guarantee (anti-ReDoS). See §5 for how that point is handled.

## 2. Decision

1. Replace `google-re2` with the stdlib `re` module in `packages/matching`, **behaviour-identical**
   (compile with `re.ASCII`, see §4.1).
2. **No anti-ReDoS guardrail** is added (decided 2026-07-03) — residual risk accepted, see §5.
3. Switch the 3 Dockerfiles to `python:3.14-alpine`, **digest-pinned on the multi-arch index**.

## 3. Scope

`re2` is imported **only** in `packages/matching` (verified by grep — no other package references
it), across 3 source files + 1 test:

| File | Current usage |
|---|---|
| `src/catalog_matching/matchers.py` | `re2.compile(pattern)` in `RegexMatcher` |
| `src/catalog_matching/interpolation.py` | `re2.escape(...)` (literal placeholder insertion) |
| `src/catalog_matching/validation.py` | compile-check `re2.compile` / `except re2.error` |
| `tests/test_interpolation.py` | `re2.escape` / `re2.compile` (assertions) |

Packaging side: the 3 `Dockerfile`s (`crawler`, `verifier`, `webui`). The **third-party** images
(amuled, gluetun, clamav/freshclam sidecar) are not *our* base images → out of scope.

## 4. Lot A — domain: `google-re2` → `re`

### 4.1 The swap, behaviour-identical

- `matchers.py`: `re2.compile(pattern)` → `re.compile(pattern, re.ASCII)`. The `(?i)` prefix
  (already applied at the head of the pattern for case-insensitivity) stays valid under `re`.
- `interpolation.py`: `re2.escape(...)` → `re.escape(...)` (the `re` module is already imported
  there for `_PLACEHOLDER`). `{mono_gate}` → `[^\s\S]` unchanged (identical never-match under `re`).
- `validation.py` (`_check_regexes_compile`): `re2.compile` → `re.compile(..., re.ASCII)`;
  `except re2.error` → `except re.error`; message "regex not compilable under RE2" → "… under
  `re`".

**Why `re.ASCII` (the crux of parity).** RE2 treats `\b \d \s \w` as **ASCII** by default; `re`
treats them as **Unicode** by default on `str`. But `fold()` does not reduce everything to ASCII
(non-Latin characters, e.g. CJK, survive). Compiling with `re.ASCII` reproduces RE2's semantics
**exactly** for the existing patterns (`\bENG\b`, `\bvf\b`, `r\d\d`, `\s?`…) → the **golden corpus
stays green**, which is our non-regression proof.

### 4.2 Dependency / tooling cleanup

- Remove `google-re2>=1.1.20251105` from `packages/matching/pyproject.toml`.
- Remove the `[[tool.mypy.overrides]] module = "re2"` block (root `pyproject.toml`, lines 62-64);
  `re` is typed, so no override is required.
- Regenerate `uv.lock` (`uv sync`) → `google-re2` drops out of the graph.

### 4.3 Tests

- `test_interpolation.py`: 2 `re2.escape` / `re2.compile` references → `re` (`re.escape`,
  `re.compile(..., re.ASCII)` as the assertion needs).
- `test_validation.py` (assertion `pytest.raises(ConfigError, match="RE2")`) → new message.
- **Requirement**: 100% branch coverage per package maintained; the golden corpus (real 62a
  fixtures, etc.) must stay green **with no fixture change** — any divergence flags a semantic
  difference to investigate.

## 5. Consequences & accepted residual risk

- **A loosening (not a break)**: the compile-check now validates against `re`, not RE2. *Lookaround*
  and *backreferences* therefore become **syntactically accepted** in `matcher.yml`. Invalid
  patterns still fail at load (compile-check kept).
- **Loss of RE2's structural linear-time guarantee.** Residual risk: a pathological pattern
  (`(x+)+`…) **written by the operator** could, on an adversarial filename, cause catastrophic
  backtracking (ReDoS). **Accepted** because: `matcher.yml` is **operator-owned, version-controlled
  and reviewed** config (the attacker controls the *filename*, never the *pattern*); the current
  patterns are all flat (no ambiguous nested quantifier); filenames are length-bounded. A guardrail
  (linter + input bound) was **explicitly rejected as disproportionate** (decided 2026-07-03).

## 6. Lot B — packaging: Python 3.14 Alpine, digest-pinned

Unblocked by Lot A. Across the 3 `Dockerfile`s:

- builder: `ghcr.io/astral-sh/uv:python3.14-alpine…@sha256:<index-digest>`;
  runtime: `python:3.14-alpine…@sha256:<index-digest>`.
- **Digests resolved on the multi-arch index** (same method as the recent *pin base images to
  multi-arch index digests* commits) → covers amd64 **and** arm64 under a single digest.
- `crawler` / `webui`: change **limited to the `FROM` lines** (venv copied, non-root; no system
  dependency, no non-musl dependency once Lot A is done).
- `verifier` (the only tricky one):
  - `apt-get install --no-install-recommends ffmpeg clamav libseccomp2` →
    `apk add --no-cache ffmpeg clamav libseccomp`;
  - busybox user creation: `addgroup -S -g 999 nonroot && adduser -S -u 999 -G nonroot nonroot`
    (`groupadd`/`useradd` do not exist on Alpine);
  - purge the empty clamav DB dropped by the package (`/var/lib/clamav/*`) — unchanged in spirit:
    the real DB comes from the mounted RO volume;
  - `pyseccomp` loads `libseccomp.so.2` **at runtime** → the `libseccomp` package must be present.
- The crawler still creates `/data/{catalog,local,quarantine}` owned by `nonroot` (unchanged).

## 7. Verification

- **Full gate** (100% branch/package, ruff, ruff format, mypy, sqlfluff, check_templates).
- **Non-regression proof** = golden corpus green under `re`+`re.ASCII`.
- **To validate on hardware** (outside the sandbox — the integration/smoke suites have no local
  veth; run by the operator via `!`): real multi-arch Alpine build, and the `verifier` Alpine image
  with working `ffprobe` / `clamscan` / `libseccomp` on **amd64 and arm64** (per the "test all
  published arches" policy).

## 8. Sequencing

Two branches/PRs sharing this spec:

- **Lot A** — `refactor(matching): replace google-re2 with stdlib re` — merged and **gate green**
  first (the golden corpus proves parity).
- **Lot B** — `chore(docker): migrate base images to python 3.14 alpine (digest-pinned)` — depends
  on A being merged.

## 9. Doc updates (Wrap phase)

- `CLAUDE.md`: drop RE2 as an invariant (*Architecture — the matching engine* and *Gotchas*
  sections), record the move to `re`+`re.ASCII` and the accepted residual ReDoS risk.
- `docs/specs/2026-06-10-crawler-mvp-design.md` §8.2: cross-reference note to this spec.
- Milestone handoff at the end of the work.

## 10. Non-goals

- No anti-ReDoS guardrail (see §5).
- No change to third-party images (amuled, gluetun, clamav/freshclam sidecar).
- No change to the matching policy (patterns, tiers, targets): behaviour-identical swap.
