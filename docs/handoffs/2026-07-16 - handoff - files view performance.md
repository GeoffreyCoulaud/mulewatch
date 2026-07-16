# Handoff: make the /files explorer fast (~10s to ~12ms)

Date: 2026-07-16
Branch: `fix/files-view-performance` (on top of `main` 70c8161)
Spec: inline (this was a measured bug hunt, not a design)

## Context

Follow-up to the `/files` explorer chantier (`2026-07-15 - handoff - files explorer sort
search filter.md`). The page took ~10s to render on the live node, and felt worse when
filtering or sorting.

## Current state

Implemented, reviewed twice (whole-branch, then the blocker fix), green on the full gate
(`uv run poe check` EXIT 0: matching 253, crawler 1050, verifier 177, vex_guards 73; lint-all
+ mypy strict clean; 100% branch coverage per package). Validated against a copy of the real
catalogue inside the shipped image. Not yet merged, not yet deployed.

## What the numbers actually said

Measured first, before touching anything. Worth repeating rather than trusting:

- `/files` TTFB 9.6s, all of it server-side. The 3 queries the handler runs (`list_files`,
  `count_files`, `tier_counts`) cost ~3.0s EACH, totalling 9.14s of the 9.64s.
- **Filters and sorts are NOT slower.** 9.6s bare vs 10.4s filtered, within noise. The
  reported "worse with filters" is a flat ~10s plateau repaid on every click, not a filter
  cost. Nothing filter-specific needed fixing.
- Root cause: `file_observations` is append-only and re-observed every cycle, so it holds
  **1,183,660 rows for 1,402 files (~844 observations per file)**. `latest_obs` numbered all
  of them with a `ROW_NUMBER()` window to keep 1,402. A plain `COUNT(*)` over the same table
  takes 15.7ms, so the cost was the window's sort, not the I/O.

## What was built

1. **Migration 0004** (`0004_file_observations_latest_index.sql`):
   `CREATE INDEX idx_file_observations_hash_observed ON file_observations (ed2k_hash, observed_at)`.
   2 columns on purpose: `id` is `INTEGER PRIMARY KEY` (= the rowid), which SQLite already
   stores as every index's implicit trailing key, so it serves the `id DESC` tiebreak unnamed
   (measured: same covering seek, smaller index on a hot append path).
2. **`latest_obs` reshaped to a seek** (`webui/adapters/catalog_read.py`): driven by `files`
   (1,402 rows), seeking each file's newest observation with a correlated
   `ORDER BY observed_at DESC, id DESC LIMIT 1`. `latest_dec` / `latest_ver` deliberately keep
   their window (hundreds of rows; YAGNI until they grow).
3. **`temp_store=MEMORY` scoped to the migration window** (`persistence_sqlite/connection.py`),
   restored in a `finally`; the loop body moved to a new `_run_scripts` helper. This is what
   makes 0004 survivable in the container, see pitfalls.

Result: the 3 queries went **9,139ms -> 21.3ms**, and the full page render (SQL + Jinja2)
is **10-13ms** on the real volumetry. Every variant (sorted, filtered, searched, paged) lands
in the same range: the plateau is gone.

## Learned pitfalls

- **The two halves of the fix are indivisible, and each looks WORTHLESS alone.** The index
  alone buys 1.8x (a window must number every row, so it still walks the table; the index only
  removes the sort). The seek alone is *slower than the status quo* (3,350ms vs 2,798ms).
  Together: 430x. Anyone benchmarking one half in isolation will correctly measure "no gain"
  and wrongly conclude the idea is dead.
- **That is exactly how migration 0003 got it wrong.** It explicitly declined this index,
  reasoning "the window's per-hash sort is cheap (few observations per file)" and "a composite
  there was measured unused by the planner". Both are refuted: 844 observations per file is not
  "few", and the composite reads as unused only while a window is in the query. 0003's `.sql`
  and its test docstring now carry a SUPERSEDED note, because that prose was authoritative and
  would talk the next person out of the fix.
- **`CREATE INDEX` cannot spill in this container, and the gate cannot see it.** The external
  sorter needs ~85MiB of temp files at this scale; `/tmp` is a 64m tmpfs, `/var/tmp` is not
  writable under `read_only: true`, and the WRITE connection left `temp_store` at the file
  default (the `temp_store=MEMORY` hotfix of 2026-07-06 lives only in `reader.py`). Migration
  0004 therefore died with `database or disk is full` -> rollback -> **startup crash loop**.
  On the host `/tmp` is 16G and it migrates in 0.7s, so `uv run poe check` is structurally
  blind to this. Same incident class as the 2026-07-06 webui SQLITE_FULL. Reproduce DB changes
  in the hardened container, at real scale, or you will not see it.
- Chosen remedy: memory temp store over a bigger tmpfs, because the deployed compose
  (`/home/geoffrey/Projets/2026-06-29 keroro emule/`) **diverges from the repo's `deploy/`**: a
  compose-side fix would have to be applied by hand at the same moment as the image, and
  forgetting it crash-loops the node. The image now carries its own remedy.
- **The price of that choice: the failure mode got quieter, and the memory is unbounded.**
  SQLite's in-memory sorter never flushes and is NOT bounded by `cache_size`: it grows linearly
  at ~116 bytes per row of the sorted table (measured: 2MiB cache still consumed 127MiB). At
  1.19M rows the peak is ~150MiB of the 512m limit, so the ceiling sits near **4.5M rows**. Past
  it the container is OOM-killed (SIGKILL, exit 137, empty log, no MigrationError), which is
  strictly harder to diagnose than the SQLITE_FULL it replaces. Accepted knowingly (0004 is
  one-shot, 3.5x of headroom today, and the same trade-off was already taken for the reader on
  2026-07-06), and now written down in `administration.md` (Limites connues) +
  `troubleshooting.md` (the crash-loop entry assumed the log says why; on an OOM it says
  nothing). The thing to watch is a FUTURE migration sorting `file_observations`, not 0004.
- The seek form of `latest_obs` yields one row per catalogued file, including all-NULL rows for
  a file with no observation, where the window form yielded no row. Provably absorbed by the
  LEFT JOINs (differential-tested), but read the CTE on its own and it counts files, not
  observed files.

## Not validated against real hardware

- **The fix has NOT run on the live node.** The image was not rebuilt or redeployed. What was
  validated: the real `open_catalog` startup path executed inside the shipped image under the
  shipped constraints (`--read-only`, `--tmpfs /tmp:size=64m`, `--memory 512m`, `--user
  999:999`, `--cap-drop ALL`) against a copy of the real v3 catalogue: migrated in 5.5s over
  1,190,173 observations, `user_version` 3 -> 4, index built, temp_store restored, **peak RSS
  153 MiB of the 512m limit**. Render timings were taken on the host, via the real `build_app`
  and a TestClient, against a copy of the real catalogue.
- The first start after deploying pays a one-shot ~5.5s migration and a ~150 MiB memory spike.
  The index adds ~84 MiB to a ~365 MiB catalog.db.
- The write-path cost of maintaining one more index on `file_observations` was **not measured**
  (deliberate call: the read win is overwhelming).
- `merge` will now reject any v3 source until it is reopened by a v4 crawler
  (`merger.py:207-212`). That guard is working as designed; this is its first version bump.

## Next step

Deploy and confirm on the real node (rebuild the image, restart, watch the first start apply
0004 and the page land in the ~10ms range).

Follow-ups noted, none blocking:

1. **`file_observations` grows without bound** (1.19M rows for 1,402 files, ~844 each). The
   seek is O(log n) so this holds for a long time, but the retention story and
   `python -m mulewatch.compact` deserve a look. This is the deeper issue the index only
   defers.
2. `FileRow.source_count` is typed `int` but would receive `None` for a file with no
   observation (`views.py`, no `or 0` guard, unlike `filename` / `last_seen`). Pre-existing and
   latent: the real node has 0 such files.
3. `latest_dec` / `latest_ver` have the same structural defect, parked under YAGNI while
   `match_decisions` (196 rows) and `file_verifications` (0 rows) stay small.
4. A `MemoryError` raised mid-migration escapes `_run_scripts` with the transaction still OPEN:
   it is not a `sqlite3.Error`, so the `except` there neither rolls back nor wraps it in the
   MigrationError the spec §14 fail-fast expects. No corruption risk (`_open` closes the
   connection on any BaseException), and unreachable under `mem_limit` alone (cgroup OOM sends
   SIGKILL, it never surfaces as a Python MemoryError), so this only bites under an `RLIMIT_AS`.
   The in-memory sorter is what made this path reachable at all.
5. `_apply_migrations` sets and restores the pragma even when nothing is pending (the
   `current == latest` path). Harmless, one PRAGMA pair per start.
