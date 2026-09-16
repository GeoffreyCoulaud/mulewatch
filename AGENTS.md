# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`mulewatch` continuously surveils the eMule network (eD2k + Kad, via an aMule client driven over its EC protocol) to recover lost-media episodes of the French dub of *Keroro mission Titar* (aired 2008 on Teletoon), cataloguing all available metadata along the way.

It is a **virtual uv workspace** with three packages: `packages/crawler/` (package `mulewatch`, dist `mulewatch`), `packages/matching/` (package `catalog_matching`, dist `catalog-matching`, shared domain), and `packages/vex_guards/` (package `vex_guards`, dist `vex-guards`, dev/CI tooling that reads `security/*.vex.openvex.json` to keep our OpenVEX claims honest; never shipped in a prod image). The crawler package `mulewatch` also contains the in-process webui subpackage `mulewatch.webui` (read-only catalog viewer + light runtime controls + read-only SQL console), served on its own thread by `python -m mulewatch` (one image, one compose service `mulewatch`, one of the three processes s6 supervises in it — see the confinement invariant below).

## Orientation — read before substantial work

The live state, history, and recommended next step are deliberately **not** in this file (they would rot here). They live in:

- `docs/handoffs/` — one continuation guide per milestone (`<ISO date> - handoff - <context>.md`). **The newest is the entry point**: current state, what was just built, learned pitfalls, next step, and what is *not yet validated against real hardware*.
- `docs/specs/2026-06-10-crawler-mvp-design.md` — the authoritative MVP design (17 sections). Other dated specs in that dir record each subsystem's design + decisions; plans are in `docs/plans/`.
- `docs/testing-guide.md` — every test suite (unit + the integration markers), prerequisites, CI pistes.
- `docs/runbooks/deployment.md`: bring a node up (the two compose stacks, VPN, secrets, first boot, High-ID/Low-ID); `docs/runbooks/administration.md`: operate & tune one (lifecycle, optional High-ID + its risks, metrics, container hardening, catalog tools, known limits); `docs/runbooks/troubleshooting.md`: symptom → cause → fix entries (any level).
- `docs/reference/` — dated empirical findings about EC / amuled.
- `git tag` — milestones are annotated `vX.Y.Z-<name>` (not pushed), one per subsystem.

### Where the code lives

The crawler is Clean/Hexagonal: `domain/` pure, `application/` async use-cases, `adapters/` I/O, `composition/` wiring (`CrawlerApp` + `python -m mulewatch`). Paths below are under `packages/crawler/src/mulewatch/` (**c:**) unless noted.

| Subsystem | Location | Role |
|---|---|---|
| Matching engine | `packages/matching/src/catalog_matching/` | declarative YAML-policy file→episode matcher (see Architecture) — shared by the crawler and the in-process webui |
| WebUI (in-process) | c: `webui/` | read-only catalog viewer + runtime controls (`/controls`) + read-only SQL console (`/console`); Starlette/Jinja2 served on its own thread by `python -m mulewatch`; the bind is FIXED at `0.0.0.0:8080` in code, `crawler.yml`'s `webui.enabled` only gates the whole surface |
| EC adapter | c: `adapters/mule_ec/` | aMule EC codec/transport/client; `tools/ec_probe.py` |
| Persistence | c: `adapters/persistence_sqlite/` | append-only catalog.db + local.db; `.sql` migrations; sync repos |
| Search / crawl loop | c: `domain/search/`, `application/` | keywords/cycle/backoff/coverage; worker pool, persisted backoff |
| Download | c: `domain/download/` + ports/adapters | candidate → eD2k link → amuled queue → completion. Nothing moves or opens the file |
| Observability | c: `domain/observability/`, `adapters/observability/` | events → policy → dispatcher; Prometheus + apprise |
| Port-sync (High-ID) | c: `application/` | gluetun port → EC SetPort → restart amuled |
| Standalone catalog tools | c: `merge/`, `compact/` | `python -m mulewatch.{merge,compact}` — N→1 fusion / daily rollup |
| Packaging | `deploy/base.compose.yml` + `deploy/compose.yml` (direct) + `deploy/gluetun.compose.yml` (VPN) + `tests/smoke/compose.yaml`, `packages/crawler/Dockerfile` | one image, one `mulewatch` service (crawler + amuled + amuleweb under s6), no compose profile; smoke stack; container hardening |
| Supply-chain artefacts | `security/` + `.github/workflows/grype-scan.yml` + `release.yml` (`publish-manifest`) | keyless cosign signature + 3 signed attestations (CycloneDX/Syft-JSON SBOM, OpenVEX) on the image's multi-arch index; daily Grype scan → Code scanning. See `SECURITY.md`. |

## Design invariants (do not violate)

- **The catalog's subject is the file, never the person** — no tracking, no deanonymization.
- **The crawler PROD never reads downloaded bytes, and never touches the filesystem of the output directory.** amuled writes a finished file straight into its own IncomingDir; nothing moves it, opens it or inspects it afterwards. Completion is a *positive signal* (amuled's shared-files list), never byte-inference.
- **Package boundary:** `catalog_matching` is a pure shared library imported by the crawler (and by its in-process webui), never the reverse. `vex_guards` is dev/CI tooling and is never imported by shipped code, nor installed in the prod image.
- **Two run modes, one topology:** `download.enabled: true` (the shipped default) wires the download loop live; `download.enabled: false` or an absent `download:` section is crawl-only. This is a **config flag**, not two ways of assembling the stack: both compose stacks start every service unconditionally, and there is no compose profile anywhere.
- **Standalone tools** (`merge`, `compact`) never touch prod code or mutate a DB in place — they read a source and write a NEW file.
- **`deploy/` is the operator-owned single source of truth for config (decided 2026-07-01, relocated from `deploy/config/crawler/` to the `deploy/` root 2026-09-16)** — `deploy/crawler.yml` / `deploy/matcher.yml` / `deploy/targets.yml` stay editable-by-operator deployment config, next to the compose files that mount them; it is **forbidden to canonicalize them as code artifacts** (package data, inline policy dicts, or duplicate test fixtures that shadow them). Every consumer *derives from* `deploy/`, never the reverse: the matcher policy has exactly ONE copy (`deploy/matcher.yml`), read by the matching golden corpus + engine unit tests via `parents[N]` — a test-time path coupling to `deploy/`, deliberately accepted (test-only, not an import; the code DAG is unchanged). Do not reintroduce a `canonical_config.yaml` fixture or an inline `_CANONICAL_RAW` policy dict.
- **Boundary discipline (E-D13):** absorb failures from external I/O (an apprise notifier, an EC call → degrade), but let in-process 100%-tested code crash loudly (a `PrometheusSink` failure is a bug, not a transient).
- **Confinement posture (decided 2026-06-17, updated 2026-06-29, narrowed 2026-09-13, REVERSED for the crawler 2026-09-16):** the portable floor is container hardening, and it is the whole story — but the single-container image reduced what that floor can hold. PID 1 of the `mulewatch` service is `s6-svscan`, preceded by a root one-shot that creates the `amule` user from `PUID`/`PGID`, chowns the bind mounts and writes `amule.conf`; each service then drops privileges with `setpriv`. **`user:`, `read_only:` and `cap_drop: ALL` therefore no longer apply to any shipped service** (spec `docs/specs/2026-09-16-single-container-embedded-amule.md` §9, with operator sign-off): the crawler descended to amuled's confinement level rather than amuled rising to the crawler's. What remains, and is what the compose files must keep: `security_opt: no-new-privileges:true`, `pids_limit: 512` and `mem_limit: 2g`. `no-new-privileges` alongside `setpriv` is **not yet validated on real hardware** (spec §13). Kernel namespaces stay a deliberate non-goal, and that reasoning still binds: `net=none`, bwrap and mount namespaces require either `CAP_SYS_ADMIN` or unprivileged user namespaces (host-sysctl-dependent, conflicts with Docker's default seccomp). The per-child seccomp blocklist and rlimits left the project with the analysis child they confined (scope reduction, 2026-09-13); nothing in the crawler spawns a subprocess over untrusted input any more. Same reasoning summarized for operators in `docs/runbooks/administration.md` § Limites connues. Historical record (gVisor deprecated 2026-06-29 as YAGNI, per-child ring): `docs/specs/2026-06-15-ring-noyau-design.md`.
- **amuled is no longer a third-party container: it is a process of our own image** (2026-09-16), so the 2026-06-17 carve-out for it is gone — there is nothing left to exempt, because the whole service shares the posture above. Documented in `docs/runbooks/troubleshooting.md` § Stockage & droits and `docs/runbooks/administration.md` § Limites connues. Residual risk still accepted, and now wider: a compromise of any of the three processes reaches the bind-mounted `deploy/downloads/{incoming,temp}`, `deploy/data/` (the catalog) and `deploy/amule/`. Do not "fix" this without revisiting the decision record.

## Commands

The gate lives in `pyproject.toml` under `[tool.poe.tasks]` (poethepoet) as the single source of truth — the pre-push hook and CI both call it. `uv run poe` lists every task with its `help`.

```bash
uv sync --dev        # install (scripts/setup-dev.sh also installs the pre-push hook)
uv run poe check     # THE FULL GATE (lint-all + test) — the pre-push hook and CI run exactly this
uv run poe fix       # auto-fix everything mechanical: ruff --fix + ruff format + sqlfluff fix
```

Gate sub-tasks, runnable in isolation: `lint` · `format-check` · `type-check` · `sql-lint` · `template-check` (grouped as **`lint-all`**), and **`test`** (runs each package's suite in its own process, so per-package coverage stays isolated). Fixers: `lint-fix` · `format-fix` · `sql-fix` (grouped as **`fix`**).

**Before hand-fixing lint / formatting / SQL, run `uv run poe fix`** — don't spend turns rewriting by hand what a fixer applies mechanically; review its diff instead.

**The gate is PER PACKAGE** (`cd packages/<pkg> && uv run pytest`). The intent: each package owns its own pytest config and 100 % branch coverage in isolation — a root run would mix coverage data across packages and break the per-package threshold. A bare `uv run pytest` from the repo root is also blocked mechanically (the root has no `[tool.pytest.ini_options]` and a root `conftest.py` sets `collect_ignore_glob = ["packages/*"]` → exit 5 with zero collected). Tooling split: `[tool.ruff]` / `[tool.mypy]` at root span all three packages; `[tool.pytest]` / `[tool.coverage]` / `[tool.sqlfluff]` are per-package; one root `uv.lock`. Deployment artifacts live under `deploy/` (compose + `config/` + `deploy/.env.example`); the smoke stack under `tests/smoke/`.

**Single test** (the package-wide `--cov-fail-under=100` makes a lone test "fail" — disable coverage):

```bash
( cd packages/matching && uv run pytest tests/test_engine.py::test_evaluate_real_62a_is_download_via_first_rule_on_62a --no-cov -q )
```

Integration suites (Docker / ffmpeg, deselected by default, excluded from coverage) are documented in `docs/testing-guide.md`.

## Hard rules (enforced, non-negotiable — do not relax)

- **100% branch coverage on unit tests, per package**, gated in CI and the pre-push hook (`--cov-fail-under=100`, `branch=true`). Integration suites (`ec_integration`, `download_integration`, `orchestration_integration`, `compose_integration`) are deselected by the per-package `addopts` and excluded from coverage measurement: they run **on demand**, not in the gate (see `docs/testing-guide.md`). Never lower the unit-test threshold; add the missing test (exercise *both* sides of every conditional).
- **Strict TDD**: tests are the spec; write the failing test first, watch it fail, then the minimal implementation. Code review judges the tests first. Every test function is annotated `-> None` with typed params.
- **`mypy --strict`** over **both `src` and `tests`**. **`ruff`** selects `E,F,I,UP,B,SIM`, line-length **100**.
- **Clean / Hexagonal**: `domain/` is **pure** — no I/O, no `yaml`/DB/network/clock/logging imports. All I/O lives in `adapters/`. The dependency graph is a DAG. `${NAME}` env-var interpolation in `crawler.yml` is resolved by the config adapter before anything reaches the domain — the domain itself never touches env vars.
- **Python only** (>=3.13). Conventional commits (`feat(domain):`, `fix(domain):`, `test:`, `chore:`, `docs:`).
- **Language: all code is English** (decided 2026-07-02) — identifiers AND prose: comments, docstrings, runtime-emitted messages/logs, CI step names, and commit messages. The only French left in the codebase is genuine *domain data* (real VF episode titles like `La Grenouille Cosmique`, eMule filenames, non-ASCII test fixtures) — data, not prose. **New docs under `docs/specs/`, `docs/plans/` and `docs/handoffs/` are written in English** (decided 2026-07-03); past docs keep their original language (no retro-translation). Conversational replies to the operator stay in their chosen language.
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

Any non-documentation change reaches `main` **through a PR** (see Wrap) so CI's required `validate / gate` runs before merge — this holistic review is the last local check before that PR.

### 5. Wrap

Once the gate is green and code reviewed:

1. **Write the handoff** in `docs/handoffs/<ISO date> - handoff - <context>.md`: current state, what was just built, learned pitfalls, suggested next step, what is NOT validated against real hardware. The handoff is committed before continuing the wrap phase.
2. **Integrate.** **Push the branch and open a PR** for any change touching code, config, tests, `deploy/`, or CI: `main`'s branch protection requires the `validate / gate` check, but `enforce_admins: false` means a local admin merge silently bypasses CI — don't. Wait for the gate green, then merge (linear history is required → **squash or rebase**, not a merge commit). **Exception — documentation-only** (diff touches only `docs/**` + root `*.md`): a local merge/commit to `main` is fine, no PR needed. "Leave as-is" stays available when the user wants to handle it later.
3. **Tag** annotated `vX.Y.Z-<name>` (not pushed), one per subsystem.
4. **Clean up** branch and/or worktree if applicable.

Use the `finishing-a-development-branch` skill to guide this phase.

## Architecture — the matching engine

The core is one layered, declarative matching engine under `packages/matching/src/catalog_matching/`. **The matcher/rule policy is 100% in YAML config; the code is a minimal fixed engine.**

```
load_yaml(path)                         # adapters/config/yaml_loader.py — the ONLY I/O
  → parse_matcher_config / parse_targets   # validation.py — schema + fail-fast graph validation:
  →                                        #   DAG/named-cycle, depth bound (32), regex compile-check,
  →                                        #   unique target_id, closed tier/attr enums → ConfigError
  → MatchingEngine(config, targets)        # engine.py — pre-resolves a matcher tree PER TARGET once
  → engine.evaluate(FileCandidate(...))    #   brute-force over all targets (no funnel)
       → MatchDecision(target_id, rule_name, tier, explanation)  |  None  (file discarded)
```

Module roles (each file is single-purpose):
- `normalization.py` — `fold()` (NFKD + strip diacritics + casefold + `{œ→oe, æ→ae}`, keeps punctuation/digits), `normalize()`/`tokenize()` (alphanumerics only).
- `models.py` — `FileCandidate`, `TargetSegment` (`season`/`seasonal_number`/`absolute_number`/`segment`/`title`/`status`/`sole_segment`; `.target_id` = `062A` from `absolute_number`, zero-padded; double numérotation absolu+saisonnal).
- `matchers.py` — the 4 leaf matchers (`KeywordMatcher`, `RegexMatcher` over stdlib **`re`** (`re.ASCII`), `CoverageMatcher` via rapidfuzz, `AttrBetweenMatcher`).
- `interpolation.py` — regex placeholder interpolation (`{season} {seasonal_number} {absolute_number} {segment} {title}`, plus `{mono_gate}` → `[^\s\S]` never-match pour neutraliser un token sur les cibles non-mono).
- `combinators.py` — the `Matcher` Protocol (`matches(candidate) -> bool`) + `All/Any/NotMatcher`; leaf matchers satisfy it structurally.
- `config.py` — frozen tagged-union config model (`*Def`, `Rule`, `MatcherConfig`, `TIERS`).
- `validation.py` — `parse_*` (structural) + `validate_config` (semantic/graph pass; checks needing the full token table live here, not in parsing).
- `resolver.py` — builds the per-target `Matcher` tree; regex interpolated+compiled per target, coverage bound to the title.
- `engine.py` — `MatchingEngine`; deterministic decision = `min`-key `(-tier_rank, rule_index, target_id)`; `Explanation` is **returned, not logged**.

Invariants: the decision is order-independent (target_ids are unique); `MatchDecision`'s three string fields are exactly the future `match_decisions` columns (persistence columns like `decided_at`/`node_id` are an adapter's job). Regex tokens compile under stdlib `re` with `re.ASCII` (was RE2 until 2026-07-03 — see Gotchas).

## Gotchas

**Matching engine (stdlib `re`, `re.ASCII`):**
- Regex tokens compile with `re.compile(pattern, re.ASCII)` (`matchers.py`, `validation.py`). `re.ASCII` keeps `\b \d \s \w` ASCII — matching the pre-2026-07-03 RE2 default the existing policy relies on (`fold()` does not reduce non-Latin scripts to ASCII). Case-insensitivity is a leading `(?i)` prefix (not a flag arg). An invalid pattern raises **`re.error`** (caught in `validation.py` → `ConfigError` "not compilable").
- **Why `re` and not RE2** (decided 2026-07-03, re-examined 2026-09-16): the original reasons were that `google-re2` shipped no musllinux wheels (which blocked Alpine) and was a native C++ dependency (CVE surface). **The musllinux half of that is now dead**: the single-container image moved the runtime to `debian:trixie-slim`, where manylinux wheels are available, so Alpine no longer blocks anything. The decision stands anyway, on the reasons that survive: stdlib `re` works, the ReDoS risk below was accepted on its merits, `matcher.yml` stays operator-owned, and re-adding a native C++ dependency to buy back a guarantee we decided we did not need would be a regression, not a fix. Re-adding RE2 is explicitly out of scope (`docs/specs/2026-09-16-single-container-embedded-amule.md` §14/§15). Trade-off: RE2's *structural* linear-time guarantee is gone. **No anti-ReDoS guardrail** — residual risk accepted because `matcher.yml` is operator-owned, version-controlled and reviewed (the attacker controls the filename, never the pattern). Consequence: lookaround/backreferences are now syntactically permitted (were impossible under RE2). Full record: `docs/specs/2026-07-03-drop-google-re2-alpine-migration.md`.
- Coverage idioms: a `Protocol` stub `def m(...) -> bool: ...` must be **one line** (a body with `...` on a second line counts as an uncovered branch under `branch=true`). A `case _: assert_never(x)` arm — i.e. the "unreachable default" of a `match` over a closed tagged-union — needs `# pragma: no cover` because it is unreachable by design but the branch counter doesn't know that.
- Don't validate config order-dependently (parse pass = structural; graph pass = full table). Recursive validators need an explicit depth guard → a clean `DepthExceededError`, not `RecursionError` (which is a Python runtime artifact, not a domain error).

**EC / amuled (empirical — facts established by hardware probes / source reading, see `docs/reference/`):**
- *EC = External Connection, the TCP protocol through which the crawler commands the aMule daemon (`amuled`). Defined by aMule, opcodes documented in `docs/reference/ec-protocol.md`.*
- EC exposes **no media metadata on search results**: search-result tags carry only filename, size, hash, source count; no duration, codec, bitrate. Since content verification left the scope (2026-09-13) nothing in the project ever learns more than that, so the catalog holds exactly what eD2k search reports. Detail: `2026-06-11-ec-field-richness.md`.
- The download-queue **partfile hash is in the `EC_TAG_PARTFILE_HASH` (0x031E) child tag**, not the parent's own value (which is a UINT8 index, not the file hash). A naive decoder that takes the parent value gets the queue position instead of the MD4 hash. Detail: `2026-06-13-ec-download-opcodes.md`.
- amuled moves a finished file to its IncomingDir and flips status to complete **after** the move (no race), so by the time `PS_COMPLETE`(9) is observable the file is already at its final on-disk path. Completion is detected via the **shared-files list** (a positive signal: amuled auto-shares completed files, so the file appearing in `EC_OP_GET_SHARED_FILES` = it exists at its final path). Detail: `2026-06-17-amuled-completion-behavior.md`.
