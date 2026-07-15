# Handoff: evict obvious non-episodes from the "unidentified" catch-all

Date: 2026-07-15
Branch: `fix/unidentified-non-episode-eviction` (8 commits on top of `main` 69c71a7)
Spec: `docs/specs/2026-07-15-unidentified-non-episode-eviction.md`
Plan: `docs/plans/2026-07-15-unidentified-non-episode-eviction.md`

## Context

Post-v1.0.0, before sharing the project with other lost-media researchers, we studied the
live crawler catalogue (http://localhost:8080, 1381 files, ~70 decided) and iterated on the
matching policy to stop the `keroro_large` "unidentified" catch-all from mislabelling obvious
non-episodes. This is chantier 1 of a two-part effort; chantier 2 (the `/files` explorer UI)
is specced but not yet implemented (see "Next step").

## Current state

Implemented, task-reviewed, whole-branch-reviewed, and green on the full gate
(`uv run poe check` EXIT 0: matching 253, crawler 1003, verifier 177, vex_guards 73;
lint-all + mypy strict clean; 100% branch coverage per package). Not yet merged, not yet
deployed to the real node.

## What was built (policy + one webui read fix)

All in `deploy/config/crawler/matcher.yml` (the single source of truth) plus one webui read
change. The new definition: **"unidentified" = a keroro video/archive with no non-episode
marker and no bare episode number.**

1. `fix(matching): evict comics, the movie and openings` (2052669): widened `not_episode`
   (`comic` / `doujinshi` / `同人誌` / `movie`) and re-based `keroro_large` on `is_episode`.
2. `fix(matching): evict out-of-range bare numbers` (cc72c6b): new target-agnostic token
   `episode_number` (BARE-number arm ONLY, "Approach 1") plus `{ not: episode_number }` on
   `keroro_large`. Also updated one stale engine test that encoded the old contract.
3. `fix(matching): veto the Catalan dub in foreign_lang` (2a05083, then trimmed in 8968be3):
   added the `estrenen` marker. (`\bels\b` was added then removed, see pitfalls.)
4. `fix(webui): exclude catalog-tier decisions from the target-scoped read` (d6d82e0):
   `_filter_clauses` target clause gained `AND fdt.tier != 'catalog'`, so `/targets/001A` and
   `?target=001A` stop listing the catch-all files (which the engine pins to 001A). Mirrors
   the exclusion `coverage_for` already does for the dashboard.
5. `fix(matching): drop the els marker and pin numbered-archive eviction` (8968be3) +
   `chore: rename the Voie codename to English and refresh a docstring` (1accff3): final-review
   fixes (see below).

## Learned pitfalls / decisions (do not silently revert)

- **`episode_number` is bare-number ONLY (Approach 1).** The seasonal `SxE` / `NNxNN` forms
  are deliberately NOT in the token. Reason: `segment_id` matches those forms only WITH the
  A/B letter, so an in-range whole-episode reference without a letter (`Keroro S2E11.mkv`,
  `Keroro 01x37.mkv`) legitimately sits in "unidentified"; an eviction token including those
  arms would wrongly discard it. Approach 2 (make `SxE`/`NNxNN`-without-letter fan out and
  match like a bare number, so `01x37` identifies episode 37) was scoped out as a possible
  future chantier.
- **The eviction is range-agnostic and hits ARCHIVES too (operator-approved, Option A).** The
  whole-branch review flagged that `numero_nu` needs `is_video`, so an in-range bare-number
  ARCHIVE with no title/source signal (e.g. `[Keroro].062.zip`) matches no rule and is now
  DISCARDED rather than kept as unidentified. The operator chose to accept this (Option A) over
  exempting archives (Option B). Pinned by the golden case `in_range_numbered_archive_is_discarded`
  so it is not later "fixed" as a bug. If a real in-range numbered archive that should be kept
  ever surfaces on the node, revisit Option B (gate only the video path with `{not: episode_number}`).
- **`\bels\b` was removed.** It carried a low-but-nonzero risk of silently discarding a kept
  French file containing a bare "els" token; `estrenen` alone evicts the observed Catalan file.
  If a Catalan sibling with no `estrenen` appears, add a more specific Catalan cue (not the bare
  article).
- **Propagation is automatic via the re-evaluation backfill.** Changing `matcher.yml` changes
  the policy fingerprint, so on the next crawler restart the backfill re-evaluates every
  catalogued file and appends silent `retracted` rows for the now-evicted files (no
  notification). No manual backfill script.
- The real theatrical-movie file (`[T-N]Keroro_Gunsou_Movie...`) is already vetoed by
  `foreign_lang` "Gunsou"; the new `movie` marker is defensive for a Gunsou-less English "Movie".
- The internal codename in the spec/plan/comments is "Approach 1 / Approach 2" (English), not
  "Voie" (which now only survives as the French common noun in the French runbook table).

## NOT yet validated against real hardware

- The eviction has NOT run against the live catalogue. It only takes effect after the new
  `matcher.yml` is deployed and the crawler is restarted (the fingerprint gate triggers the
  one-time re-evaluation). Predicted effect: "unidentified" shrinks from 37 to ~2-5 files
  (manga zips, the movie, the opening, the Catalan dub, and the out-of-range `[Keroro].104+`
  cohort all retracted); `Keroro.avi` / `keroro.zip` stay.
- The `/targets/001A` de-pollution is unit-tested but not eyeballed on the real node.

## Next step

1. Merge this branch (PR: CI `validate / gate` is required on `main`).
2. Deploy the new `matcher.yml` to the real node (`/home/geoffrey/Projets/2026-06-29 keroro
   emule`, which reads its own config, not the repo's `deploy/`) and **restart the crawler**
   (remember: amuled shares gluetun's netns, so restart order matters). Then verify on
   http://localhost:8080 that "unidentified" shrank and `/targets/001A` no longer lists the
   catch-all files. Watch for any false eviction (a kept file wrongly discarded) as the
   re-evaluation runs.
3. Then chantier 2: the `/files` explorer UI (sortable columns, filename search, tier filter,
   live tier counts). Spec already committed: `docs/specs/2026-07-15-files-explorer-sort-search-filter.md`.
   Its live tier counts will reflect the cleaned catalogue, which is why chantier 1 went first.
