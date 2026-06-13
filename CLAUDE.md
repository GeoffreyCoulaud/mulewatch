# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`emule-indexer` continuously surveils the eMule network (eD2k + Kad, via an aMule client driven over its EC protocol) to recover lost-media episodes of the French dub of *Keroro mission Titar* (aired 2008 on Teletoon), cataloguing all available metadata along the way. A design constraint baked throughout: **the catalog's subject is the file, never the person** — no tracking, no deanonymization.

**Current state:** built so far — the **matching engine** (pure-domain pipeline, `src/emule_indexer/domain/matching/`), the **EC adapter** (`src/emule_indexer/adapters/mule_ec/`: pure sync codec, async transport, `AmuleEcClient`, mapping to `FileObservation`, `tools/ec_probe.py` CLI, mandatory testcontainers integration suite against a real `amuled`), the **data model** (`src/emule_indexer/adapters/persistence_sqlite/`: catalog.db + local.db full schemas with append-only triggers, versioned `.sql` migrations linted by sqlfluff, sync repositories for observations/decisions/node identity/verification task queue with atomic claim + lease + dead-letter), and the **search orchestration** — the **crawl loop** that wires it all together (`domain/search/` pure: keywords/cycle/backoff/coverage; `application/` async use-cases: `record_observations`/`search_worker`/`run_search_cycle`; `composition/`: `CrawlerApp` + `python -m emule_indexer`). It runs a worker pool (one `amuled` per instance), persists observations + decisions (anti-redundancy by verdict change), nudges an in-process hub, with per-(instance,channel) backoff **persisted** in `scheduler_state`, deterministic ordering (injectable `Clock`/`Rng`), and **observable, bounded shutdown**. Also built: the **download capability** (D-download) — `domain/download/` (pure: `states`/`policy`/`ed2k_link`), ports `MuleDownloadClient`+`DownloadEntry`/`Quarantine`, adapters (EC `add_link`/`download_queue` + opcodes, `quarantine_fs` atomic-rename, `SqliteDownloadRepository` + migration `local/0002`), catalog read side (`download_decisions` latest=download / `last_observation`), optional download config, and the single download loop (`application/run_download_cycle`/`download_loop`: monitor → completions → candidates → sleep/nudge; tolerant; **NEVER reads the bytes** — quarantine is `os.replace` only). It is **TESTED but NOT wired live**: `composition/app.py` is unchanged — the live `CrawlerApp` wiring + the full-mode gate (`VERIFIER_URL` + health-check) are deferred to **D-verify** (they need the `ContentVerifier` port). Key empirical facts: **EC exposes no media metadata on search results** (see `docs/reference/2026-06-11-ec-field-richness.md`); the download-queue **partfile hash lives in the `EC_TAG_PARTFILE_HASH` (0x031E) child tag, not the parent's own value** (the parent own-value is a UINT8 index — see `docs/reference/2026-06-13-ec-download-opcodes.md`). The **verifier + live wiring** (D-verify), observability (Prometheus/apprise, plan E), and packaging (plan F) are **not built yet**. Milestones are tagged `v0.1.0-foundations` → `v0.7.0-orchestration` on `main` (not pushed; **D-download carries NO tag** — D-verify closes the Plan D milestone). Integration tests: `uv run pytest -m ec_integration --no-cov` (EC adapter), `uv run pytest -m orchestration_integration --no-cov` (full crawl loop vs a real `amuled`), and `uv run pytest -m download_integration --no-cov` (EC download mechanics: `add_link` + `download_queue` vs a real `amuled`) — all Docker-required, deselected from the default run, excluded from coverage.

**Read these before substantial work** (they hold context that spans many files):
- `docs/handoffs/` — continuation guides, one per milestone (`<ISO date> - handoff - <context>.md`). The newest is the entry point: current state, architecture, conventions, learned pitfalls, recommended next step.
- `docs/superpowers/specs/2026-06-10-crawler-mvp-design.md` — the authoritative MVP design (17 sections). Implementation plans are in `docs/superpowers/plans/`.

## Commands

```bash
uv sync --dev                          # install (also: scripts/setup-dev.sh installs the pre-push hook)

# The full gate (must be green before any commit; the pre-push hook + CI run the same six):
( cd packages/crawler  && uv run pytest -q )          # crawler tests, 100% BRANCH coverage
( cd packages/verifier && uv run pytest -q )          # verifier tests, 100% BRANCH coverage
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run sqlfluff lint packages/crawler/src             # lint SQL (migrations SQLite embarquées)
```

**Running a single test:** the pytest config applies `--cov-fail-under=100` over the whole package, so running one file/test alone reports <100% and the run "fails" even when the tests pass. Disable coverage for focused runs:

```bash
( cd packages/crawler && uv run pytest tests/domain/matching/test_engine.py::test_evaluate_real_62a_is_download_via_first_rule_on_62a --no-cov -q )
```

Always run the full gate (both packages + ruff + mypy + sqlfluff) before considering work done — that's what enforces the gate.

**Le gate est PAR PAQUET** (`cd packages/<pkg> && uv run pytest`) — obligatoire. Un `uv run pytest` nu **depuis la racine n'est PAS le gate** : la racine n'a pas de `[tool.pytest.ini_options]`, donc pas de coverage et pas de désélection des marqueurs d'intégration. Un `conftest.py` racine (`collect_ignore_glob = ["packages/*"]`) le neutralise : depuis la racine, pytest ne collecte rien (exit 5).

**Workspace uv VIRTUEL :** `packages/crawler` (paquet `emule_indexer`, dist `emule-indexer`) + `packages/verifier` (paquet `download_verifier`, dist `download-verifier`) ; `[tool.ruff]`/`[tool.mypy]` à la racine (spannent les deux paquets), `[tool.pytest]`/`[tool.coverage]`/`[tool.sqlfluff]` par paquet ; un seul `uv.lock` racine ; `config/` reste racine.

## Hard rules (enforced, non-negotiable — do not relax)

- **100% branch coverage**, gated in CI and the pre-push hook (`pyproject.toml`: `--cov-fail-under=100`, `branch=true`, `fail_under=100`). Never lower the threshold; add the missing test (exercise *both* sides of every conditional).
- **Strict TDD**: tests are the spec of a feature; write the failing test first, see it fail, then the minimal implementation. Code review judges the tests first. Every test function is annotated `-> None` with typed params.
- **`mypy --strict`** runs over **both `src` and `tests`**. **`ruff`** selects `E,F,I,UP,B,SIM`, line-length **100**.
- **Clean / Hexagonal**: `domain/` is **pure** — no I/O, no `yaml`/DB/network/clock/logging imports. All I/O lives in `adapters/`. The dependency graph is a DAG (`config.py` is the leaf; `engine.py` is the top).
- **Python only** (≥3.12). Work directly on `main`; tag each milestone as an annotated `vX.Y.Z-<name>` (not pushed). Conventional commit prefixes (`feat(domain):`, `fix(domain):`, `test:`, `chore:`, `docs:`).
- Plans are executed **subagent-driven**: a fresh implementer per task, then a spec-compliance review, then a code-quality review, then a final holistic review before tagging. The final holistic review repeatedly catches real cross-cutting bugs — keep it.
- For library/framework/CLI questions, use the **context7 MCP** (current docs) rather than recalled knowledge.

## Architecture — the matching engine

The whole built system is one layered, declarative matching engine under `src/emule_indexer/domain/matching/`. **The matcher/rule policy is 100% in YAML config; the code is a minimal fixed engine.** End-to-end flow:

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
- `models.py` — `FileCandidate` (observed file), `TargetSegment` (canonical episode; `.target_id` = `S2E062A`, zero-padded).
- `matchers.py` — the 4 leaf matchers (`KeywordMatcher`, `RegexMatcher` over **RE2**, `CoverageMatcher` via rapidfuzz with `.value()`, `AttrBetweenMatcher`).
- `interpolation.py` — regex placeholder interpolation (`{number} {segment} {title} {date_alt}`), folded French months, date alternation.
- `combinators.py` — the `Matcher` Protocol (`matches(candidate) -> bool`) and `All/Any/NotMatcher`. Leaf matchers satisfy the Protocol structurally (they don't import it).
- `config.py` — frozen tagged-union config model (`*Def` dataclasses, `Rule`, `MatcherConfig`, `TIERS`, `type Operand`/`TokenDef`).
- `validation.py` — `parse_*` (purely structural parsing) + `validate_config` (the semantic/graph pass). Semantic checks that need the full token table live in the graph pass, not in parsing.
- `resolver.py` — builds the per-target `Matcher` tree (`MatcherResolver.resolve_all` → `ResolvedTarget`); regex are interpolated+compiled **per target**, coverage bound to the target title.
- `engine.py` — `MatchingEngine`; the deterministic decision = `min`-key `(-tier_rank, rule_index, target_id)` (highest tier → lowest rule index → lowest target_id); `Explanation` is **returned, not logged**.

Key invariants: RE2 gives linear-time matching (filenames are hostile input); the decision is provably order-independent (target_ids are unique); `MatchDecision`'s three string fields are exactly the columns the future `match_decisions` table persists (§11) — persistence columns like `decided_at`/`node_id` are deliberately excluded (an adapter's job).

## Gotchas (the operationally critical few — full list in the latest handoff)

- `google-re2` imports as **`re2`** (no type stubs → mypy override in place). Invalid pattern raises **`re2.error`**. **RE2 has no lookaround and no backreferences** — for a digit-boundary, use a *consuming* guard `(?:^|[^0-9])` / `(?:[^0-9]|$)`, not `\b` (which also blocks `_`/letters).
- `re2.compile()`/`re2.escape()` return `Any` — `... is not None` recovers a `bool`; wrap `re2.escape(...)` in `str(...)` to satisfy `--strict`.
- Coverage idioms: a Protocol stub `def m(...) -> bool: ...` must be **one line** (the `def` line counts as covered); a `case _: assert_never(x)` arm needs `# pragma: no cover`.
- Don't validate config order-dependently (the parse pass is structural; the graph pass is where the full table exists). Recursive validators need an explicit depth guard so a pathological config raises a clean `DepthExceededError` instead of `RecursionError`.
