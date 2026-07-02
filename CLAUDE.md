# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`emule-indexer` continuously surveils the eMule network (eD2k + Kad, via an aMule client driven over its EC protocol) to recover lost-media episodes of the French dub of *Keroro mission Titar* (aired 2008 on Teletoon), cataloguing all available metadata along the way.

It is a **virtual uv workspace** with four packages: `packages/crawler/` (package `emule_indexer`, dist `emule-indexer`), `packages/verifier/` (package `download_verifier`, dist `download-verifier`), `packages/matching/` (package `catalog_matching`, dist `catalog-matching`, shared domain), and `packages/webui/` (package `catalog_webui`, dist `catalog-webui`, read-only catalog viewer).

## Orientation — read before substantial work

The live state, history, and recommended next step are deliberately **not** in this file (they would rot here). They live in:

- `docs/handoffs/` — one continuation guide per milestone (`<ISO date> - handoff - <context>.md`). **The newest is the entry point**: current state, what was just built, learned pitfalls, next step, and what is *not yet validated against real hardware*.
- `docs/specs/2026-06-10-crawler-mvp-design.md` — the authoritative MVP design (17 sections). Other dated specs in that dir record each subsystem's design + decisions; plans are in `docs/plans/`.
- `docs/testing-guide.md` — every test suite (unit + the integration markers), prerequisites, CI pistes.
- `docs/runbooks/deployment.md` — bring a node up (compose profiles, VPN, secrets, first boot, High-ID/Low-ID); `docs/runbooks/administration.md` — operate & tune one (lifecycle, optional High-ID + its risks, clamav, metrics, container hardening, catalog tools, known limits); `docs/runbooks/troubleshooting.md` — symptom → cause → fix entries (any level).
- `docs/reference/` — dated empirical findings about EC / amuled.
- `git tag` — milestones are annotated `vX.Y.Z-<name>` (not pushed), one per subsystem.

### Where the code lives

The crawler is Clean/Hexagonal: `domain/` pure, `application/` async use-cases, `adapters/` I/O, `composition/` wiring (`CrawlerApp` + `python -m emule_indexer`). Paths below are under `packages/crawler/src/emule_indexer/` (**c:**) or `packages/verifier/src/download_verifier/` (**v:**) unless noted.

| Subsystem | Location | Role |
|---|---|---|
| Matching engine | `packages/matching/src/catalog_matching/` | declarative YAML-policy file→episode matcher (see Architecture) — shared by crawler and future webui |
| EC adapter | c: `adapters/mule_ec/` | aMule EC codec/transport/client; `tools/ec_probe.py` |
| Persistence | c: `adapters/persistence_sqlite/` | append-only catalog.db + local.db; `.sql` migrations; sync repos |
| Search / crawl loop | c: `domain/search/`, `application/` | keywords/cycle/backoff/coverage; worker pool, persisted backoff |
| Download | c: `domain/download/` + ports/adapters | candidate → eD2k link → amuled queue → completion; quarantine |
| Verification (consumer) | c: `application/run_verification_cycle`, `HttpContentVerifier` | claims the queue, RPCs the verifier, records the verdict |
| Verifier service | v: `app.py`, `check.py` | Starlette `POST /verify`; spawns a confined analysis child per file |
| Analysis checks | v: `checks/` | `type_sniff` (puremagic) + `ffprobe` + opt-in `clamav`; worst-status |
| Observability | c: `domain/observability/`, `adapters/observability/` | events → policy → dispatcher; Prometheus + apprise |
| Port-sync (High-ID) | c: `application/` | gluetun port → EC SetPort → restart amuled |
| Standalone catalog tools | c: `merge/`, `compact/` | `python -m emule_indexer.{merge,compact}` — N→1 fusion / daily rollup |
| Packaging | `deploy/base.compose.yml` + `deploy/{gluetun,direct}.compose.yml` + `tests/smoke/compose.yaml`, `packages/*/Dockerfile` | observer/download profiles; smoke stack; container hardening (cap_drop, read_only, seccomp) |

## Design invariants (do not violate)

- **The catalog's subject is the file, never the person** — no tracking, no deanonymization.
- **The crawler PROD never reads downloaded bytes.** Quarantine promotion is `os.replace` only; bytes are read solely inside the disposable verifier child. Completion is a *positive signal* (amuled's shared-files list), never byte-inference.
- **Package boundary:** the crawler never imports `download_verifier`; the verifier never imports `emule_indexer`. Only the contract test crosses it.
- **Two run modes:** *observer* (`download.enabled: false` or absent in `crawler.yml`) is crawl-only; *download* (`download.enabled: true`) wires the download + verification loops live, fail-fast on a verifier health check.
- **Standalone tools** (`merge`, `compact`) never touch prod code or mutate a DB in place — they read a source and write a NEW file.
- **`deploy/config/` is the operator-owned single source of truth for config (decided 2026-07-01)** — `crawler.yml` / `matcher.yml` / `targets.yml` stay editable-by-operator deployment config; it is **forbidden to canonicalize them as code artifacts** (package data, inline policy dicts, or duplicate test fixtures that shadow them). Every consumer *derives from* `deploy/`, never the reverse: the matcher policy has exactly ONE copy (`deploy/config/crawler/matcher.yml`), read by the matching golden corpus + engine unit tests via `parents[N]` — a test-time path coupling to `deploy/`, deliberately accepted (test-only, not an import; the code DAG is unchanged). Do not reintroduce a `canonical_config.yaml` fixture or an inline `_CANONICAL_RAW` policy dict.
- **Boundary discipline (E-D13):** absorb failures from external I/O (apprise notifiers, the verifier RPC → degrade), but let in-process 100%-tested code crash loudly (a `PrometheusSink` failure is a bug, not a transient).
- **Confinement posture (decided 2026-06-17, updated 2026-06-29):** the portable floor is container hardening (`cap_drop: ALL` / `no-new-privileges` / `read_only` / `internal`) + per-child seccomp **blocklist** + rlimits. Per-child kernel namespaces and a seccomp allowlist are deliberate non-goals. **Why** : per-child kernel namespaces (`net=none`, bwrap, mount namespaces) require either `CAP_SYS_ADMIN` (which would regress the `cap_drop: ALL` baseline) or unprivileged user namespaces (host-sysctl-dependent, conflicts with Docker's default seccomp). Seccomp allowlist (vs. the current blocklist) was rejected because it's too brittle on healthy media (false-positive risk on legitimate libc calls during `ffprobe` / `clamscan`). Same reasoning summarized for operators in `docs/runbooks/administration.md` § Limites connues. See `docs/specs/2026-06-15-ring-noyau-design.md` for the full record (gVisor section deprecated 2026-06-29, YAGNI).
- **amuled is third-party and intentionally NOT hardened with our `cap_drop: ALL` / `user:` / `read_only` baseline** (decided 2026-06-17, same posture decision). Documented in `docs/runbooks/troubleshooting.md` § Droits cross-user and `docs/runbooks/administration.md` § Limites connues. Residual risk accepted for v0.x: if amuled is compromised, the attacker reaches the `quarantine` volume. Do not "fix" this without revisiting the decision record.
- **The `freshclam` sidecar is likewise NOT hardened with `cap_drop: ALL`** (decided 2026-07-02, same third-party rationale). Its `clamav/clamav` entrypoint runs as root and structurally requires `CHOWN`/`FOWNER`/`SETUID`/`SETGID`/`DAC_OVERRIDE`; under `cap_drop: ALL` it restart-loops on a `chown … Operation not permitted`. It keeps `no-new-privileges` but runs with the default capability set (still non-privileged: no `SYS_ADMIN`/`NET_ADMIN`). Residual risk is bounded — freshclam only writes the signature DB, which the verifier reads RO and re-validates in its own confined child. The earlier claim (spec `2026-06-15-clamav-design.md` §5.2) that `cap_drop: ALL` suffices for freshclam is **refuted** — do not re-add it.

## Commands

```bash
uv sync --dev                          # install (scripts/setup-dev.sh also installs the pre-push hook)

# The full gate — all eight must be green before any commit (pre-push hook + CI run the same):
( cd packages/matching && uv run pytest -q )          # matching tests, 100% BRANCH coverage
( cd packages/crawler  && uv run pytest -q )          # crawler tests, 100% BRANCH coverage
( cd packages/verifier && uv run pytest -q )          # verifier tests, 100% BRANCH coverage
( cd packages/webui    && uv run pytest -q )          # webui tests, 100% BRANCH coverage
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run sqlfluff lint packages/crawler/src                    # embedded SQLite migrations
uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates  # garde templates sans logique
```

**The gate is PER PACKAGE** (`cd packages/<pkg> && uv run pytest`). The intent: each package owns its own pytest config and 100 % branch coverage in isolation — a root run would mix coverage data across packages and break the per-package threshold. A bare `uv run pytest` from the repo root is also blocked mechanically (the root has no `[tool.pytest.ini_options]` and a root `conftest.py` sets `collect_ignore_glob = ["packages/*"]` → exit 5 with zero collected). Tooling split: `[tool.ruff]` / `[tool.mypy]` at root span all four packages; `[tool.pytest]` / `[tool.coverage]` / `[tool.sqlfluff]` are per-package; one root `uv.lock`. Deployment artifacts live under `deploy/` (compose + `config/` + `deploy/.env.example`); the smoke stack under `tests/smoke/`.

**Single test** (the package-wide `--cov-fail-under=100` makes a lone test "fail" — disable coverage):

```bash
( cd packages/matching && uv run pytest tests/test_engine.py::test_evaluate_real_62a_is_download_via_first_rule_on_62a --no-cov -q )
```

Integration suites (Docker / ffmpeg, deselected by default, excluded from coverage) are documented in `docs/testing-guide.md`.

## Hard rules (enforced, non-negotiable — do not relax)

- **100% branch coverage on unit tests, per package**, gated in CI and the pre-push hook (`--cov-fail-under=100`, `branch=true`). Integration suites (`ec_integration`, `download_integration`, `verify_integration`, `analysis_integration`, `orchestration_integration`, `compose_integration`) are deselected by the per-package `addopts` and excluded from coverage measurement — they run **on demand**, not in the gate (see `docs/testing-guide.md`). Never lower the unit-test threshold; add the missing test (exercise *both* sides of every conditional).
- **Strict TDD**: tests are the spec; write the failing test first, watch it fail, then the minimal implementation. Code review judges the tests first. Every test function is annotated `-> None` with typed params.
- **`mypy --strict`** over **both `src` and `tests`**. **`ruff`** selects `E,F,I,UP,B,SIM`, line-length **100**.
- **Clean / Hexagonal**: `domain/` is **pure** — no I/O, no `yaml`/DB/network/clock/logging imports. All I/O lives in `adapters/`. The dependency graph is a DAG. `${NAME}` env-var interpolation in `crawler.yml` is resolved by the config adapter before anything reaches the domain — the domain itself never touches env vars.
- **Python only** (≥3.12). Conventional commits (`feat(domain):`, `fix(domain):`, `test:`, `chore:`, `docs:`).
- **Language: all code is English** (decided 2026-07-02) — identifiers AND prose: comments, docstrings, runtime-emitted messages/logs, CI step names, and commit messages. The only French left in the codebase is genuine *domain data* (real VF episode titles like `La Grenouille Cosmique`, eMule filenames, non-ASCII test fixtures) — data, not prose. Historical markdown under `docs/` was not retro-translated. Conversational replies to the operator stay French.
- **Subagent-driven execution** (Act phase) + **holistic review** (Verify phase): the cross-cutting review regularly catches bugs — don't skip it.
- For library/framework/CLI questions, use the **context7 MCP** (current docs), not recalled knowledge.

## Workflow — Discuss → Spec → Act → Verify → Wrap

Five phases, always in order. **Committing is cheap** — you're allowed to commit autonomously.

### 1. Discuss

**Free-form text** discussion with the user. Use `brainstorm` or `pick-my-brain` skills if clarification is needed. **No `AskUserQuestion` tool** — ask the question in the message directly. No code, no plan — just understanding.

### 2. Spec

Two forms, depending on complexity:

- **Simple / obvious** : inline spec in the conversation, a few paragraphs.
- **Structured** : spec markdown (`docs/specs/<date>-<slug>.md`) + plan markdown (`docs/plans/<date>-<slug>.md`) if needed.

**The spec is reviewed and approved by the user** before writing plans (if any). Plans are not reviewed — they follow from the approved spec.

**Do not use `EnterPlanMode`.** The project workflow is self-contained, not coupled to Claude Code's plan-mode feature.

### 3. Act

`main` is **integration-only** ; never edit directly on it. As soon as code or docs will be modified, **branch first**.

**Branching :** ask the user (4 options) :
1. Stay on current branch
2. New branch **in-place** (`git switch -c <branch>`) — suggested default for edits the user follows in their editor
3. New **worktree** (`EnterWorktree`) — suggested default when dispatching coding agents
4. Other (user describes)

Naming: `<type>/<kebab-slug>` (conventional-commit types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`).

**Execution: subagent-driven by default.** Delegate work to subagents (`Agent`) to keep the main context clean. Exception: very simple, short, localized action (e.g. one file, one change) → do inline. Use the `subagent-driven-development` or `dispatching-parallel-agents` skill as appropriate.

**Worktrees:** `EnterWorktree` creates `.claude/worktrees/<name>`, moves the agent session there, the user's editor stays on `main`. `.claude/worktrees/` is gitignored. `worktree.baseRef = "head"`.

### 4. Verify

Run the **full gate** (unit tests 100% branch per package, ruff, mypy, sqlfluff, check_templates). Review the produced code **holistically** — this review regularly catches cross-cutting bugs.

If the change touches CI or benefits from being verified on remote hardware, **open a PR** to see CI run.

### 5. Wrap

Once the gate is green and code reviewed:

1. **Integrate** per user preference: local merge into `main`, or PR, or leave as-is. Suggested default: local merge, unless the change touches CI → PR.
2. **Tag** annotated `vX.Y.Z-<name>` (not pushed), one per subsystem.
3. **Clean up** branch and/or worktree if applicable.
4. **Write the handoff** in `docs/handoffs/<ISO date> - handoff - <context>.md`: current state, what was just built, learned pitfalls, suggested next step, what is NOT validated against real hardware. The handoff is committed and pushed on `main`.

Use the `finishing-a-development-branch` skill to guide this phase.

## Architecture — the matching engine

The core is one layered, declarative matching engine under `packages/matching/src/catalog_matching/`. **The matcher/rule policy is 100% in YAML config; the code is a minimal fixed engine.**

```
load_yaml(path)                         # adapters/config/yaml_loader.py — the ONLY I/O
  → parse_matcher_config / parse_targets   # validation.py — schema + fail-fast graph validation:
  →                                        #   DAG/named-cycle, depth bound (32), RE2 compile-check,
  →                                        #   unique target_id, closed tier/attr enums → ConfigError
  → MatchingEngine(config, targets)        # engine.py — pre-resolves a matcher tree PER TARGET once
  → engine.evaluate(FileCandidate(...))    #   brute-force over all targets (no funnel)
       → MatchDecision(target_id, rule_name, tier, explanation)  |  None  (file discarded)
```

Module roles (each file is single-purpose):
- `normalization.py` — `fold()` (NFKD + strip diacritics + casefold + `{œ→oe, æ→ae}`, keeps punctuation/digits), `normalize()`/`tokenize()` (alphanumerics only).
- `models.py` — `FileCandidate`, `TargetSegment` (`season`/`seasonal_number`/`absolute_number`/`segment`/`title`/`status`/`sole_segment`; `.target_id` = `S2E062A` from `absolute_number`, zero-padded; double numérotation absolu+saisonnal).
- `matchers.py` — the 4 leaf matchers (`KeywordMatcher`, `RegexMatcher` over **RE2**, `CoverageMatcher` via rapidfuzz, `AttrBetweenMatcher`).
- `interpolation.py` — regex placeholder interpolation (`{season} {seasonal_number} {absolute_number} {segment} {title}`, plus `{mono_gate}` → `[^\s\S]` never-match pour neutraliser un token sur les cibles non-mono).
- `combinators.py` — the `Matcher` Protocol (`matches(candidate) -> bool`) + `All/Any/NotMatcher`; leaf matchers satisfy it structurally.
- `config.py` — frozen tagged-union config model (`*Def`, `Rule`, `MatcherConfig`, `TIERS`).
- `validation.py` — `parse_*` (structural) + `validate_config` (semantic/graph pass; checks needing the full token table live here, not in parsing).
- `resolver.py` — builds the per-target `Matcher` tree; regex interpolated+compiled per target, coverage bound to the title.
- `engine.py` — `MatchingEngine`; deterministic decision = `min`-key `(-tier_rank, rule_index, target_id)`; `Explanation` is **returned, not logged**.

Invariants: RE2 → linear-time matching (filenames are hostile input); the decision is order-independent (target_ids are unique); `MatchDecision`'s three string fields are exactly the future `match_decisions` columns (persistence columns like `decided_at`/`node_id` are an adapter's job).

## Gotchas

**Matching engine / RE2:**
- `google-re2` imports as **`re2`** (no type stubs → mypy override in place). Invalid pattern raises **`re2.error`**.
- **RE2 has no lookaround and no backreferences** — *lookaround* = zero-width assertions like `(?=…)` / `(?<…)` that match without consuming characters; *backreference* = `\1` referring to a captured group. For a digit boundary, use a *consuming* guard `(?:^|[^0-9])` / `(?:[^0-9]|$)` (= "start-of-string OR a non-digit char, consumed"), not `\b` (which behaves differently in RE2 than in PCRE).
- `re2.compile()` / `re2.escape()` return `Any` — `... is not None` recovers a `bool`; wrap `re2.escape(...)` in `str(...)` to satisfy `--strict`.
- Coverage idioms: a `Protocol` stub `def m(...) -> bool: ...` must be **one line** (a body with `...` on a second line counts as an uncovered branch under `branch=true`). A `case _: assert_never(x)` arm — i.e. the "unreachable default" of a `match` over a closed tagged-union — needs `# pragma: no cover` because it is unreachable by design but the branch counter doesn't know that.
- Don't validate config order-dependently (parse pass = structural; graph pass = full table). Recursive validators need an explicit depth guard → a clean `DepthExceededError`, not `RecursionError` (which is a Python runtime artifact, not a domain error).

**EC / amuled (empirical — facts established by hardware probes / source reading, see `docs/reference/`):**
- *EC = External Connection, the TCP protocol through which the crawler commands the aMule daemon (`amuled`). Defined by aMule, opcodes documented in `docs/reference/ec-protocol.md`.*
- EC exposes **no media metadata on search results** — search-result tags carry only filename, size, hash, source count; no duration, codec, bitrate. The verifier (post-download) is the only place that knows the media is e.g. a 24-min H.264 file. Detail: `2026-06-11-ec-field-richness.md`.
- The download-queue **partfile hash is in the `EC_TAG_PARTFILE_HASH` (0x031E) child tag**, not the parent's own value (which is a UINT8 index, not the file hash). A naive decoder that takes the parent value gets the queue position instead of the MD4 hash. Detail: `2026-06-13-ec-download-opcodes.md`.
- amuled moves a finished file to its IncomingDir and flips status to complete **after** the move (no race) — meaning by the time `PS_COMPLETE`(9) is observable, the file is already at its final on-disk path. Completion is detected via the **shared-files list** (a positive signal: amuled auto-shares completed files, so the file appearing in `EC_OP_GET_SHARED_FILES` = it exists at its final path). Detail: `2026-06-17-amuled-completion-behavior.md`.
