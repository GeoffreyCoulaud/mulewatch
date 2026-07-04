# Handoff — matcher GB* veto + webui header tooltips + no-em-dash UI rule

Date: 2026-07-04
Tags cut this session: `v0.23.1-matcher-foreign-gb`, `v0.24.0-webui-header-tooltips`

## Current state

Two small, independent lots shipped to `main` (each via PR + green CI gate + rebase merge).
A third, larger lot is **scoped but not built** — see "Next step".

### What was just built

1. **`fix(matching)` — veto GB* (Simplified Chinese) tags** (`v0.23.1-matcher-foreign-gb`, PR #16).
   - `deploy/config/crawler/matcher.yml`: `foreign_lang` now vetoes `GB18030 | GB2312 | GBK | GB`
     (added inside the `\b(...)\b` group, longest→shortest). Complements the pre-existing
     `BIG5` (Traditional Chinese) veto.
   - Motivation: `[POPGO][Keroro][GB]05(28D86ED1).avi` (a real catalogued file) was landing in
     `keroro_large` (tier `catalog` → "unidentified") because no foreign marker fired.
   - Golden corpus: `foreign_gb_popgo_chinese_discarded` (real name), red before / green after.

2. **`feat(webui)` — `?` header tooltips + em-dash purge** (`v0.24.0-webui-header-tooltips`, PR #17).
   - `files.html`: dropped `<p class="tier-legend">`; added a `<button class="th-help">?</button>`
     + `role="tooltip"` `<div>` on the **Sources / Target / Tier / Verdict** headers. Table now
     `class="files-table"`.
   - `app.css`: CSS-only tooltip (no JS → CSP `default-src 'self'` safe). Reveal on `:hover` /
     `:focus-within`; rightmost columns open leftward (`.th-tip--end`). `.files-table { overflow:
     visible }` overrides the base `table { overflow: hidden }` so the tooltip can escape the
     table box — this matters on short (2-row) tables like the current live view.
   - **Em-dash purge** (second commit): empty-value cell placeholder `—` → `·` (middle dot) across
     file list / target coverage / file detail; summary line separator `—` → `:`; page `<title>`
     separator `X — emule-indexer` → `X · emule-indexer` (5 templates).

3. **New cross-project rule — no em-dashes in UI.** The user never wants em-dashes (—) or
   en-dashes (–) in a UI, and only very rarely in copy. Persisted as:
   - `~/.claude/rules/no-em-dashes-in-ui.md` (global, all projects, auto-loaded).
   - Project memory `feedback-no-em-dashes-in-ui`.
   Separators → `:` / period / parentheses / middle dot `·`; empty value → a word or blank;
   short hyphen `-` is fine. Dev prose (code comments/docstrings) is out of scope.

## Learned pitfalls (verified this session by reading the code)

- **The tier verdict IS persisted per file, and is NOT recomputed on matcher change.**
  `catalog.db` `match_decisions(ed2k_hash, target_id, rule_name, tier, decided_at, node_id)` is
  **append-only** (DB triggers). The webui `/files` reads each file's **latest persisted**
  decision (`catalog_read.py` `_SQL_FILES_SOURCE`, `dec.tier`); it does **not** re-run the engine.
  (`matching_read.MatchingExplainer` only recomputes the *explanation text* on the detail page,
  for an already-assigned `target_id`.)
- **A decision is (re)written only on re-observation**, by the crawler, and only if
  `(target_id, rule_name, tier)` changed (`application/record_observations.py:51-57`). The engine
  is built once at startup (`composition/app.py`), so a `matcher.yml` change needs a crawler
  restart even to affect newly-observed files. So the GB fix (lot 1) does **not** retroactively
  fix the already-catalogued `[POPGO][…][GB]` row — that needs lot 3.
- **Exclusion is not representable today.** When the engine returns `None`, `record_observation`
  does `return False` and writes nothing (`record_observations.py:52-53`). The append-only model
  has no "un-match" row, so a file that a new matcher now excludes keeps its stale decision as
  "latest" forever.
- **The `notify` tier currently triggers NO action.** `domain/observability/policy.py:202-208`
  emits a COMMUNITY notification **only** for `tier == "download"`; `notify` produces just an
  INFO log + a metric. There is no notify→notification path yet.
- **Asymmetry to respect in lot 3:** download consumption is **table-driven / replayable**
  (`download_decisions()` = latest-per-hash where `tier='download'`, polled by the download loop),
  but the download **notification** is emitted from the **in-memory** `DecisionRecorded` event at
  discovery, not from a table scan. An offline/backfill re-eval that only appends rows would queue
  downloads but would NOT fire notifications unless the notify/notify-download paths are made
  table-driven.
- **webui gotchas:** templates are logic-free (`_dev/check_templates.py` — no `{% if %}`, no
  filters, no `(` inside `{{ }}`); CSP blocks inline `<style>`/`style=` (keep tooltip CSS in
  `app.css`). Several tests scope absence assertions to the exact `<td>…</td>` cell precisely
  because the header tooltips (previously the legend) also contain words like "unidentified" /
  "pending".

## Suggested next step — Lot 3: "the catalogue follows the matcher"

Goal (user's words): change the matcher and see verdicts recalculate — excluding entries,
re-tiering entries to notify/download, and actually performing the tier's action.

**Decisions already made with the user (ready to spec):**
- **Backfill at crawler startup** — on start, re-evaluate the whole catalogue against the current
  matcher and append changed decisions. Simple, and matches "the catalogue follows the matcher".
- **Retraction** — represent "no longer matches" in the append-only model, e.g. a sentinel
  `match_decisions` row (a discard/retracted tier) so the webui + consumers stop showing the stale
  tier. (The webui already shows latest-per-hash, so a retraction row would win.)
- **`notify` → apprise**, COMMUNITY audience (same as download). Build the missing notify action.
- Make download/notify **notifications table-driven / replayable** so the startup backfill actually
  fires the actions (not just the in-memory discovery event).

**Design constraints to honour in the spec:**
- The catalogue is **append-only by invariant** — retraction is an *append*, never a mutate/delete.
- **Standalone tools (`merge`, `compact`) must write a NEW file** — so backfill is an *in-crawler
  application operation* (runs after the engine is built at startup), NOT a merge/compact-style
  standalone tool that mutates `catalog.db` in place... except backfill legitimately appends to the
  live `catalog.db`; reconcile this with the invariant in the spec (backfill is prod app code
  appending decisions, which `record_observation` already does — it is not a "standalone tool").
- Engine is built once at startup; backfill must use that same engine instance.

Start with brainstorming → spec (`docs/specs/2026-07-…-catalog-reevaluation.md`) before code.

## Not validated against real hardware

- The GB* veto and the tooltip markup/behaviour are covered by **unit tests only** (100% branch,
  full gate green on amd64+arm64). Neither has been exercised against a **live catalogue** or a
  **deployed webui** this session.
- Tooltip hover/focus behaviour was previewed via a faithful HTML artifact and asserted in tests,
  but not driven in a real browser against the running webui.
- The `overflow: visible` on `.files-table` removes the base table's corner-rounding for the
  files/targets tables (a deliberate, scoped cosmetic trade to let tooltips escape the box) — not
  visually confirmed on the deployed site.
