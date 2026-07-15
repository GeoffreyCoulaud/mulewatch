# `/files` explorer: sortable columns, filename search, tier filter with live counts

Status: approved in brainstorming (2026-07-15), pending spec review.
Related: `2026-07-09-webui-files-layout.md` (the current layout; it put "sorting or
filtering" explicitly out of scope, this spec is its follow-up), `monolith-consolidation`
(webui structure), `catalog-reevaluation` (retracted == unmatched at the read layer).

## 1. Problem

`/files` already supports rich reads: the handler honours `target`, `tier`, `verdict`, `q`
(filename substring), `show_unmatched` and `page` query params, and `catalog_read` builds
the filtered/paginated SQL for them. But **none of those filters is exposed in the UI**: the
only controls rendered are the matched/all toggle and Prev/Next. There is no search box, no
tier filter, and no sorting at all. A visiting researcher can only page through a fixed
last-seen-desc list.

Audience and posture (operator-confirmed): `/files` is a **read-only discovery surface for a
passive external researcher** (lost-media peers we are about to share the project with). The
goal is to browse the whole catalogue comfortably and grasp its shape, not to run a task. The
dashboard already answers "where is the collection episode-by-episode", so `/files` need not.

## 2. Decisions (operator-approved)

Read-only, no write actions on this page (runtime controls stay in `/controls`). Four
capabilities, nothing more (the operator explicitly declined eD2k-in-list, type glyphs, a
changed default landing, and any density/column change):

1. **Bidirectional column sorting** on Name, Size, Sources, Last seen, Tier. Server-side
   (the list is SQL-paginated, so a client-side sort would only reorder the 50 visible rows).
   Sort state lives in the URL (shareable, consistent with the posture). Injection-safe via a
   column allowlist (no raw value ever interpolated into SQL).
   - Not sortable: Hash / Title / Verdict (little meaning), and Target (a file usually carries
     two decisions, `065A`+`065B`; ambiguous sort key).
   - Tier sorts by the file's **strongest** tier rank (download > notify > catalog), reusing
     `catalog_matching.config.TIER_RANK` as the single source (the SQL rank expression is
     generated from it, not hand-duplicated).
2. **Filename search** exposing the existing `q` filter as a text input.
3. **Tier filter** exposed as a control.
4. **Live counts** next to the tier options (facet-lite): `notify (32)  unidentified (5)
   download (1)`, so the researcher sees the shape before clicking.

Default landing unchanged (matched-only, last-seen-desc): default sort stays
`last_seen desc`, matching today's `ORDER BY obs.observed_at DESC`.

## 3. Design

All precomputation stays in the handler/domain (W-D8: templates carry no logic, only
`for`/`else`); the template renders precomputed URLs, labels and indicators.

### 3.1 Sorting (`catalog_read.list_files` + a header view-model)

- Query params: `sort` in `{name, size, sources, last_seen, tier}`, `dir` in `{asc, desc}`.
  Both normalised in the handler; an unknown/malformed value falls back to the default
  (`sort=last_seen`, `dir=desc`) rather than erroring.
- `list_files` gains `sort`/`dir` params. A module-level allowlist maps each key to a fixed
  ORDER BY column expression:

  ```
  name       -> obs.filename
  size       -> f.size_bytes
  sources    -> obs.source_count
  last_seen  -> obs.observed_at
  tier       -> dec.best_tier_rank      (new computed column, see below)
  ```

  The final clause is `ORDER BY <expr> <ASC|DESC>, f.ed2k_hash` (the `ed2k_hash` tiebreak
  keeps paging stable). SQLite sorts NULLs first in ASC (unmatched files with NULL
  filename/observed_at cluster predictably); acceptable, documented.
- `best_tier_rank`: added to the `dec_agg` CTE as
  `MAX(<CASE tier ...>)`, where the CASE is **generated in Python from `TIER_RANK`** (trusted
  constants, not user input) so there is one source of truth for tier order. A file with no
  decision has NULL rank.
- Header view-model: the handler precomputes, per sortable column, `{label, url, indicator}`
  where `url` sets `sort=col` and the next `dir` (a fresh column uses a per-column sensible
  default direction: Name asc; Size / Sources / Last seen / Tier desc; the active column
  flips), preserving every other active param; `indicator` is `""`, `"asc"`, or `"desc"`.

### 3.2 Search (expose `q`)

A GET form (the whole filter bar, section 3.4) with a text input named `q`, prefilled from
the active value. No backend change (the `obs.filename LIKE ?` clause exists). Submitting
resets `page` to 1 (a new result set).

### 3.3 Tier filter + live counts (`catalog_read.tier_counts`)

- New read `tier_counts(*, target, verdict, query) -> dict[str, int]`: a `GROUP BY tier` over
  the same source and the same filters **except the tier filter itself** (standard facet
  behaviour, so every option shows the count you would get by choosing it). A file counts under
  a tier iff it has at least one live decision of that tier, matching the filter's EXISTS
  semantics (a multi-tier file can count under two facets; fine).
- The handler builds a facet view-model: one entry per tier present (plus an "all" reset),
  `{tier, label, count, url, selected}`; `url` sets/clears `tier=` preserving other params and
  resetting `page`. The webui masks `catalog` as **"unidentified"** here too (same rule the
  row rendering uses).
- The matched/all toggle stays as-is; the tier facet composes with it (choosing a tier implies
  matched files, since unmatched files have no tier).

### 3.4 The filter bar (`files.html` + `app.css`)

A single GET `<form action="/files">` above the table: the search input, the tier facet, and a
submit; hidden inputs carry the current `sort`/`dir`/`show_unmatched` so submitting search or
tier does not lose them. Rendered only on `/files` (the `handle_target` reuse of `files.html`
passes an empty facet/searchbar, exactly as it already passes an empty `summaries`).

## 4. Testing (strict TDD, 100% branch per package)

Crawler package (`packages/crawler`):

- `catalog_read.list_files` sort: each allowlist key yields the expected order (fixture with a
  known permutation); `dir=asc` vs `desc`; an unknown `sort`/`dir` falls back to the default
  (both branches); the `ed2k_hash` tiebreak is deterministic; a would-be injection value
  (`sort="size; drop table"`) is rejected to the default, not interpolated.
- `catalog_read.tier_counts`: counts per tier over a seeded catalogue; a multi-tier file counts
  in both facets; excluded tier filter (facet ignores its own `tier=`); empty catalogue -> `{}`.
- `best_tier_rank` consistency: a test asserts the generated SQL rank orders tiers exactly like
  `TIER_RANK` (guards the single-source claim; red if the CASE and the dict diverge).
- Handler: `sort`/`dir` normalisation (valid, unknown, missing); header view-model URLs and
  indicators for the active column both directions and an inactive column (default direction);
  facet view-model (selected vs not, `catalog` -> "unidentified", `page` reset); search prefill.
- Template: `for`/`else` only, so `check_templates` stays green.

CSS is not unit-tested; verified by eye against the real node after the change (the
"verify UI changes in the real container" habit), including the wide-body layout from 07-09.

## 5. Non-goals / out of scope

- No client-side or multi-column sort; no saved views/presets.
- No `verdict` facet (near-empty in observer mode); the `verdict` param stays reachable by URL
  but is not exposed in the bar.
- No eD2k link in the list, no type glyph, no changed default landing, no column/density change
  (all operator-declined this round).
- Mobile/tablet layouts (inherited stance from 07-09).
- No new write actions.

## 6. Files touched

- `packages/crawler/src/mulewatch/webui/adapters/catalog_read.py` (`list_files` sort params +
  allowlist, `dec_agg.best_tier_rank`, new `tier_counts`).
- `packages/crawler/src/mulewatch/webui/domain/views.py` (header + facet view-models).
- `packages/crawler/src/mulewatch/webui/composition/app.py` (`handle_files`: param
  normalisation, header/facet precompute, pass to template; `handle_target` passes empties).
- `packages/crawler/src/mulewatch/webui/adapters/templates/files.html` (filter bar + sortable
  headers).
- `packages/crawler/src/mulewatch/webui/adapters/static/app.css` (filter bar + header affordance).
- `packages/crawler/tests/webui/` (read-layer + handler + view-model tests).
- No schema change, no engine change.
