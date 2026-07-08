# Bare-number precision: date veto + drop the seasonal alternative

- Status: draft (awaiting operator approval)
- Date: 2026-07-08
- Branch: `fix/bare-number-precision`
- Follows: `2026-07-06-multi-target-matching.md` (this tunes the policy that work shipped)

## 1. Problem (observed on the live catalogue)

The multi-target engine emits one decision per segment target a file covers. Run against the
operator's real catalogue (crawler on `localhost:8080`, ~1218 files), the bare-number rules
(`numero_nu` / `numero_nu_confirmed`, both keyed on the `segment_id_loose` token) over-match on
**incidental numbers** in the filename. The canonical, correctly named Teletoon rip:

```
[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » [Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi
```

currently produces **five** decisions, all in `download`:

```
021A, 021B, 062A, 072A, 072B
```

Only `062A` is real (pinned by the exact `N°062A` via `id_segment_exact`). The other four are
spurious, and they are traced to two compounding causes:

1. **Incidental numbers match as episode numbers.** `segment_id_loose` matches any bordered
   number 1..103 anywhere in the name. The day-of-month of the broadcast date (`21` in
   `21 septembre 2008`) is read as an episode number. Proven by decomposition: stripping the date
   leaves `062A` alone; the date alone yields `021` + `072`. Hours (`16H50`) and years (`2008`)
   are already safe (the `h`/letter border, and 2008 > 103).

2. **Double-numbering collision.** A bare number n <= 52 matches both `absolute_number = n` and
   `seasonal_number = n` (which belongs to `absolute_number = 51 + n`). So `21` matches episode 21
   **and** episode 72 (S2E21); `03` matches 03 and 54. Every bare number <= 52 fans out to two
   unrelated episodes.

A third factor turns this from cosmetic noise into real cost: those same TV rips carry a
`source_marker` (TELETOON), so `numero_nu_confirmed` places the false positives in the `download`
tier. And because the catalogue's purpose is to track which lost segments are recovered, a bogus
`072` decision marks a still-lost segment as **found**: a false completeness signal, worse than
plain noise.

At the current catalogue scale the bug is visible on one logical file (three encodings), because
only that file carries a broadcast date. But that file is the template for a well-named Teletoon
rip: every future one will carry its air date and hit this.

Not a problem, confirmed on the live data: the ~1200 discarded files are foreign-language versions
(Sarxento/gallego/japones, doujin), correctly vetoed by `foreign_lang`. The `[Keroro].0NN.`
whole-episode files match correctly. This spec does not touch either.

## 2. Decision (operator-approved)

Tighten the bare-number token `segment_id_loose` with two edits; **no engine code changes**.

### 2.1 Date veto (keep 2-digit recall)

The bare number must not match when it is part of a date. Two negative lookaheads, local to the
number (not a file-level veto, which would also suppress a legitimate episode number present in the
same filename):

- not followed by a French month name, and
- not followed by a numeric date separator + digit (`jj/mm`, `jj-mm`, `jj.mm`).

Month names cover the **full name and common abbreviations** (`sept`/`septembre`, `janv`/`jan`,
`dec`, ...), written in **folded** form because `RegexMatcher` matches on `fold(filename)` (NFKD +
strip diacritics + casefold): `aout` (not `août`), `fevrier`, `decembre`. `\b` stays ASCII under
`re.ASCII`. The trailing `\b` on each month is a guard: a longer word that merely starts like a
month abbreviation is **not** vetoed (`marseille` / `novateur` / `octet` after a bare number still
match; only a real `mars` / `nov` / `oct` token vetoes). A bare number immediately followed by a
month is treated as a date even without a year (`62 mai` -> unidentified), an accepted trade of a
rare edge for date robustness.

Rejected alternative (recorded): requiring the bare number to be a **3-digit zero-padded** absolute
(`001`..`103`) would also exclude dates (day = 1-2 digits, year = 4) and is simpler, but it drops
2-digit bare-number recall (`Keroro 62` -> unidentified). Per the operator's recall-first stance
for lost media (nothing forces an uploader to zero-pad; a false positive is cheap, a missed episode
is not; refine from observed data over time), the date veto is preferred. The 3-digit option stays
on the table if the veto proves fragile.

### 2.2 Drop the `{seasonal_number}` alternative

Remove `{seasonal_number}` from `segment_id_loose` so a bare number designates only its
`absolute_number`. This kills the collision. Explicit seasonal numbering (`S02E21`, `2x21`) is
still matched by the exact `segment_id` token, which is unchanged. The recall lost is only the
**bare, season-marker-less** seasonal form, which no real catalogue file uses (all use the 3-digit
absolute). Reversible if bare-seasonal data later appears.

Note: `{seasonal_number}` remains a live interpolation placeholder (used by `segment_id`), so
`interpolation.py` is unchanged.

## 3. Policy change (`matcher.yml`)

`segment_id_loose`, before:

```
(?:^|[^0-9A-Za-z])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9A-Za-z]|$)
```

after:

```
(?:^|[^0-9A-Za-z])0*{absolute_number}(?!\s*(?:janv?(?:ier)?|fevr?(?:ier)?|mars|avr(?:il)?|mai|juin|juil(?:let)?|aout|sep(?:t(?:embre)?)?|oct(?:obre)?|nov(?:embre)?|dec(?:embre)?)\b)(?!\s*[/.\-]\s*\d)(?:[^0-9A-Za-z]|$)
```

Lookaheads are permitted since RE2 was dropped (2026-07-03). The pattern still compiles under
`re.ASCII` (validation's compile check passes).

## 4. Behaviour (prototyped against the real `targets.yml`)

| Filename | Before | After |
|---|---|---|
| real `N°062A … 21 septembre 2008 … TELETOON` | `021A,021B,062A,072A,072B` (download) | `062A` (download) |
| `N°062A … 21/09/2008 … TELETOON` | 062A + date fan-out | `062A` (download) |
| `[Keroro].072.[Xvid.Mp3].[hash].avi` | `072A,072B` (notify) | `072A,072B` (notify) |
| `Keroro 21.avi` (no date) | `021A,021B,072A,072B` | `021A,021B` |
| `Keroro 03.avi` (no date) | `003A,003B,054A,054B` | `003A,003B` |
| `Keroro 62 teletoon.avi` | `062A,062B` (download) | `062A,062B` (download) |
| `KERORO … 21 septembre 2008 TELETOON.avi` (no id) | date fan-out (download) | unidentified |

## 5. Testing (TDD, 100% branch per package)

The change is config + tests only, so the risk is a golden corpus that does not actually guard the
fix. Today it does not: `golden_targets.yaml` holds only 062A/062B/094A, so no collision target
exists and `21 septembre` matches nothing there. That is exactly why the bug shipped. Enriching the
targets is required, but it first exposes a latent fixture flaw that must be fixed.

### 5.1 Fixture reorganization (prerequisite, no behaviour change)

The `catalog`-tier cases assert a `target_id` that is a pure artefact: for an unidentified file the
catch-all (`keroro_large`) picks the **min-key** target_id over the present targets. Asserting it
couples the case to the target set, so merely *adding* a target (021, whose `021A` < `062A`) would
flip the six existing `catalog` cases from `062A` to `021A` and break them. The fixture, not the
change, is at fault.

Fix: add an `unidentified: true` case type to the corpus harness (`test_golden_corpus.py`),
symmetric to the existing `discarded: true`. It asserts `len(decisions) == 1`, `tier == "catalog"`,
`rule_name == "keroro_large"`, and does **not** assert the target_id (exactly what the webui checks
to render "unidentified"). Migrate the six existing `catalog` cases to it (each keeps its real
intent: opening-demoted, seasonal-without-letter, resolution-not-a-segment, hash-digits-not-a-number,
bare archive, keyword-only). Also update `test_corpus_covers_every_tier_and_a_discard`, which counts
tiers via the case's `tier` field, to count an `unidentified` case as the `catalog` tier. This makes
the corpus robust to adding any target.

### 5.2 Enrich the reduced target fixture

Add episodes 021 (S1E21, absolute 21) and 072 (S2E21, absolute 72) to `golden_targets.yaml`. They
share `seasonal_number = 21`, the collision pair; both are two-segment. Safe now that catalog cases
no longer pin a target_id.

### 5.3 Guards for the fix (red on the old policy, green on the new)

With the targets enriched, the existing `real_62A_full_release` and `ascii_no_accents_62A` (which
carry `21 septembre 2008`, expecting `062A` alone) become red->green guards: on the old policy they
produce the 021/072 fan-out (red), on the new policy they return `062A` alone (green). Add:

- `Keroro 21.avi` -> `021A + 021B` only (red on the old policy: also 072) proves the dropped
  seasonal alternative.
- `... N°062A ... 21/09/2008 ... TELETOON` -> `062A` only: numeric-date veto.
- `... N°062A ... 21 sept 2008 TELETOON` -> `062A` only: abbreviated-month veto.
- date-only `... 21 septembre 2008 TELETOON` (no exact id) -> `unidentified`: the bare date no
  longer fabricates an episode.
- `Keroro 62 marseille.avi` -> `062A + 062B` (unchanged): characterization guard that the `\b`
  anchor does not veto a longer word merely starting like a month.

Verify the red->green cycle: run the corpus after 5.1 + 5.2 and confirm the fix guards fail; apply
the policy; confirm green. No engine unit test references `segment_id_loose`'s body (confirmed:
`test_engine`'s real-62A test uses only 62A/62B, so it stays green); confirm `test_resolver` /
`test_matchers` / `test_interpolation` stay green during Act.

## 6. Non-goals

- **Mojibake filenames** (`NÂ°062A`, double-encoded) losing their exact-id match: out of scope,
  will not be handled (operator decision, 2026-07-08).
- **Additional date forms** (`le 21` prefix, other locales, exotic separators): deferred, added
  from observed data when needed (operator decision, 2026-07-08). French month names (full +
  common abbreviations) and `jj/mm`, `jj-mm`, `jj.mm` numeric dates are covered from the start.

## 7. Migration / backfill

Editing `matcher.yml` changes the policy fingerprint, so the startup backfill
(`run_backfill_if_policy_changed` -> `reevaluate_catalog`) fires automatically on the next deploy,
re-evaluates the whole catalogue, and per-target-retracts the stale 021/072 rows of the 062A file.
No DDL, no reset. Watch the `catalogue re-evaluated: N files, M rows written` log line.

## 8. Files touched

- `packages/matching/tests/test_golden_corpus.py` (test harness: `unidentified` case type +
  completeness-guard update; no engine code).
- `packages/matching/tests/fixtures/golden_corpus.yaml` (migrate 6 catalog cases to `unidentified`;
  new + strengthened date/collision cases).
- `packages/matching/tests/fixtures/golden_targets.yaml` (add 021 + 072).
- `deploy/config/crawler/matcher.yml` (one token: `segment_id_loose`).
- No engine / production code. Change reaches `main` through a PR (CI `validate / gate`).
