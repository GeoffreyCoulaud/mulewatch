# Handoff — matcher precision + canonical target_id + files-page cleanup

- Date: 2026-07-04
- Branch: `feat/matcher-precision-and-files-ui` (worktree)
- Spec: `docs/specs/2026-07-04-matcher-precision-and-files-ui.md`
- Plan: `docs/plans/2026-07-04-matcher-precision-and-files-ui.md`
- Suggested tag: `v0.23.0-matcher-precision`

## Why this happened

First live run (gluetun + High-ID + download). The webui `/files` page showed
29 matched files of 726 catalogued — and **not one was French**: Taiwanese R3
DVD rips (`BIG5`), a Spanish dub (`XeTe`), manga/doujinshi ZIPs, an opening.
That is a legitimate mission finding (the VF is rare — lost media). But it
exposed three false-positive mechanisms in the matcher and two display warts.
The operator's call: harden the matcher (don't compensate in the UI), and the
absence of true positives is expected, not a concern.

## What was built (3 tasks + a fix wave, all reviewed)

1. **Canonical `target_id` → absolute form** (`refactor`, commit `0eb0db2`).
   `S2E062A` → `062A` (`f"{absolute_number:03d}{segment.upper()}"`). The old
   form mixed season + absolute (a chimera). The id is an **opaque key, built in
   one place** (`models.py` property) and never re-parsed — verified — so this
   was a one-line source change + a uniform mechanical sweep of test/fixture
   literals across all 4 packages (`S[12]E(ddd)(L)` → `\1\2`). Seasonal form is
   now display-only.

2. **Harden the matcher** (`feat`, commit `ed5c083`), in the operator-owned
   `deploy/config/crawler/matcher.yml`:
   - `foreign_lang` += `BIG5`, `R3_DVDRIP`, `XeTe`. Because `is_keroro`
     (= `french_safe ∧ keroro_titar`) gates **every** rule incl. `keroro_large`,
     a foreign match now yields **no decision at all** — foreign Keroro files
     drop out of the matched view entirely (still raw-catalogued as
     observations). This single change kills *all* observed false positives
     (the hash-digit ones too — those files are `BIG5`, gated before the number
     ever matters; and the Spanish `title_hit`, gated before coverage runs).
   - `segment_id_loose` boundaries `[^0-9]` → `[^0-9A-Za-z]` — a digit inside a
     CRC hash tag (`[7C094A47]`) no longer matches an episode number. Structural
     defense for any future genuine-French file.
   - Regression fixtures added to the golden corpus (real BIG5/XeTe names →
     discarded; a synthetic french-safe hash case → catch-all, not notify).

3. **`/files` page cleanup** (`feat`, commit `37c9860`), webui:
   - Target column `062A / S02E11A` (canonical / seasonal locator), resolved
     from the already-loaded targets catalog; `unidentified` when
     `tier == catalog`. New **Title** column. Human size, trimmed timestamp,
     `pending` verdict, tier legend. All display, no domain change.

4. **Fix wave** (final review): guard test asserting the prod policy has exactly
   one `catalog`-tier rule (`2cf5b41`) + `human_size` unit-promotion fix
   (`6d95a87`).

Gate green end-to-end: matching 224 / crawler 731 / verifier 176 / webui 137,
100 % branch per package, mypy `--strict` / ruff / sqlfluff / template-check
clean.

## Learned pitfalls (don't relearn these)

- **`tier == catalog` ⟺ the catch-all.** `keroro_large` is the *only* catalog
  rule and is target-agnostic, so it matches every target and the tiebreak
  always picks the smallest id — every catalog row is "unidentified", never a
  real episode. The webui display and a guard test
  (`test_golden_corpus.py::test_prod_policy_has_exactly_one_catalog_tier_rule_named_keroro_large`)
  now depend on this; a comment at the `keroro_large` rule flags it. Adding a
  second catalog-tier rule will (correctly) break that test.
- **`mono_gate` masks `segment_id_loose` on multi-segment targets.** To
  regression-test the boundary fix, the golden subset needed a **mono** target
  (`094A` added to `golden_targets.yaml`) — a bi-segment target short-circuits
  the loose number matcher entirely.
- **`foreign_lang` is a blocklist — whack-a-mole by design, and that's fine.**
  It's operator-owned, version-controlled config; iterate the regex as new
  foreign markers surface, exactly as with the amule anti-match regex.

## NOT yet validated against real hardware

Everything below passed unit/golden tests but has **not** been observed on the
live node:

- The live `/files` page rendering (new columns, `unidentified`, seasonal id).
- That the crawler, after redeploy, actually stops surfacing the Taiwanese /
  Spanish files as matched (the golden corpus proves the policy; the live crawl
  is the real confirmation).

## Next step (operator)

1. **Reset the local DBs**: wipe + rebuild `catalog.db` / `local.db`. The
   `target_id` format changed and the old data is noise — no migration (decided
   in the spec). This is a manual operator action, not in the code.
2. Redeploy with the new `matcher.yml`, let it crawl, and eyeball `/files`:
   foreign files should no longer be matched; identified rows should read
   `062A / S02E11A` + title; generic rows read `unidentified`.

## Deferred (small, non-blocking)

- Dashboard still shows the bare `062A` (spec Out-of-scope — give it the same
  `062A / S02E11A` + title treatment as `/files` later).
- `S9E999Z` unknown-target sentinels in 4 test files → normalize to `999Z`
  (cosmetic).
- Webui test suite's pre-existing `ResourceWarning: unclosed database`
  (GC-timing, predates this branch) → close connections in the fixtures.
