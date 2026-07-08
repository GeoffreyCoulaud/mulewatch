# Bare-number precision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the bare-number rules from matching incidental numbers (broadcast-date day-of-month) and from colliding across the absolute/seasonal double numbering, so a well-named Teletoon rip resolves only its real segment(s).

**Architecture:** Config + tests only, no engine/production code. One token (`segment_id_loose`) in the operator-owned `matcher.yml` gains a local date veto and loses its `{seasonal_number}` alternative. A prerequisite test-fixture reorganization decouples `catalog`-tier cases from the arbitrary catch-all `target_id` so the target fixture can be enriched.

**Tech Stack:** Python 3.14, pytest, PyYAML, stdlib `re` (`re.ASCII`), uv, poethepoet.

## Global Constraints

- 100% branch coverage per package, gated (`--cov-fail-under=100`, `branch=true`). Add tests for both sides of every conditional.
- Strict TDD: failing test first, watch it fail, then the minimal change.
- `mypy --strict` over `src` and `tests`; `ruff` selects `E,F,I,UP,B,SIM`, line length 100. Every test function annotated `-> None`, typed params.
- All code and prose in English. Conventional commits (`fix:`, `refactor:`, `test:`, ...).
- No em-dashes or en-dashes anywhere (use `:`, `(...)`, `.`, or a short `-`).
- The gate is PER PACKAGE: run from `packages/matching`. A single test file needs `--no-cov` (the package-wide 100% gate makes a lone file "fail" otherwise).
- `deploy/config/crawler/matcher.yml` is the operator-owned single source of truth: edit it in place. In a YAML double-quoted string, regex backslashes are doubled (`\\s`, `\\b`, `\\d`, `\\-`).

---

### Task 1: Reorganize the corpus harness (add `unidentified` case type)

Refactor with no behaviour change. Today every `catalog`-tier case asserts `target_id: 062A`, a pure artefact of the catch-all min-key over the present targets. Asserting it couples the case to the target set (adding target 021 would flip all six to `021A`). Add a target-agnostic `unidentified` case type and migrate the six cases, so Task 2 can enrich the targets safely.

**Files:**
- Modify: `packages/matching/tests/test_golden_corpus.py` (harness `test_golden_corpus`, guard `test_corpus_covers_every_tier_and_a_discard`)
- Modify: `packages/matching/tests/fixtures/golden_corpus.yaml` (six catalog cases)

**Interfaces:**
- Produces: the corpus case schema gains `{ id, filename, unidentified: true }`, asserting exactly one decision with `tier == "catalog"` and `rule_name == "keroro_large"`, target_id not asserted. Task 2 relies on this for `bare_date_only_is_unidentified`.

- [ ] **Step 1: Add the `unidentified` branch to the harness**

In `test_golden_corpus.py`, insert this branch in `test_golden_corpus` right after the `if "decisions" in case:` block (after its `return`, before `assert len(decisions) == 1`):

```python
    if case.get("unidentified", False):
        assert len(decisions) == 1, f"{case['id']}: expected one decision, got {decisions}"
        decision = decisions[0]
        assert decision.tier == "catalog", f"{case['id']}: expected catalog tier, got {decision.tier}"
        assert decision.rule_name == "keroro_large", (
            f"{case['id']}: expected keroro_large, got {decision.rule_name}"
        )
        return
```

Replace `test_corpus_covers_every_tier_and_a_discard` with:

```python
def test_corpus_covers_every_tier_and_a_discard() -> None:
    # Completeness guard: the corpus exercises the 3 tiers + at least one discard.
    # An ``unidentified`` case IS the catalog tier (it just does not pin the arbitrary target_id).
    tiers = {c["tier"] for c in _CASES if "tier" in c}
    if any(c.get("unidentified", False) for c in _CASES):
        tiers.add("catalog")
    assert {"download", "notify", "catalog"} <= tiers
    assert any(c.get("discarded", False) for c in _CASES)
```

- [ ] **Step 2: Prove the new guard fails on a non-unidentified file**

Temporarily append this bogus case to the END of `golden_corpus.yaml`'s `cases:` (a download file wrongly flagged `unidentified`):

```yaml
  - id: TEMP_bogus_unidentified
    filename: "Keroro VF Les demoiselles cambrioleuses.avi"
    unidentified: true
```

Run: `( cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q )`
Expected: FAIL on `TEMP_bogus_unidentified` ("expected catalog tier, got download"). This proves the guard rejects a wrong file. Then delete the `TEMP_bogus_unidentified` case.

- [ ] **Step 3: Migrate the six catalog cases to `unidentified`**

In `golden_corpus.yaml`, for each of the six cases below, replace the three lines
`tier: catalog` / `target_id: 062A` / `rule_name: keroro_large` with a single `unidentified: true`, keeping each case's `id`, `filename`, and comment. Rename the first case's `id` (its old name referenced the tie-break that is no longer tested).

The six: `keroro_only_catalog_tiebreak_target_id` (rename to `keroro_only_is_unidentified`), `seasonal_episode_no_segment_falls_back_to_catalog`, `decoy_resolution_not_a_segment`, `not_episode_opening_demotes_title_to_catalog`, `bare_archive_zip_stays_catalog`, `hash_digits_not_matched_as_episode_number`.

Example (first case), from:

```yaml
  - id: keroro_only_catalog_tiebreak_target_id
    # "Keroro" seul -> keroro_large (catalog) sur 62A ET 62B ; départage target_id -> 62A.
    # NB : filler neutre (pas "Gunso", désormais veto-é comme titre japonais).
    filename: "Keroro rediffusion.mkv"
    tier: catalog
    target_id: 062A
    rule_name: keroro_large
```

to:

```yaml
  - id: keroro_only_is_unidentified
    # "Keroro" alone -> keroro_large (catalog) over every target -> unidentified.
    # The winning target_id is an arbitrary min-key artefact and is deliberately not asserted.
    filename: "Keroro rediffusion.mkv"
    unidentified: true
```

- [ ] **Step 4: Run the corpus to verify it stays green**

Run: `( cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q )`
Expected: PASS (refactor, no behaviour change: the six files are still unidentified).

- [ ] **Step 5: Commit**

```bash
git add packages/matching/tests/test_golden_corpus.py packages/matching/tests/fixtures/golden_corpus.yaml
git commit -m "refactor(matching-tests): add target-agnostic unidentified corpus case type"
```

---

### Task 2: Tune `segment_id_loose` (date veto + drop seasonal), guarded red->green

The fix itself. Enrich the target fixture with the collision pair, add the guard cases (which then fail on the current policy), then apply the one-token policy edit to make them pass.

**Files:**
- Modify: `packages/matching/tests/fixtures/golden_targets.yaml` (add 021 + 072)
- Modify: `packages/matching/tests/fixtures/golden_corpus.yaml` (add guard cases)
- Modify: `deploy/config/crawler/matcher.yml:18` (token `segment_id_loose`)

**Interfaces:**
- Consumes: the `unidentified` case type from Task 1.
- Produces: the shipped policy where a bare number matches only its `absolute_number` and never inside a date.

- [ ] **Step 1: Enrich the target fixture**

Append to `packages/matching/tests/fixtures/golden_targets.yaml` under `episodes:` (titles verbatim from `deploy/config/crawler/targets.yml`):

```yaml
  # Collision pair for the bare-number precision guards: absolute 21 (S1E21) and absolute 72
  # (S2E21) share seasonal_number 21. Both two-segment.
  - season: 1
    seasonal_number: 21
    absolute_number: 21
    segments:
      - { letter: A, title: "Economie d'énergie" }
      - { letter: B, title: "Keroro part à la campagne" }
  - season: 2
    seasonal_number: 21
    absolute_number: 72
    segments:
      - { letter: A, title: "Le défi des chefs cuisiniers" }
      - { letter: B, title: "Duel contre le plus puissant des combattants" }
```

- [ ] **Step 2: Add the guard cases**

Append to `packages/matching/tests/fixtures/golden_corpus.yaml` under `cases:`:

```yaml
  # --- Bare-number precision (spec 2026-07-08): date veto + dropped seasonal collision ---
  - id: bare_21_no_date_pins_absolute_only
    # Dropped {seasonal_number}: bare "21" is only episode 21, not also 72 (S2E21).
    filename: "Keroro 21.avi"
    decisions:
      - { target_id: 021A, tier: notify, rule_name: numero_nu }
      - { target_id: 021B, tier: notify, rule_name: numero_nu }

  - id: numeric_date_veto_pins_exact_only
    # "21/09/2008" is a broadcast date, not episode numbers: only the exact N°062A stands.
    filename: "[TV] KERORO MISSION TITAR N°062A x 21/09/2008 TELETOON.avi"
    tier: download
    target_id: 062A
    rule_name: id_segment_exact

  - id: abbrev_month_date_veto_pins_exact_only
    # The abbreviated month "sept" still vetoes the day-of-month "21".
    filename: "KERORO N°062A x 21 sept 2008 TELETOON.avi"
    tier: download
    target_id: 062A
    rule_name: id_segment_exact

  - id: bare_date_only_is_unidentified
    # A broadcast date with no episode id no longer fabricates episodes 21/72.
    filename: "KERORO MISSION TITAR 21 septembre 2008 TELETOON.avi"
    unidentified: true

  - id: month_like_word_not_date_vetoed
    # The \b anchor: "marseille" starts like "mars" but is not a month, so 62 still matches.
    filename: "Keroro 62 marseille.avi"
    decisions:
      - { target_id: 062A, tier: notify, rule_name: numero_nu }
      - { target_id: 062B, tier: notify, rule_name: numero_nu }
```

- [ ] **Step 3: Run the corpus to verify the guards FAIL on the current policy**

Run: `( cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q )`
Expected: FAIL. On the unchanged policy, the enriched targets make the date/collision cases wrong:
`real_62A_full_release` and `ascii_no_accents_62A` now return 5 decisions (expected 1);
`bare_21_no_date_pins_absolute_only` also emits 072A/072B; `numeric_date_veto_...`,
`abbrev_month_date_veto_...` return 5; `bare_date_only_is_unidentified` returns 4. This is the red baseline. (`month_like_word_not_date_vetoed` already passes: it has no collision and no date.)

- [ ] **Step 4: Apply the policy edit**

In `deploy/config/crawler/matcher.yml`, replace line 18 (`segment_id_loose`) with:

```yaml
  segment_id_loose: { regex: "(?:^|[^0-9A-Za-z])0*{absolute_number}(?!\\s*(?:janv?(?:ier)?|fevr?(?:ier)?|mars|avr(?:il)?|mai|juin|juil(?:let)?|aout|sep(?:t(?:embre)?)?|oct(?:obre)?|nov(?:embre)?|dec(?:embre)?)\\b)(?!\\s*[/.\\-]\\s*\\d)(?:[^0-9A-Za-z]|$)" }
```

- [ ] **Step 5: Run the corpus to verify GREEN**

Run: `( cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q )`
Expected: PASS (all guards green; the pre-existing cases unaffected).

- [ ] **Step 6: Commit**

```bash
git add packages/matching/tests/fixtures/golden_targets.yaml packages/matching/tests/fixtures/golden_corpus.yaml deploy/config/crawler/matcher.yml
git commit -m "fix(matcher): veto dates and drop the seasonal alternative in the bare-number token"
```

---

### Task 3: Full gate and holistic verification

**Files:** none (verification only).

- [ ] **Step 1: Full matching gate (100% branch coverage)**

Run: `( cd packages/matching && uv run pytest )`
Expected: PASS, coverage 100% (234 + new tests). If coverage drops, a new branch is unexercised: add the missing case.

- [ ] **Step 2: Confirm the shipped-policy consumers still pass**

Run: `( cd packages/crawler && uv run pytest tests/composition/test_prod_targets.py tests/composition/test_app.py tests/composition/test_main.py tests/domain/test_policy_fingerprint.py --no-cov -q )`
Expected: PASS (the policy fingerprint changes, which is intended; no test pins its value).

- [ ] **Step 3: Full per-package gate**

Run: `uv run poe check`
Expected: PASS (lint-all + per-package tests: ruff, mypy --strict, sqlfluff, template-check, pytest).

- [ ] **Step 4: Holistic review**

Re-read the diff as a whole: the policy line reads correctly, the guard cases assert the spec's §4 behaviour, no `catalog` case pins a target_id anymore, no em-dashes in the added prose. Confirm `test_resolver` / `test_matchers` / `test_interpolation` are green (they are part of Step 1).

---

## Self-Review

- **Spec coverage:** §4/§5.1 -> Task 1 (`unidentified` type + guard). §5.2 -> Task 2 Step 1. §5.3 -> Task 2 Steps 2/3/5. §3 policy edit -> Task 2 Step 4. §7 backfill is runtime (no task; fires on deploy). §6 non-goals: nothing to do. §8 files: all covered.
- **Placeholder scan:** none (the only `TEMP_` case is explicitly added then deleted in Task 1 Step 2).
- **Type consistency:** the `unidentified` key name is identical in the harness (Task 1) and the fixtures (Tasks 1/2). Rule names (`numero_nu`, `id_segment_exact`, `keroro_large`) and tiers match the shipped policy verbatim.
