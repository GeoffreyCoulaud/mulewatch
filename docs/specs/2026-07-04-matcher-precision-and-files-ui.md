# Spec — Matcher precision + canonical target_id + files-page cleanup

- Date: 2026-07-04
- Status: approved
- Branch: `feat/matcher-precision-and-files-ui`

## Context

First live run (gluetun + High-ID + download, personal machine). The webui
`/files` page surfaced 29 matched files out of 726 catalogued. Reading them
against the mission (French dub — *VF* — of *Keroro mission Titar*) revealed:

- **No French file at all.** The matched set is the *international* Keroro
  corpus: Taiwanese R3 DVD rips (`BIG5`), a Spanish dub (`XeTe`), Japanese
  manga/doujinshi ZIPs, an opening theme. This is itself a valid mission
  finding: the VF does not trivially surface on eD2k/Kad (it is rare — that is
  the lost-media premise). The operator confirmed the keywords are fine; no
  concern about the absence of true positives.
- **The `notify` tier is polluted** by three false-positive mechanisms
  (analysis grounded in `deploy/config/crawler/matcher.yml`):
  1. `foreign_lang` blocklist gaps — `BIG5`, `R3_DVDRIP`, `XeTe` are not
     excluded, so the Taiwanese and Spanish releases pass `french_safe`.
  2. `segment_id_loose` matches digits *inside the CRC32 release tag*
     (`[7C094A47]` → "094" matched absolute 94 → `S2E094A`; `[50CCEC96]` →
     "50" matched seasonal 50 → `S2E101A`).
  3. `title_hit` coverage (rapidfuzz ≥ 0.6) fires across languages (a Spanish
     title fuzzy-matched a French one).
- **The `download` tier held**: no foreign file reached it (it requires a
  positive `source_marker` — `teletoon|idf1|vf`). The safety valve works.

Two display warts compounded the reading:

- **`target_id` is a chimera**: `S2E094A` mixes the season (`S2`) with the
  *absolute* number (`094`). Season 2 has only 52 episodes, so it reads as a
  contradiction. The id is unique (absolute is globally unique) but mislabeled.
- **`S1E001A` / `catalog` is a catch-all, not "episode 1"**: `keroro_large` is
  the only `catalog`-tier rule and has no per-target criterion, so it matches
  every target and the deterministic tiebreak always picks the smallest
  `target_id`. Every `catalog` row is really "generic Keroro, unidentified".

## Decisions

### 1. Harden the matcher (operator-owned `matcher.yml`)

Iterative precision, not a redesign. The operator explicitly prefers hardening
the matcher over surfacing language in the UI.

1a. **`foreign_lang` (primary fix).** Add the observed foreign markers:
`BIG5` (Chinese encoding), `R3_DVDRIP` (region-3 Asian DVD), `XeTe` (Spanish
group). Because `is_keroro = french_safe ∧ keroro_titar` and every rule
depends on `is_keroro` (including `keroro_large`), a foreign match now yields
**no decision at all** — foreign Keroro files drop out of the matched view
entirely (they remain raw-catalogued as observations). This single change
removes *all* observed false positives, including the hash-digit ones (those
files are `BIG5`, gated out before `segment_id_loose` is even reached), and the
Spanish `title_hit` (gated out before coverage runs). **Approved consequence:**
foreign Keroro episodes are no longer catalogued at any tier — precision over
completeness for non-French material.

1b. **`segment_id_loose` boundary (structural, secondary).** Independent of
1a, tighten the number boundaries from `[^0-9]` to `[^0-9A-Za-z]` (require a
real delimiter). This kills the hash-digit-matching *class* for any file,
including a future genuine French file whose CRC tag happens to contain episode
digits. Pure YAML edit. Minimal recall cost (glued forms like `keroro23A`,
rare and recovered by title/source_marker elsewhere).

1c. **`coverage` unchanged.** Keep `title_hit: min 0.6`. The only observed
cross-language case is already killed by 1a, and `notify` is a review bucket
where some noise is acceptable.

### 2. Canonicalize `target_id` to the absolute form

The canonical id becomes **absolute-only**: `f"{absolute_number:03d}{letter}"`
→ `001A`, `062A`, `103A`. Rationale: the absolute number is globally unique
(1–103) so it is self-sufficient; the seasonal form repeats across seasons
(S1E01 and S2E01 both have `seasonal_number = 1`) and therefore *needs* the
`S..E..` prefix — it is the heavier, display-only form. The current
`S<season>E<absolute>` carries the prefix cost without its benefit.

The id is constructed in exactly one place (`models.py`, the `target_id`
property) and is **never re-parsed** anywhere (verified: no split/regex/slice
on `target_id`). It is an opaque key. The change is therefore a one-line source
edit plus a uniform mechanical sweep of test-data literals: strip the `S<n>E`
prefix, keeping the 3-digit absolute + letter (`S2E062A` → `062A`).

The seasonal form `S<season:02d>E<seasonal_number:02d><letter>` (`S02E11A`)
becomes a *derived display* computed by the webui from the targets catalog.

Determinism is preserved: zero-padded absolute ids sort numerically; the
catch-all tiebreak still lands on `001A` (the ex-`S1E001A`).

### 3. Clean up the `/files` page (webui, display only)

- **Target column**: `062A / S02E11A` (canonical absolute / seasonal locator),
  resolved from the already-loaded targets catalog. When `tier == "catalog"`
  (⟺ the `keroro_large` catch-all — the only catalog rule), display
  `unidentified` instead of a target.
- **Title column** (new): the episode title; `—` for the catch-all.
- **Tier**: a legend explaining `catalog` / `notify` / `download`.
- **Verdict**: `—` → `pending` ("not yet verified").
- **Size**: human-readable (`349 MB`).
- **Last seen**: trimmed (`2026-07-03 23:45Z`), no microseconds.

## Out of scope

- Language/VF flag, `rule_name` column, eD2k link in the list — all already in
  `files/<id>`.
- The **dashboard** catch-all (every `catalog` file attributed to `001A` in
  coverage counts) — a known corollary, treated separately.
- Filter *controls* in the template (the backend already supports the query
  params).

## Data

Local `catalog.db` / `local.db` are reset (wipe + rebuild). Current data is
essentially noise (foreign corpus, false positives), so no id migration —
confirmed by the operator.

## Verification

Full gate per package (100 % branch coverage, ruff, mypy `--strict`, sqlfluff,
template check) + holistic review. Each matcher change is proven red→green with
a regression fixture in the golden corpus.
