# Handoff: backlog stack (union of names, disk warning, Kad widening)

- Date: 2026-09-25
- Stack (bottom to top, `gh stack`): #72 `fix/decision-name-union` → #73 `feat/disk-space-warning`
  → `feat/kad-search-more` (this PR). Not merged, not tagged at the time of writing.
- Source of every item: `agents/specs/2026-09-22-amuleapi-migration.md` §8 (left untouched).

## 1. Where the project stands

`BACKLOG.md` is empty. Of its six entries, three shipped in this stack and three were dropped by the
operator on 2026-09-25, each on a measurement:

- **Concurrent searches (§8.1):** a cycle is 2 keywords × 2 channels = 4 searches of at most 30 s,
  then a 300 s wait. Parallelism would buy about 25 % more passes that mostly re-find the same
  results; lowering `cycle_interval_seconds` buys the same with no code.
- **Duration rule (§8.2):** measured on the real node after lot 1: `media` is filled on 43 % of
  observed hashes but only 19 of 132 matched ones. Segments run 9 to 12 min, full episodes 20 to
  24 min; the only outlier is a 59 min file (likely a compilation) at `notify` on 001A/001B, which
  a lost-media hunt wants to keep. No rule is worth writing.
- **SSE completion (§8.3):** the download loop re-reads the shared list every 30 s; SSE would only
  shave that latency.

## 2. What landed

- **#72 union of names (§8.4).** `record_decision_if_changed` judges a hash on every filename the
  catalog ever observed for it (`CatalogRepository.known_filenames`) through
  `MatchingEngine.evaluate_all`. Per target, the best match over all names by
  `(-tier_rank, rule_index)`, then the unchanged fan-out / single-winner rules at the union level.
  A target is retracted only when no name matches it. The existing flapping rows stay (append-only).
- **#73 disk warning (§8.1).** Reuses the download cycle's own `statvfs`: gauge
  `emule_download_disk_free_bytes` every cycle, and `DiskSpaceLow` (WARNING + operations
  notification) once when free space crosses below `download.min_free_bytes`, re-armed at or above
  it (`application/edge_state.py`). No amuleapi `/status` call.
- **Kad widening (§8.1).** `MuleClient.widen_search()` posts `/search/{id}/more`; `409
  kad_more_exhausted` maps to `ApiKadExhaustedError` (→ `True`). The search worker widens a Kad
  search on each poll tick from the second one on (the first would reask nobody and burn one of
  Kad's 4 reasks) until Kad reports exhausted; a failure is logged and stops the widening, never
  the search.

## 3. Pitfalls learned

- **Attribution beats a catch-all on the same target only by config.** Across names the per-target
  pick is tier first, then rule index; within one name it stays "first matching rule". Every
  attributable rule in `deploy/matcher.yml` outranks `archive_candidate` / `keroro_large` today.
- **A `202` from `/more` proves nothing**: a daemon predating the feature always answers it, and a
  successful reask changes neither `progress.percent` nor `progress.state`.
- **Do not run heavy aggregates on the live catalog from the host.** A `GROUP BY` over the 9 M
  `file_observations` rows answered "database disk image is malformed" (also via `docker exec`),
  while `PRAGMA quick_check` said `ok` and the crawler kept writing. Query a copy instead.

## 4. Not validated against real hardware

- The stabilization after #72: expect at most one row per flapping (hash, target), then silence.
  Check `08fd5637…`/095A, which had 309 rows.
- Whether `/more` actually surfaces new results on the live Kad network, and how often it answers
  `409` before the fourth reask.
- The low-disk notification end to end through apprise.

## 5. Next step

Review and merge the stack bottom-up (`gh stack merge`, squash), then tag a minor release and
watch the three items above on the node.
