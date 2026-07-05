# Multi-target matching: whole-episode files → per-segment decisions

- Status: draft (awaiting operator approval)
- Date: 2026-07-06
- Supersedes/obsoletes: the `{mono_gate}` workaround (interpolation.py + `sole_segment`)

## 1. Problem

A single P2P file legitimately contains **both** segments (A and B) of one Keroro
episode. This is the normal Japanese numbering scheme: `[Keroro].072.[Xvid.mp3].[hash].avi`
(~164 MB) is the whole ~24-min episode 72, which the catalog models as **two** lost-media
targets, `072A` and `072B`.

Today the matching engine cannot express this. The `numero_nu` / `numero_nu_confirmed`
rules match a *bare* absolute number, but their `segment_id_loose` token carries a
`{mono_gate}` placeholder (`matcher.yml:18`) that interpolates to `[^\s\S]` (never-match)
for any target that is **not** mono-segment (`interpolation.py:39-40`). So a bare number can only
identify a mono-segment episode. For a two-segment episode the bare number matches nothing
and the file falls through to the `keroro_large` catch-all → tier `catalog` → the web UI
shows it as **unidentified**.

Observed symptom (web UI, 2026-07-05): `[Keroro].074.avi` and `[Keroro].090.avi` (mono
episodes) are correctly identified and tiered `notify`, while `[Keroro].072/081/086/092`
(two-segment episodes) show as unidentified despite the identical filename shape.

Root cause is fully established: not a bug, but a modeling gap. The engine returns exactly
**one** `MatchDecision | None` per file (`engine.py:186`), so it structurally cannot say
"this file satisfies two targets."

## 2. Goals / non-goals

**Goal.** A whole-episode video file produces a decision for **each** segment target it
covers (`072A` *and* `072B`), so a single recovered file resolves both lost segments.

**Non-goals.**

- Episodes outside the catalogued range (absolute_number > 103, i.e. seasons 3+). They
  have no target and are correctly unidentified. Extending `targets.yml` is out of scope.
- Archives (`.zip`/`.rar`). They keep their current single-decision behavior (see §8) — we
  cannot peek inside, and the fan-out rules all require `is_video`, so archives never enter
  the fan-out path. No change, no regression.
- Downloading a file more than once. A whole-episode file is one ed2k hash → one download
  that covers both segments (see §8).

## 3. Decision semantics (the core rule — operator-approved)

Two natures of signal:

- **Segment-level** (pins one specific segment): a title match (`title_confirmed` /
  `title_review`) or a lettered number (`id_segment_exact`, e.g. `N°072A`).
- **Episode-level** (designates the whole episode, not a segment): a bare number
  (`numero_nu` / `numero_nu_confirmed`).

Applied **per episode** (all targets sharing an `absolute_number`):

1. If **any** segment of the episode has a segment-level match → emit **only** those
   segments, each at its own tier. The bare number is ignored (does not add siblings).
2. Else, if the bare number matches the episode → emit **all** segments of the episode, at
   the bare-number tier.
3. Else → the episode contributes nothing.

| File | Signals | Output |
|---|---|---|
| `[Keroro].072.avi` | bare number only | **072A + 072B** (rule 2) |
| `[Keroro].072 Le défi…avi` | title A + bare number | **072A only** (rule 1 — title cuts the fan-out) |
| `Keroro 072 VF teletoon.avi` | bare number + source marker | **072A + 072B in `download`** (rule 2, no cap) |
| `N°072A …TELETOON` | lettered A | **072A only** (rule 1) |
| `…072 Le défi… Duel contre…avi` | title A + title B | **072A + 072B** (rule 1, both pinned) |

**No tier cap** (operator decision): a strong signal is a verdict, not a doubt. A bare
number + source marker fans out to both segments at `download`, exactly as it already does
for a mono episode. Lost-media recall over false-negative caution.

## 4. Engine changes (`packages/matching`)

`MatchingEngine.evaluate(candidate)` return type: `MatchDecision | None` →
**`list[MatchDecision]`** (empty list = file discarded, replacing `None`).

Algorithm (pure domain, no I/O, deterministic):

1. Filename length bound (unchanged) → `[]` if over.
2. Per target, first-matching rule (unchanged: `_first_matching_rule`).
3. **Attributable set** = matches whose winning rule is a number/title video rule:
   `{id_segment_exact, title_confirmed, numero_nu_confirmed, title_review, numero_nu}`.
   - Segment-level = `{id_segment_exact, title_confirmed, title_review}`.
   - Episode-level = `{numero_nu_confirmed, numero_nu}`.
4. Group the attributable set by episode (`absolute_number`); within each episode apply the
   §3 rule to select the emitted segments.
5. If the selected set is non-empty → return it, **sorted by `target_id`** (determinism).
6. Else → fall back to the **existing single-winner min-key** over ALL matches (this yields
   the one `keroro_large` catalog decision for an unidentified file, or the one
   `archive_candidate` decision for an archive) → return `[that]`, or `[]` if nothing
   matched at all.

Why step 6 preserves everything: the catch-all (`keroro_large`) matches every target
identically (it references no number/title), so it must never fan out. The single-winner
fallback keeps its current behavior — one `catalog`-tier decision under the min-key
`target_id` (smallest, stable = `001A`), which the web UI already masks to "unidentified"
(display keys off `tier == "catalog"`, not the `target_id`). Archives never reach the
fan-out (their rules require `is_archive`, not `is_video`), so they too fall to step 6 →
unchanged.

`Explanation` stays per-decision (one per emitted target). `explain()` unchanged.

### 4.1 Retire the `{mono_gate}` workaround

The mono_gate existed solely to suppress bare-number matches on two-segment episodes — the
exact limitation this spec removes. Delete `{mono_gate}` from `segment_id_loose` in
`matcher.yml`, and remove the now-dead `mono_gate` branch in `interpolation.py` and the
`sole_segment` field in `models.py` if nothing else reads it (the fan-out groups by
`absolute_number`; it does not need `sole_segment`). This is a net simplification.

## 5. Policy change (`matcher.yml`) — also the backfill trigger

`segment_id_loose` loses its leading `{mono_gate}`. This edit changes the
`matcher.yml`/`targets.yml` fingerprint, which **is required** so the startup backfill
fires (§10). The rules list and tiers are otherwise unchanged.

## 6. Persistence (`packages/crawler/.../persistence_sqlite`)

**No DDL migration.** `match_decisions` is already append-only, one row per INSERT, no
UNIQUE constraint (`migrations/catalog/0001_initial.sql:63-73`). It already supports N
decisions per hash. Changes are read-side + retraction only.

- `_SELECT_LAST_DECISION` (`LIMIT 1` per hash) → **latest per `(ed2k_hash, target_id)`**.
  Return type moves from a single `DecisionRecord | None` to a mapping
  `target_id → DecisionRecord` (the set of current verdicts for the hash), for the
  set-based anti-redundancy in §7. Excludes the legacy `target_id = ""` sentinel.
- `_SELECT_DOWNLOAD_DECISIONS` (`PARTITION BY ed2k_hash`) → **`PARTITION BY ed2k_hash,
  target_id`**, then filter `tier = 'download'`. Otherwise a file with both segments in
  `download` loses one candidate (critical).
- `record_retraction` becomes **per target**: it must retract `(hash, 072A)` while leaving
  `(hash, 072B)` intact. The `target_id = ""` sentinel (`domain/retraction.py`) can no
  longer mean "the whole file"; a retraction row now carries the specific `target_id` being
  retracted, `tier = "retracted"`.
- `record_decision` INSERT is unchanged; the write side loops per emitted decision.

## 7. Application record path (`application/decisions.py`)

`record_decision_if_changed` is the single production caller of `evaluate`. It moves from a
1:1 compare to a **set diff keyed by `(hash, target_id)`**:

1. `fresh = engine.evaluate(candidate)` → `list[MatchDecision]` (may be empty).
2. `persisted = catalog.last_decisions(hash)` → `dict[target_id, DecisionRecord]`
   (excludes the legacy `""` sentinel).
3. For each `d` in `fresh`: if `to_record(d) != persisted.get(d.target_id)` →
   `record_decision(hash, d)` (new or changed). Emit `DecisionRecorded`; nudge download if
   `d.tier == "download"`.
4. For each `target_id` in `persisted` (non-retracted) **not** in `fresh` →
   `record_retraction(hash, target_id)` (a target dropped out of the set — e.g. after a
   policy change). Emit the retraction event.
5. Return the count of rows written (0..N). Callers (`record_observations`,
   `reevaluate_catalog`) sum it instead of treating a `bool`.

This makes retraction **per target**, which is what §10 relies on to reconcile legacy rows.

## 8. Download / verification (minimal)

A whole-episode file = one hash = one physical file, downloaded once. Dedup by hash
(`is_downloaded(hash)`) stays correct. With `download_decisions` now returning both
`(hash, 072A)` and `(hash, 072B)`, the loop dedups to one queued download (whichever
candidate it processes first labels `record_queued(hash, target_id)`; the label is only for
expected-verification and is harmless). The verification verdict is per hash
(`file_verifications`), so once the single file is verified, **both** segment rows inherit
the verdict via the web UI join. No structural change to the download queue or verifier.

Documented residual: `get_target_id(hash)` (verification `_build_expected`, download queue)
returns one of the two targets. Acceptable for v1 — verification validates the media, not
the segment identity.

## 9. Web UI (`packages/webui`)

Today every read assumes one latest decision per hash; a second target would be **silently
dropped** (not duplicated) — the real risk.

**Rendering: one row per file, targets aggregated in the cell** (approved). Aligns with the
invariant "the catalog's subject is the file" and keeps counters file-based.

- Files SQL: replace the latest-decision-per-hash join with **latest per `(hash,
  target_id)`**, aggregated back to one row per hash (`catalog_read.py:37-55`, `72-83`).
- `FileRow` / `FileRowDisplay`: `target`/`title`/`tier`/`verdict` become sequences
  (`views.py:44-47`, `176-182`); the Target/Title cells render the (usually two) segments
  (`072A · 072B`, titles joined with ` · `). Tier: usually shared; if segments differ, list
  per target.
- `catalog → unidentified` mask stays per decision (`app.py:51-52`); with the fan-out an
  identified file has no `catalog` decision, so the instability the analysis flagged cannot
  arise.
- `/targets/{id}` filter (`catalog_read.py:195`, `app.py:341-362`): a file appears under
  **each** of its targets — now correct because the join is per `(hash, target_id)`.
- Counters (`_SQL_COUNT_FILES_BASE`, `catalog_read.py:118-128`): keep **file-based** with
  `COUNT(DISTINCT ed2k_hash)` so "N of 747 catalogued" stays a file count.
- File detail page already iterates `decisions` (0-or-1 today) → handles N with only the
  data layer (`_SQL_LAST_DECISION`, `FileDetail.decision`) moving to a list.

## 10. Backfill / catalog migration — no reset required

The existing catalog is **not** reset and upgrades itself on first start after deploy.

- No DDL: old rows stay valid.
- Reads take the latest verdict per `(hash, target_id)`: new decisions supersede old ones,
  nothing is corrupted.
- The policy-gated startup backfill (`run_backfill_if_policy_changed` →
  `reevaluate_catalog`, `composition/app.py:578`) re-evaluates the **whole** catalog against
  the new engine before the loops start, via the same `record_decision_if_changed`. A file
  now identified as `072A + 072B` gets both rows appended; the old arbitrary-target
  "unidentified" row (under `001A`, `catalog`) is **retracted** by §7 step 4 because it left
  the fresh set. Automatic cleanup.

Two requirements this depends on (both in scope):

- **The backfill must fire.** It is gated on the `matcher.yml`/`targets.yml` fingerprint.
  Removing `{mono_gate}` from `matcher.yml` (§5) changes the fingerprint → the backfill runs
  automatically. (A hypothetical engine-code-only change would not; §5 guarantees the YAML
  moves.)
- **Legacy-row tolerance.** The new read/diff logic must tolerate two artifacts already in
  the catalog without choking: the old retraction sentinel `target_id = ""` (ignored in the
  diff — it is not a real target), and old "unidentified" rows under an arbitrary
  `target_id` (a normal `(hash, target_id, catalog)` entry, reconciled by per-target
  retraction on the backfill pass).

## 11. Edge cases & invariants

- **Mono episode** (`074A` only): fan-out with a single-segment episode → `[074A]`. Same
  result as today, now via the fan-out path.
- **Out of range** (`112`+): no number rule matches any target → attributable set empty →
  step 6 fallback → `keroro_large` → unidentified. Unchanged.
- **Both segment titles present**: both pinned segment-level → both emitted. Handled by §3
  rule 1 (a plain "winner expansion" would have missed the second title).
- **Determinism preserved**: output sorted by `target_id`; `target_id`s are unique
  (fail-fast in `parse_targets`).
- **Invariant kept**: `keroro_large` remains the only `tier: catalog` rule and the only
  producer of "unidentified"; the golden-corpus guard
  (`test_prod_policy_has_exactly_one_catalog_tier_rule_named_keroro_large`) still holds.

## 12. Testing (TDD, 100% branch coverage per package)

- **Matching**: golden-corpus cases for each §3 table row (multi-decision assertions); mono
  still single; out-of-range still unidentified; both-titles case. Engine unit tests for the
  fan-out grouping, the segment-level suppression, the step-6 fallback, and empty output.
  Watch the coverage idioms (one-line `Protocol` stub; `assert_never` `# pragma: no cover`).
- **Persistence**: latest-per-`(hash, target_id)` reads; per-target retraction; the legacy
  `""` sentinel is ignored; `download_decisions` returns both segments.
- **Application**: set-diff writes new/changed, retracts dropped targets, is idempotent on
  re-eval; return count.
- **Web UI**: a two-target file renders one row with aggregated cells; appears under both
  `/targets/{id}`; counters stay file-based.
- Follow strict TDD: failing test first, watch it fail, minimal implementation.

## 13. Phasing (one spec, phased plan)

1. **Matching engine** (`packages/matching`): `evaluate → list`, fan-out, drop `mono_gate`,
   policy edit. Testable in isolation.
2. **Application + persistence** (`packages/crawler`): set-diff record path, per-target
   retraction, the two read queries, legacy-row tolerance.
3. **Web UI** (`packages/webui`): rendering A, counters, `/targets/{id}`, detail page.

Phases 1→2 land together (the return-type change is a hard cut across the workspace); phase
3 is read-side/presentation and can follow. The whole vertical ships as one PR (or a short
stack) so `main` never sees a half-wired engine.
