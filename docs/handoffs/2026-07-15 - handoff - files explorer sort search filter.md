# Handoff: `/files` explorer (sorting, search, tier facet)

Date: 2026-07-15. Branch: `feat/files-explorer-sort-search-filter` (worktree
`.claude/worktrees/feat+files-explorer`), based off `main` at `03b8baa`.
Spec: `docs/specs/2026-07-15-files-explorer-sort-search-filter.md`.
Plan: `docs/plans/2026-07-15-files-explorer-sort-search-filter.md`.

This is the second of two chantiers that hardened the catalog before sharing the project with
other lost-media researchers. The first (`docs/handoffs/2026-07-15 - handoff - unidentified
non-episode eviction.md`) tightened the matching rules; this one makes the `/files` page a
comfortable read-only discovery surface for a passive external visitor.

## Current state

Feature complete, gate green, whole-branch reviewed as merge-ready. Not yet a PR (awaiting the
operator's go on the finishing options). The full crawler package gate passes: 1038 unit tests,
100% branch coverage, `mypy --strict` clean over src+tests, ruff/format/sqlfluff/template-check
all green.

Branch commits (on top of `03b8baa`):

- `189ea5d` docs(plan): implementation plan
- `75c7694` feat(webui): sortable list_files via a column allowlist + best_tier_rank
- `d502791` feat(webui): tier_counts facet read
- `066fa44` docs(plan): fix a task-3 header URL assertion (omit default sort param)
- `5dd3a7c` feat(webui): bidirectional column sorting on /files
- `e7f1830` feat(webui): filename search + tier facet with live counts on /files
- `bb43c34` fix(webui): stop the tier facet inheriting the site nav border

## What was built

Four capabilities on `/files`, all server-side and URL-driven (shareable, consistent with the
passive-researcher posture), all read-only:

1. **Bidirectional column sorting** on Name, Size, Sources, Last seen, Tier. The read layer
   (`catalog_read.list_files`) gained `sort`/`direction` keyword params resolved through a fixed
   `SORT_COLUMNS` allowlist and a `SORT_DIRECTIONS` map, so no query-param value is ever
   interpolated into SQL. Tier sorts by the file's strongest tier via a new `best_tier_rank`
   column on the `dec_agg` CTE, whose CASE is GENERATED in Python from
   `catalog_matching.config.TIER_RANK` (one source of truth for tier order). Defaults are
   `last_seen`/`desc`, reproducing the previous ordering byte-for-byte.
2. **Filename search** exposing the pre-existing `q` filter as a GET form.
3. **Tier filter** as a facet of links, with a new `catalog_read.tier_counts` facet-lite read
   (counts per tier over the same filters EXCEPT the tier itself).
4. **Live counts** next to each tier option; `catalog` is masked to `unidentified`.

The handler (`composition/app.py handle_files`) builds two ordered dicts, `filters`
(target, tier, verdict, q, show_unmatched) and `sort_dir` (sort, dir; only when non-default), and
every derived URL (sort headers, facet links, search hidden inputs, page nav, matched/all toggle)
is precomputed from those, so param order is deterministic and each control preserves exactly the
state it should while resetting `page` when it should. All branching is precomputed into frozen
view-models (`domain/views.py`: `SortHeader`, `SortHeaders`, `TierFacet`, `HiddenInput`,
`SearchBar`, `FilterBar`); the template (`adapters/templates/files.html`) stays logic-free (W-D8):
the sort indicator is a `data-sort` attribute drawn by CSS, and the optional sort headers / filter
bar use the 0-or-1-element-tuple `{% for %}` pattern so `handle_target` reuses `files.html` with
empty `headers=()` / `filter_bar=()` (no bar, plain labels on target pages).

## Learned pitfalls (for the next agent)

- **W-D8 is strict**: `check_templates` rejects `{% if %}`/filters/`(` inside `{{ }}`. Precompute
  every string in the handler/view-model. A precomputed value MAY contain `(` (e.g. the facet
  `count_display` = `"(3)"`); only the template SOURCE is constrained.
- **Injection-safe sort = allowlist keys, not values.** The raw `sort`/`dir` params are only ever
  dict keys; `.get(key, default)` maps unknown/malformed to the default. `sqlite3.execute` also
  refuses a smuggled second statement, so the injection test goes red on any interpolating
  implementation.
- **Sort-test seeds must decorrelate every key.** The first seed correlated `size` with
  `last_seen` (and later `tier` with `last_seen`), so a column-swap regression would have passed
  unnoticed. The shipped seed (`_seed_sortable`) gives all five keys distinct orders (last_seen is
  orthogonal to both size and tier). If you add a sortable column, keep this property.
- The plan carried three test typos the implementers caught (an injection-test direction, a
  fixture INSERT arity, a header URL that contradicted the omit-defaults invariant). The plan and
  briefs were corrected each time; the code is the source of truth.

## Suggested next step

Open the PR (branch protection requires the `validate / gate` check; do not local-admin-merge).
After merge, squash or rebase (linear history required). Then tag the subsystem annotated
`vX.Y.Z-<name>` (operator's numbering, not pushed).

## NOT validated against real hardware

- **CSS / visual layout**: the filter bar, the facet chips, the sort-direction arrows, and the
  wide-body layout (from the 2026-07-09 files-layout work) are markup-tested only, never
  eye-verified. Check them on the real node (`localhost:8080`) after deploy, per the
  "verify UI changes in the real container" habit. The tier-facet border reset (`bb43c34`) removes
  an unintended inherited underline but the final spacing still wants a human eye.
- The tier counts and the assembled page were exercised against seeded SQLite fixtures, not the
  real catalog. The chantier-1 matcher deploy (copy `matcher.yml` + restart the crawler to trigger
  re-evaluation) is still an operator action; once done, this page's facet counts will reflect the
  cleaned catalog.

## Deferred, non-blocking (from the whole-branch review)

- No test seeds a decision-without-observation file for `tier_counts` (guaranteed by the LEFT
  JOIN, mirrors `count_files`; optional hardening).
- Pre-existing em-dashes remain on untouched lines of `app.py`/`views.py`/`catalog_read.py`. The
  branch introduced none. Per the global rule, code-prose em-dashes are left alone unless asked; a
  boy-scout cleanup of these files is available on request.
