# Spec — webui `/files` defaults to matched-only (with a catalogue counter)

- Date: 2026-07-02
- Status: approved
- Scope: `packages/webui` only (read-only viewer). No crawler / matcher / schema change.

## Problem

The `/files` page is a raw dump of the whole catalogue: `FROM files LEFT JOIN
match_decisions …`, one row per `ed2k_hash`, paginated 50, ordered by last-seen.
Because the crawler catalogues **every** observed file's metadata unconditionally
(the `match_decisions` row is written only on a non-`None` decision), the vast
majority of rows are eMule noise the matcher already discarded — e.g.
`(HBS).Sarxento.Keroro.ep021.(gallego,japones).dvdrip.by.hobbes.ogm`, correctly
excluded (Galician/Japanese, not the French dub), shown with `target`/`tier`/
`verdict` = `—`. This noise drowns the handful of files that actually matched a
target.

This is a presentation issue, not a matcher bug: "excluded by the matcher" means
"no decision row written", never "absent from the catalogue". The catalogue keeps
everything by design; the viewer should not give equal weight to unmatched rows.

## Goal

Make `/files` a **signal** view by default — only files that produced a match
decision — with the raw catalogue one server-side opt-in away, and a counter that
tells the operator how much is hidden.

## Design

### 1. Semantics & parameter

- New query param `show_unmatched` (absent ⇒ false).
- **Default (false):** the list query adds `AND dec.target_id IS NOT NULL` →
  only files with ≥1 match decision. This is exactly "matched": `record_decision`
  only writes on a non-`None` `MatchDecision`, whose `target_id` is always set, so
  a decision row is never null and an "anti-match" row cannot exist. `dec` is the
  latest-decision LEFT JOIN, so the clause reads "has a latest decision".
- **`?show_unmatched=1`:** the clause is dropped → current behaviour (whole
  catalogue).
- The clause composes with the existing `target/tier/verdict/q` filters via `AND`,
  with no special-casing. When `target=X` is present the clause is redundant but
  harmless. Consequence: `/targets/{id}` (which calls `list_files(target=…)`) is
  already matched-only and **does not change** — it only needs the new `list_files`
  parameter to default to false.

### 2. Counter (filter-aware)

A single `COUNT` query mirroring the list query's `FROM` + JOINs + `q/tier/verdict`
clauses, **without** the matched clause and **without** pagination, using
conditional aggregation:

```sql
SELECT COUNT(*) AS total, COUNT(dec.target_id) AS matched
FROM files AS f
LEFT JOIN (latest observation) AS obs …
LEFT JOIN (latest decision)    AS dec …
WHERE <same q/tier/verdict clauses; NOT the matched clause>
```

`COUNT(dec.target_id)` counts non-null rows = matched; `COUNT(*)` = total in the
current scope (one row per file — the latest-window subqueries keep `FROM files` at
one row per `ed2k_hash`, so `COUNT(*)` is the file count, not an observation count).
One round trip; honest under an active `q` (e.g. "42 matched of 512 catalogued").

### 3. UI (all English)

A summary line above the table in `files.html`:

- **Default mode:** `Showing matched files only — {matched} of {total} catalogued.`
  + link **"Show all catalogued files"** → adds `show_unmatched=1`.
- **Show-all mode:** `Showing all catalogued files — {total} catalogued ({matched}
  matched).` + link **"Matched only"** → removes `show_unmatched`.
- The toggle link **preserves** the active `target/tier/verdict/q` params and
  **resets `page=1`** (counts differ between modes; page N may not exist).

### 4. Code changes

- `CatalogReader.list_files(…, show_unmatched: bool = False)` — conditional matched
  clause. Default false keeps `handle_target` correct with no call-site change.
- `CatalogReader.count_files(…)` — new method, same `target/tier/verdict/q` filters,
  returns `(matched, total)`.
- `handle_files` — reads `show_unmatched`, calls `list_files` + `count_files`,
  builds a `FilesSummary(matched, total, show_unmatched, toggle_url)` view-model
  (no logic in the template, per W-D8), threads `show_unmatched` into `nav_query`.
- `files.html` — summary line + toggle link.

### 5. Tests (100 % branch coverage, per package)

- `list_files` default excludes unmatched rows; `show_unmatched=True` includes them.
- `count_files` returns correct `(matched, total)` with and without a `q` filter.
- `handle_files` both modes: rows, summary label, toggle URL direction.
- Toggle URL preserves `target/tier/verdict/q` and resets `page`.
- Both branches of the `show_unmatched` truthiness check.
- Empty state (no matched files).
- Pagination preserves `show_unmatched`.

## Out of scope (YAGNI)

- No "unmatched-only" mode.
- No change to the pagination heuristic. The counter could make the "Next" link
  exact, but that is a separate concern; the existing full-page heuristic stays.
- No crawler/matcher/schema change; the catalogue keeps storing everything.
