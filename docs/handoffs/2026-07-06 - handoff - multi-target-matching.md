# Handoff: multi-target matching (whole-episode files)

- Date: 2026-07-06
- Merged: PR #21 (rebased into `main`, tip `ff8700a`), tag `v0.27.0-multi-target-matching` (annotated, not pushed)
- Spec: `docs/specs/2026-07-06-multi-target-matching.md` · Plan: `docs/plans/2026-07-06-multi-target-matching.md`

## Current state

A whole-episode P2P file (one ed2k hash covering both the A and B segments of an episode) now
produces a decision for **each** segment target, instead of collapsing to a single
`unidentified` catalog row. This fixes the reported symptom: two-segment episodes
(`[Keroro].072/081/086/092`) showed unidentified while mono episodes (074, 090) matched, despite
the identical filename shape.

The whole workspace is green: matching 234, crawler 777, verifier 176, webui 159 tests, 100%
branch coverage per package, `mypy --strict`, ruff, sqlfluff, template-check all clean. CI gate
(lint + test + build-and-verify amd64/arm64 + gate) passed on the PR.

## What was built (3 phases, subagent-driven, one review per task + a final whole-branch review)

- **matching** (`packages/matching`): `MatchingEngine.evaluate` moved from `MatchDecision | None`
  to `list[MatchDecision]`. Algorithm: per target, first-matching rule; attributable matches
  (the number/title video rules) fan out per episode (`absolute_number`) — a segment-level
  signal (a title match or a lettered number) on any segment of an episode emits only those
  segments and suppresses the bare-number fan-out for the whole episode; otherwise the bare
  number emits every segment. No attributable match falls back to the existing single-winner
  min-key (one `keroro_large` catalog decision, or one `archive_candidate`). No tier cap. The
  `{mono_gate}` workaround and the now-unused `sole_segment` field were retired (the fan-out
  obsoletes them).
- **crawler** (`packages/crawler`): `record_decision_if_changed` is now a **set diff keyed by
  `(hash, target_id)`** returning an `int` write-count; retraction is **per-target**
  (`record_retraction(hash, target_id)`, no more `target_id=""` sentinel written). Two reads
  moved to latest-per-`(hash, target_id)`: `last_decisions` (new; includes `retracted`, excludes
  the legacy `''` sentinel) and `download_decisions` (`PARTITION BY ed2k_hash, target_id`). The
  dead singular `last_decision` was removed. **No DDL** — the append-only `match_decisions` table
  already supported N rows per hash. One file is downloaded once (dedup by hash) and covers both
  segments; verification verdict is per-hash, so both segment rows inherit it.
- **webui** (`packages/webui`): one row per file with its targets aggregated in the cell (middle
  dot ` · `, U+00B7, never an em-dash); coverage/list/detail SQL read latest-per-`(hash,
  target_id)` and exclude both the `''` sentinel and `retracted` rows; counters stay file-based
  (`COUNT(DISTINCT ed2k_hash)`); a file appears under each of its targets (`EXISTS` filter). The
  logic-free templates were untouched (aggregation is precomputed into `str` display fields).

## Accepted design decision

A whole-episode download emits **two** `DecisionRecorded(download)` events, so it produces **two
community notifications** and increments `DECISIONS{download}` by 2 for one physical file. This
is intended (two lost segments recovered = two catalogue events) and was confirmed by the
operator. If ever undesired, dedup by hash at the notifier.

## No-reset migration (spec §10)

The existing catalog self-heals on the next start, no reset and no DDL. The policy-gated startup
backfill (`run_backfill_if_policy_changed` → `reevaluate_catalog`) re-evaluates every catalogued
file under the new engine — the `matcher.yml` fingerprint changed when `{mono_gate}` was dropped,
so the backfill fires automatically. The per-target set-diff then retracts stale
single-target/`unidentified` rows, and legacy `target_id=''` sentinels are ignored on read.
Proven by `test_backfill_retracts_a_legacy_arbitrary_target_row` and
`test_backfill_ignores_the_legacy_empty_sentinel`.

## Learned pitfalls (for the next effort)

- **A dedup guard must make both candidates independently eligible.** The first version of the
  §8 download-dedup guard test was a *false guard*: `062B` was absent from the test's targets, so
  the second candidate was filtered by an unrelated `skip_complete` path, not by
  `is_downloaded(hash)`. It would have passed even with the dedup removed. Fixed by making both
  segments download-eligible via a test-local `targets` override and asserting the
  `DownloadQueued` telemetry **count** (the hash-keyed `downloads.states` / `client.added_links`
  observables stay collapsed even if the loop-dedup breaks, because `FakeDownloadRepo.record_queued`
  has its own idempotent guard). The strengthened test was verified to go RED (2 events) when the
  guard is removed.
- **The crawler package is red-by-design between the Phase-1 return-type cut and Phase-2's
  `decisions.py` migration.** The real red baseline was 29 failures across 6 files (not just the
  3-4 the plan listed) — `test_search_worker.py`, `test_run_search_cycle.py`,
  `test_composition/test_app.py` also fail as pure fallout and self-resolve once `decisions.py` is
  fixed (zero changes needed to those files). Additive Phase-2 tasks (T1/T2) must be validated in
  isolation with `--no-cov`; only the atomic cut (T3) greens the whole package.
- **The SDD `scripts/task-brief` cannot disambiguate repeated `Task N` headings** across the
  plan's three phases — briefs were extracted by unique task title instead.
- **`group_concat(x, sep ORDER BY …)`** (ordered-set aggregate in the webui) needs SQLite ≥ 3.44;
  satisfied by the digest-pinned Alpine image (SQLite ≥ 3.47) and dev 3.53. Noted because it
  raises the SQL-feature floor above the pre-existing window-function floor (3.25).

## NOT validated against real hardware

Everything above is validated by **unit tests only** (100% branch). The following have **not**
been exercised on a live node:

- The **startup backfill self-healing a real existing `catalog.db`** — proven by guard tests, not
  a live run. On the next deploy, the fingerprint change will trigger a full re-evaluation of the
  operator's catalog; watch the `catalogue re-evaluated: N files, M rows written` log line.
- A **real amuled download of an actual whole-episode file** resolving both segments end to end
  (download once → verify → both targets show covered in the webui).
- The **webui rendering against a real populated catalog** (the operator's screenshot that started
  this work): after the backfill, the `[Keroro].072/081/086/092` rows should show `072A · 072B`
  etc. instead of `unidentified`.

## Suggested next step

Deploy to the observer node, let the startup backfill re-evaluate the existing catalog, and
confirm in the webui that the previously-`unidentified` two-segment Keroro files now render as
their two segment targets. Deferred non-blocking polish (from the final review, none merge-worthy
on their own): per-decision explanation on the file-detail page (currently only the first
decision is explained), a `NamedTuple` for `engine._fan_out`'s entry tuple, and dropping the
unused `_HASH_DISCARD` test constant.
