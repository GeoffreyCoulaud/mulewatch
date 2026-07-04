# Plan — Matcher precision + canonical target_id + files-page cleanup

Executes `docs/specs/2026-07-04-matcher-precision-and-files-ui.md`.

Three tasks, run sequentially (Task 1 is foundational; 2 and 3 both build on the
new id format but touch different packages).

## Global Constraints (bind every task)

- **100 % branch coverage per package** (`--cov-fail-under=100`, `branch=true`).
  Exercise *both* sides of every conditional. The gate is per package:
  `( cd packages/<pkg> && uv run pytest )`. A lone test needs `--no-cov`.
- **Strict TDD**: write the failing test first, run it, watch it fail, then the
  minimal implementation. Tests are the spec.
- **`mypy --strict`** over `src` AND `tests`. Every test function is
  `-> None` with typed params.
- **`ruff`** (`E,F,I,UP,B,SIM`), line length 100. Run `uv run poe fix` before
  hand-fixing lint/format/SQL; review its diff.
- **All code English** — identifiers and prose (comments, docstrings, messages).
  Genuine domain data (real VF titles, eMule filenames) stays as-is.
- **Clean/Hexagonal**: `domain/` is pure (no I/O). `webui/domain/format.py` is
  pure formatting.
- **Canonical id format (exact)**: `f"{absolute_number:03d}{segment.upper()}"`
  → `001A`, `062A`, `103A`.
- **Seasonal display format (exact)**:
  `f"S{season:02d}E{seasonal_number:02d}{letter.upper()}"` → `S02E11A`.
- **Conventional commits**; commit per task.
- Do **not** edit dated historical docs (`docs/specs/`, `docs/plans/`,
  `docs/handoffs/` other than this pair) — they record what was true then.

---

## Task 1 — Canonicalize `target_id` to the absolute form

**Package(s):** all four (source change in `matching`, test-data sweep
everywhere). Only ONE `src` file changes; everything else is test data.

**Source change** — `packages/matching/src/catalog_matching/models.py`, the
`target_id` property (currently line ~40):

```python
@property
def target_id(self) -> str:
    """Stable segment identifier, e.g. ``062A`` (absolute number, zero-padded, + segment letter)."""
    return f"{self.absolute_number:03d}{self.segment.upper()}"
```

**TDD order:**
1. First flip the two assertions in
   `packages/matching/tests/test_models.py`:
   - line 36: `assert target.target_id == "S2E062A"` → `== "062A"`
   - line 48: `assert target.target_id == "S1E005B"` → `== "005B"`
   Run `( cd packages/matching && uv run pytest tests/test_models.py --no-cov )`,
   watch it fail against the old property.
2. Apply the property change, watch those pass.

**Mechanical sweep** — the transform is uniform: strip the `S<n>E` prefix,
keep the 3-digit absolute + letter. Regex: `\bS[12]E([0-9]{3})([A-Z])\b` →
`\1\2` (e.g. `S2E062A` → `062A`, `S1E005B` → `005B`). Apply to every test /
fixture literal. Confirmed hit set (from a repo-wide grep — verify none are
missed, and that no match is a false positive that is NOT a target_id):
- `packages/matching/tests/`: `test_engine.py`, `test_explain.py`,
  `test_validation.py`, `test_decision_record.py`, and
  `fixtures/golden_corpus.yaml` (14 `target_id:` fields),
  `fixtures/golden_targets.yaml` (the comment on line 2 only).
- `packages/crawler/tests/`: the ~19 files listed by
  `grep -rlE '\bS[12]E[0-9]{3}[A-Z]\b' packages/crawler/tests`.
- `packages/verifier/tests/test_app.py`.
- `packages/webui/tests/`: `test_webui_app.py`, `test_webui_catalog_read.py`,
  `test_webui_coverage.py`, `test_webui_local_read.py`,
  `test_webui_matching_read.py`, `test_webui_targets_read.py`.

**Docs (living, not dated):** update the two lines in the repo-root
`CLAUDE.md` that give the id example (`.target_id` = `S2E062A` in the
Architecture matching-engine section, and the `models.py` row in the Gotchas /
module-roles description) → `062A`. Do NOT touch anything under
`docs/specs|plans|handoffs`.

**Done when:** every package gate is green
(`uv run poe check`, or per package `( cd packages/<pkg> && uv run pytest )`),
`mypy --strict` and `ruff` clean, and a repo-wide
`grep -rnE '\bS[12]E[0-9]{3}[A-Z]\b' packages` returns nothing (docs excluded).

---

## Task 2 — Harden the matcher

**Package:** `matching` (config lives in `deploy/`, tests in
`packages/matching/tests/`). Depends on Task 1 (new id format in fixtures).

**Config change** — `deploy/config/crawler/matcher.yml`:

1. `foreign_lang`: extend the regex to also exclude the observed foreign
   markers `BIG5`, `R3_DVDRIP`, `XeTe`. The `RegexMatcher` folds the filename
   and prefixes `(?i)`, so casing is irrelevant; add them as plain alternation
   branches (no `\b` needed — they never occur inside French words). Prove the
   exact string with the red fixtures below.
2. `segment_id_loose`: change BOTH number boundaries from `[^0-9]` to
   `[^0-9A-Za-z]` so a bare number inside a hex CRC tag stops matching:
   ```
   {mono_gate}(?:^|[^0-9A-Za-z])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9A-Za-z]|$)
   ```

**Test scaffolding** — `packages/matching/tests/fixtures/golden_targets.yaml`:
the existing subset (absolute 62, segments A+B) is multi-segment, so
`mono_gate` already neutralizes `segment_id_loose` there. To exercise the 1b
boundary fix, ADD one real **mono-segment** target — absolute 94
(= the observed false positive):

```yaml
  - season: 2
    seasonal_number: 43
    absolute_number: 94
    segments:
      - { letter: A, title: "La Terre est à nous !" }
```

Its `target_id` is `094A`; it is > `062A`, so the catch-all tiebreak is
unchanged (still `062A`). Verify that adding it does not alter any existing
golden-corpus case's expected outcome.

**Golden corpus fixtures (red first)** —
`packages/matching/tests/fixtures/golden_corpus.yaml`, add:

```yaml
  - id: foreign_big5_r3_taiwanese_discarded
    # Taiwanese R3 DVD rip (BIG5): foreign_lang veto -> french_safe false -> discarded.
    filename: "[KERORO][R3_DVDRIP][09][640x480.x264.AAC.2AUDIO.BIG5][7C094A47].mkv"
    discarded: true

  - id: foreign_spanish_xete_discarded
    # Spanish dub (XeTe group): foreign_lang veto -> discarded.
    filename: "Keroro 35 - El gran plan del cumple de Natsumi (DVD TV dual by XeTe).avi"
    discarded: true

  - id: hash_digits_not_matched_as_episode_number
    # French-safe, no real episode number: "094" appears ONLY inside the CRC tag.
    # Before 1b: segment_id_loose matched 094 -> numero_nu (notify) on mono 094A.
    # After 1b: the hex-bordered number no longer matches -> keroro_large catch-all.
    filename: "Keroro rediffusion [7C094A47].avi"
    tier: catalog
    target_id: 062A
    rule_name: keroro_large
```

**TDD order:** add each fixture and run the golden-corpus test to confirm it is
RED against the current policy (the two `discarded` cases currently match /
catalog; the hash case currently yields `notify` / `094A` / `numero_nu`), then
apply the `matcher.yml` changes and confirm GREEN. Keep every pre-existing case
green.

**Done when:** `( cd packages/matching && uv run pytest )` green at 100 %
branch, `mypy`/`ruff`/`sqlfluff` clean.

---

## Task 3 — Clean up the `/files` page

**Package:** `webui`. Depends on Task 1 (id format). Independent of Task 2.

**Pure formatting** — `packages/webui/src/catalog_webui/domain/format.py`
(pure, no I/O), add and unit-test:
- `human_size(size_bytes: int) -> str` — human-readable, e.g. `349 MB`
  (choose binary or decimal; document the choice; test the boundaries incl. 0
  and a multi-GB value).
- `short_timestamp(iso: str) -> str` — trim an ISO-8601 UTC string to
  `YYYY-MM-DD HH:MM` + `Z`, e.g. `2026-07-03T23:45:24.104990+00:00` →
  `2026-07-03 23:45Z`. Handle a value with no microseconds too.
- `seasonal_id(*, season: int, seasonal_number: int, letter: str) -> str` —
  `f"S{season:02d}E{seasonal_number:02d}{letter.upper()}"` → `S02E11A`.

**View-model** — `packages/webui/src/catalog_webui/domain/views.py`,
`FileRowDisplay`: add precomputed fields
- `target_display: str` — `"{canonical} / {seasonal}"`, or `"unidentified"`
  when the row's tier is `catalog`, or `"—"` when there is no decision.
- `title_display: str` — the episode title, or `"—"`.
- `size_display: str` — from `human_size`.
- `last_seen_display: str` — from `short_timestamp`.
- `verdict_display`: keep, but the fallback becomes `"pending"` (not `"—"`)
  when there is a decision but no verdict yet. (No decision → `"—"`.)

Keep the raw fields the template still needs (`ed2k_hash`, `short_hash`,
`filename`, `ed2k_link`, `tier_display`).

**Wiring** — `packages/webui/src/catalog_webui/composition/app.py`:
- Build `_segment_by_id = {seg.target_id: seg for seg in target_segments}`
  next to the existing `_title_by_id`.
- `_to_display_rows` takes the `segment_by_id` mapping and computes the new
  fields. Resolution rule per row:
  - `row.target_id is None` → `target_display="—"`, `title_display="—"`.
  - `row.tier == "catalog"` → `target_display="unidentified"`,
    `title_display="—"` (the `keroro_large` catch-all; the only catalog rule).
  - else `seg = segment_by_id.get(row.target_id)`:
    - found → `target_display=f"{row.target_id} / {seasonal_id(...)}"`,
      `title_display=seg.title`.
    - not found (id unknown to the current catalog) → `target_display=row.target_id`,
      `title_display="—"`.
- Both `handle_files` and `handle_target` call `_to_display_rows`; pass the
  mapping in both.

**Template** — `packages/webui/src/catalog_webui/adapters/templates/files.html`:
- Add a **Title** column (after Target).
- Render `target_display`, `title_display`, `size_display`, `last_seen_display`,
  `verdict_display` instead of the raw fields.
- Add a short **tier legend** (a caption or a small block) explaining
  `download` / `notify` / `catalog`. Keep templates logic-free (spec W-D8:
  precompute view-side; no `{% if %}` beyond the existing iteration guards).
  If the template check (`uv run poe template-check`) forbids new constructs,
  precompute in the view-model.

**Tests** — `packages/webui/tests/`: extend for the new fields and add cases:
- `tier == "catalog"` → `target_display == "unidentified"`, title `—`.
- a resolvable id → `"062A / S02E11A"` + real title.
- an unknown id → raw id + `—`.
- no decision → `—` / `—`, verdict `—`; decision but no verdict → `pending`.
- `human_size` / `short_timestamp` boundaries in the `format` tests.

**Done when:** `( cd packages/webui && uv run pytest )` green at 100 % branch,
`template-check` / `mypy` / `ruff` clean. Note the display uses the seasonal
form so the raw `062A` id is joined with `S02E11A` for the operator.
