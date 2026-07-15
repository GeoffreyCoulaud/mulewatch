# Evict obvious non-episodes from the "unidentified" catch-all

Status: approved in brainstorming (2026-07-15), pending spec review.
Related: `2026-07-04-catalog-reevaluation.md` (the backfill that propagates a policy
change), `2026-07-08-bare-number-precision.md` (the last `segment_id_loose` tuning and
its TDD pattern), `2026-07-06-multi-target-matching.md` (per-target fan-out decisions).

## 1. Problem (observed on the live catalogue, 2026-07-15)

The live node holds 1381 catalogued files. Only ~70 carry a live match decision:
1 `download` (the real jackpot, `[TV] KERORO MISSION TITAR N°062A ... TELETOON].avi`),
32 `notify` (the bare-number `numero_nu` cohort), and **37 `catalog`** ("unidentified",
the `keroro_large` catch-all). The catch-all is the problem surface: it labels as
"unidentified episode candidate" a pile of files that are plainly **not** Titar episodes.

Taxonomy of the 37 `catalog` files:

| Category | ~count | Example | Nature |
|---|---|---|---|
| Out-of-range bare numbers | ~23 | `[Keroro].155.[Xvid.mp3].[...].mkv` | Same release cohort we keep at `notify`, but the number (104..) is beyond the last target (103). |
| Manga / doujinshi (`.zip`) | ~8 | `[comic][.].vol.09.zip`, `(.) Keroro Na Seikatu 2.zip` | Comics, not video-anime (the CJK `軍曹` escapes `foreign_lang`; `.zip` is a generic archive). |
| The theatrical movie | 1 | `[T-N]Keroro_Gunsou_Movie[...]DVD.avi` | Not a broadcast episode. |
| Opening | 1 | `Srg Keroro - Opening 1.avi` | `not_episode` already exists but `keroro_large` never applied it. |
| Foreign language slipped through | 1 | `Srg Keroro s01e29 ... En Keroro I Els Paparazzi` (Catalan) | Escaped `foreign_lang`. |
| Generic / ambiguous | 2 | `Keroro.avi`, `keroro.zip` | Genuinely unidentifiable keroro content. |

Two **distinct** natures, per operator: out-of-range files are wrong because of their
**number** (not their format); manga / movie / opening are wrong because of their
**content**. The operator wants both evicted, on the correct axis each, while the in-range
bare `[Keroro].NNN` cohort (065..100) stays exactly as-is (kept even if it might be a false
positive: the Japanese-vs-French numbering distinction is deliberately not made).

Secondary leak: every `catalog` decision is stored with `target_id = 001A` (the smallest
target, picked by the engine's min-key tie-break on a target-agnostic rule). `coverage_for`
already excludes `catalog` from episode **status** (dashboard is clean), but the
target-scoped read does not: `/targets/001A` and `?target=001A` on `/files` list all 37
catch-all files as if they belonged to episode 1.

## 2. Decision (operator-approved)

New definition of the catch-all:

> **"unidentified" = a keroro video/archive with no non-episode marker and no *bare*
> episode number.**

Concretely, five changes:

1. **Widen `not_episode`** to also cover comics and the movie (one widened token, per
   operator preference: no new `comic`/`movie`/`non_episode` tokens).
2. **Add one target-agnostic token `episode_number`** = the **bare-number arm only**
   (`\d{2,3}` with `segment_id_loose`'s date vetoes). See "Voie 1" below for why only the
   bare arm.
3. **`keroro_large`** switches its base from `is_keroro` to `is_episode` (inheriting the
   `not_episode` exclusion) and gains `{ not: episode_number }`.
4. **Extend `foreign_lang`** with Catalan markers. With `episode_number` bare-only, the
   Catalan file's `s01e29` is no longer read as a number, so `foreign_lang` is now the
   **sole** mechanism that evicts it (and closes the axis for any bare-number Catalan sibling).
5. **Fix the 001A leak at the read layer** (webui only, no engine change): the target filter
   excludes `catalog`-tier rows, extending the `tier != 'catalog'` pattern `coverage_for`
   already uses.

### Voie 1: `episode_number` is the bare-number arm ONLY (decided 2026-07-15)

The eviction rests on: *"a file that reaches the catch-all while carrying a number must be
out of range"* (an in-range number would have been claimed by `numero_nu`). **This holds
only for the bare-number form.** `numero_nu` fans a bare number out to both segments without
requiring a segment letter, so an in-range bare number never reaches the catch-all as
`catalog`. The `SxE` / `NNxNN` forms are different: `segment_id` matches them **only with the
A/B letter**, so an in-range whole-episode reference like `Keroro S2E11.mkv` or
`Keroro 01x37.mkv` (no letter) legitimately sits in "unidentified" today (corpus case
`seasonal_episode_no_segment_falls_back_to_catalog`). Putting `SxE`/`NNxNN` arms into an
**eviction** token would wrongly **discard** those in-range references, losing real episode
candidates and regressing the corpus. So `episode_number` is bare-only.

Deferred (Voie 2, out of scope here): making `SxE`/`NNxNN`-without-letter references
**fan out and match** like a bare number (a `seasonal_loose` token + two rules mirroring
`numero_nu`). That would let `01x37` identify episode 37 and then let `episode_number` safely
include the seasonal arms. A separate chantier if wanted; YAGNI for now.

Rejected alternative (structured "kind" classification: manga/movie/opening as filterable
tiers): heavier, breaks the single-catalog-rule guard, touches the schema/display, and the
operator wants nothing deleted, only a misleading label removed. Nothing is deleted here
either: observations remain; only the live decision changes.

## 3. Policy change (`deploy/config/crawler/matcher.yml`)

`deploy/config/` is the single source of truth (the matching tests read it via `parents[N]`).

Before:

```yaml
not_episode:   { regex: "opening|ending|g[eé]n[eé]rique|\\bsample\\b|preview|trailer|bande.?annonce" }
is_episode:    { all: [is_keroro, { not: not_episode }] }
# ...
- { name: keroro_large, tier: catalog, all: [is_keroro, { any: [is_video, is_archive] }] }
```

After:

```yaml
not_episode:   { regex: "opening|ending|g[eé]n[eé]rique|\\bsample\\b|preview|trailer|bande.?annonce|\\bcomic\\b|\\bdoujin(?:shi)?\\b|同人誌|\\bmovie\\b" }
episode_number: { regex: "(?:^|[^0-9A-Za-z])\\d{2,3}(?!\\s*(?:janv?(?:ier)?|fevr?(?:ier)?|mars|avr(?:il)?|mai|juin|juil(?:let)?|aout|sep(?:t(?:embre)?)?|oct(?:obre)?|nov(?:embre)?|dec(?:embre)?)\\b)(?!\\s*[/.\\-]\\s*\\d)(?:[^0-9A-Za-z]|$)" }
is_episode:    { all: [is_keroro, { not: not_episode }] }
# ...
- { name: keroro_large, tier: catalog, all: [is_episode, { any: [is_video, is_archive] }, { not: episode_number }] }
```

`foreign_lang` gains Catalan markers (exact set tuned in TDD against the corpus; a Catalan
episode presents `s'estrenen` and `... els ...`). The precise tokens are a plan-time detail;
the requirement is: the live Catalan file above is evicted, and a Catalan filename with a
bare in-range number cannot reach `notify`.

Design notes:
- **`episode_number` is `segment_id_loose` minus the `0*{absolute_number}` interpolation**:
  the same `\d{2,3}` between non-alphanumeric boundaries with the same date vetoes (French
  month names, `dd/mm` / `dd.mm` / `dd-mm`). So `21 septembre 2008` is not read as episode 21,
  a 4-digit year never matches (`\d{2,3}` between boundaries), a resolution `640x480` never
  matches (3+ digits per side, no bounded 2-3 digit run), and a CRC `[D6A10367]` never matches
  (hex-bordered, no non-alphanumeric before the digits).
- Compiles under stdlib `re` with `re.ASCII` (no interpolation, plain `\d`).

## 4. The 001A read-layer fix (`webui/adapters/catalog_read.py`)

In `_filter_clauses`, the `target` clause today is:

```sql
EXISTS (SELECT 1 FROM latest_dec AS fdt WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.target_id = ?)
```

It becomes:

```sql
EXISTS (SELECT 1 FROM latest_dec AS fdt
        WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.target_id = ? AND fdt.tier != 'catalog')
```

So a `catalog`-tier decision (always pinned to `001A`) never makes a file appear under a
target's scope. This mirrors `coverage_for`'s existing `d[1] != "catalog"` rule; the two are
now consistent. No engine change, no migration. (Rejected: emit a sentinel `target_id` for
`catalog` decisions in the engine, more invasive; the project already chose "exclude catalog
at read" in `coverage_for`.)

## 5. Propagation to the existing catalogue (automatic)

No manual backfill. Per `2026-07-04-catalog-reevaluation.md`: editing `matcher.yml` changes
the **policy fingerprint**, so on the next crawler restart the re-evaluation backfill runs
over every catalogued file. Files that matched `keroro_large` but now match nothing get an
appended **retraction** row (`target_id=""`, `tier=retracted`); the webui already treats
`retracted == unmatched`, so the evicted files silently drop out of "unidentified".
Retractions are **silent** (log + `decisions{tier=retracted}` metric, no notification). The
in-range `numero_nu` cohort re-evaluates to the same `notify` decisions, so the
anti-redundancy guard writes nothing for them.

Operator action: restart the crawler after deploying the new `matcher.yml` (the fingerprint
gate triggers the one-time re-evaluation). Verify on the real node per the "verify DB/UI
changes in the real container" habit.

## 6. Predicted behaviour on the real catalogue

- `[Keroro].104..158`: `episode_number` (bare) true, no target in range -> **retracted**.
- `[comic]...zip`, `(同人誌)...zip`: `not_episode` (comic/doujinshi/同人誌) true -> **retracted**.
- `Keroro_Gunsou_Movie...avi`: `not_episode` (movie) true (and `foreign_lang` "Gunsou" too)
  -> **retracted**.
- `Srg Keroro - Opening 1.avi`: `not_episode` (opening) true -> **retracted**.
- `Srg Keroro s01e29 ... Els Paparazzi`: evicted by the **`foreign_lang` Catalan markers**
  (NOT by `episode_number`, which is bare-only and does not read `s01e29`) -> **retracted**.
- `Keroro.avi`, `keroro.zip`: no number, no marker -> **stay "unidentified"** (true candidates).
- `Keroro S2E11.mkv`, `Keroro 01x37.mkv` (in-range whole-episode, no segment letter):
  `episode_number` bare-only does NOT match -> **stay "unidentified"** (unchanged; Voie 1
  deliberately does not discard them).
- `[Keroro].065..100` (`numero_nu`, `notify`): unchanged (the numbered rule outranks the
  catch-all; `{ not: episode_number }` on `keroro_large` cannot demote a `notify` file).
- `N°062A ... TELETOON` (`id_segment_exact`, `download`): unchanged.

Net: `catalog`/"unidentified" shrinks from 37 to ~2-5 genuinely-unidentifiable files.

## 7. Testing (strict TDD, 100% branch per package)

Matching package (`packages/matching`), following the `2026-07-08` pattern (the reduced
target fixture + golden corpus read the real `deploy/` policy via `parents[N]`):

- **Corpus guards, red on the old policy, green on the new** (add real filenames as cases):
  - out-of-range bare number (`[Keroro].155...mkv`) -> `discarded`.
  - Japanese comic zip (`[comic][軍曹]...vol.09.zip`) -> `discarded`.
  - movie (`Keroro_Gunsou_Movie...avi`) -> `discarded`.
  - Catalan file (`Srg Keroro s01e29 ... Els Paparazzi.avi`) -> `discarded` (via the
    `foreign_lang` extension).
- **Existing corpus case that flips (intended):**
  - `not_episode_opening_demotes_title_to_catalog`
    (`Keroro Les demoiselles cambrioleuses opening.avi`): `unidentified` -> `discarded`
    (opening now excluded from `is_episode`). Update the expectation and its comment.
- **Non-regression (stay green):**
  - in-range bare number (`Keroro 62.avi`) -> `notify` fan-out on `062A`+`062B` (`numero_nu`).
  - `N°062A ... TELETOON` -> `download` on `062A`.
  - `Keroro rediffusion.mkv`, `Keroro rediffusion.zip` -> `unidentified` (keroro_large fires).
  - `Keroro S2E11.mkv` -> `unidentified` (Voie 1: bare-only does not discard it). Keep the
    existing case; add `Keroro 01x37.mkv` -> `unidentified` as an explicit Voie-1 guard.
  - the single-catalog-rule guard (`keroro_large` still the only `tier: catalog` rule).
- **`episode_number` token unit tests** (reuse the `_is_archive_matcher()` pattern:
  `_episode_number_matcher()` builds a `RegexMatcher` from the shipped token). Matches: a bare
  `.104.` / ` 155 `. Non-matches (both branches): `21 septembre` (date veto), a 4-digit year
  `2008`, a resolution `640x480`, a CRC-bordered `[D6A10367]`, and `s01e29` / `01x37` (no bare
  arm hit, confirming the seasonal forms are not evicted).

Crawler package (`packages/crawler`), read-layer:

- `catalog_read` test: a file with a `catalog`-tier decision (`target_id=001A`) is **not**
  returned by `list_files(target='001A', ...)` nor counted by `count_files(target='001A')`;
  a `notify` file on `001A` still is. Both sides of the new `tier != 'catalog'` clause.

## 8. Non-goals

- No structured "kind" classification (manga/movie/opening tiers). Explicitly rejected.
- No `SxE`/`NNxNN` whole-episode matching (Voie 2) in this chantier; deferred.
- No engine change (the target-agnostic `catalog` `target_id=001A` stays; only the read hides it).
- No change to the in-range `numero_nu` cohort semantics.
- No manual/one-shot backfill script: the existing re-evaluation gate does it on restart.
- No anti-ReDoS work (operator-owned policy; unchanged posture).

## 9. Files touched

- `deploy/config/crawler/matcher.yml` (widen `not_episode`, add `episode_number`, re-base
  `keroro_large`, extend `foreign_lang`).
- `packages/matching/tests/fixtures/golden_corpus.yaml` (new/updated cases) and
  `packages/matching/tests/test_golden_corpus.py` (the `episode_number` token unit tests).
- `packages/crawler/src/mulewatch/webui/adapters/catalog_read.py` (`_filter_clauses` target clause).
- `packages/crawler/tests/webui/test_webui_catalog_read.py` (read-layer test).
- No `.sql` migration, no schema change.
