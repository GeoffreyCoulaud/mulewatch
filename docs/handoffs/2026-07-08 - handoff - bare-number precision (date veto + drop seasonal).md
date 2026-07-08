# Handoff: bare-number precision (date veto + drop the seasonal alternative)

- Date: 2026-07-08
- Branch: `fix/bare-number-precision` (PR pending; not yet merged)
- Spec: `docs/specs/2026-07-08-bare-number-precision.md` · Plan: `docs/plans/2026-07-08-bare-number-precision.md`
- Commits: `53b0171` (test refactor), `e91bc9d` (the fix)

## Current state

The multi-target bare-number rules were over-matching on the live catalogue. Diagnosed by running
the shipped policy against the operator's real catalogue (crawler webui on `localhost:8080`) and
by re-running the engine locally over the real targets. Fixed in config + tests only, no engine or
production code. Whole workspace green: `uv run poe check` exit 0, matching 239 / crawler 990 /
verifier 176 / vex_guards 73, 100% branch coverage per package.

## The problem (grounded in the live catalogue)

The canonical, correctly named Teletoon rip
`[TV] KERORO MISSION TITAR N°062A ... [Dimanche 21 septembre 2008 ... TELETOON].avi` produced
**five** decisions, all `download`: `021A, 021B, 062A, 072A, 072B`. Only `062A` is real. Two
compounding causes, both in the `segment_id_loose` token:

1. **Incidental numbers.** `segment_id_loose` matched any bordered number 1..103 anywhere, so the
   broadcast-date day-of-month (`21` in `21 septembre 2008`) was read as an episode number. Hours
   (`16H50`) and years (`2008`) were already safe.
2. **Double-numbering collision.** A bare `n <= 52` matched both `absolute_number = n` and
   `seasonal_number = n` (= `absolute 51 + n`), so `21` also matched episode 72 (S2E21).

A source marker (TELETOON) then pushed the false positives into `download`, and a false `072`
decision marks a still-lost segment as "found" (false completeness).

Not a problem, confirmed on the data: the ~1200 discarded files are foreign-language versions
(correctly vetoed); the `[Keroro].0NN.` whole-episode files match correctly.

## What was built

- **Policy (`matcher.yml`, one token).** `segment_id_loose` gains a local date veto (French month
  names, full + common abbreviations, in folded form; and `jj/mm`, `jj-mm`, `jj.mm` numeric dates)
  and drops the `{seasonal_number}` alternative. Explicit seasonal numbering (`S02E21`, `2x21`)
  still matches via the unchanged exact `segment_id`. Chosen over the simpler "require 3-digit
  zero-padded" because the operator wants recall-first (nothing forces an uploader to zero-pad).
- **Test-fixture reorganization (`53b0171`).** The golden corpus `catalog` cases pinned
  `target_id: 062A`, a pure min-key artefact of the present target set: adding a target would flip
  and break them. Added a target-agnostic `unidentified: true` case type (symmetric to
  `discarded`) that asserts one `catalog`/`keroro_large` decision without pinning the target_id,
  and migrated the six catalog cases. Updated `test_corpus_covers_every_tier_and_a_discard`.
- **Guards (`e91bc9d`).** Enriched `golden_targets.yaml` with the 021/072 collision pair and added
  red->green guards (bare 21 -> 021 only; numeric and abbreviated-month date veto -> 062A only;
  date-only -> unidentified; `62 marseille` unchanged, proving the `\b` anchor).

## Learned pitfalls (for the next effort)

- **`golden_targets.yaml` is a SHARED cross-package fixture.** It is loaded by the matching golden
  corpus AND by `packages/crawler/tests/application/conftest.py` (via `parents[4]`). Enriching it
  moved the catch-all min-key from `062A` to `021A`, which broke three crawler application tests
  (`test_decisions.py` x2, `test_record_observations.py` x1) that pinned `062A` for an unidentified
  file: the exact same anti-pattern as the golden corpus, in a second place. Fixed by pinning a
  **stable** target (`062A/notify` via its unique title) instead of the arbitrary catch-all. When
  you touch this fixture, remember both consumers.
- **A targeted pre-check missed it; only the full `uv run poe check` surfaced it.** The recon ran
  `test_prod_targets`/`test_app`/`test_main`/`test_policy_fingerprint` (which pass) but not the
  `application/` suite. Run the whole gate before trusting "no regressions".
- **Regex backslashes are doubled in the YAML double-quoted string** (`\\s`, `\\b`, `\\d`, `\\-`),
  matching the other tokens. Lookaround is allowed since RE2 was dropped (2026-07-03).
- **The `\b` after each month is a real guard**: it stops a longer word (`marseille`, `novateur`,
  `octet`) from being vetoed by a month-abbreviation prefix. Proven by a characterization case.

## NOT validated against real hardware

Everything is validated by unit tests (100% branch) and by re-running the engine locally over the
real `targets.yml` + real filenames. NOT yet exercised on the live node:

- **The startup backfill re-evaluating the real `catalog.db`.** On the next deploy the
  `matcher.yml` fingerprint changes, so `run_backfill_if_policy_changed` re-evaluates the catalogue
  and should per-target-retract the stale `021A/021B/072A/072B` rows of the real 062A file. Watch
  the `catalogue re-evaluated: N files, M rows written` log line, then confirm in the webui that
  the file renders `062A` alone (it currently shows `021A · 021B · 062A · 072A · 072B`).

## Suggested next step

Deploy to the observer node, let the backfill run, confirm the real 062A file resolves to `062A`
alone. Then revisit the deferred multi-match tuning (`multi-match-needs-real-catalog-tuning`): this
fix closes the date/collision class; watch the re-evaluated catalogue for any remaining odd
decisions. Out of scope by operator decision (2026-07-08): mojibake filenames (`NÂ°062A`) losing
their exact-id match; additional date forms (`le 21` prefix, other locales).

## Adjacent cleanup noted (not done)

Pre-existing long dashes outside the changed regions: `matcher.yml:1` and `:29` (line 1 is also a
French header comment) and `test_record_observations.py:207`. Left untouched to keep this fix
focused; a separate chore can address them.
