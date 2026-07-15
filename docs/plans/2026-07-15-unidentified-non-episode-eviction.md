# Unidentified non-episode eviction: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the `keroro_large` catch-all from labelling obvious non-episodes (comics, the movie, openings, out-of-range bare numbers, a Catalan dub) as "unidentified", and stop `catalog`-tier decisions from leaking under episode `001A` in the webui.

**Architecture:** Four independent TDD tasks. Tasks 1-3 change only the operator-owned policy `deploy/config/crawler/matcher.yml`, validated by the `packages/matching` golden corpus (which reads that exact file). Task 4 is a one-line SQL change in the webui read layer. No engine change, no schema change; the live catalogue is re-labelled automatically by the existing re-evaluation backfill on the next crawler restart (see the spec, section 5).

**Tech Stack:** Python ≥3.14, PyYAML policy, stdlib `re` (`re.ASCII`) matchers, SQLite, pytest.

**Spec:** `docs/specs/2026-07-15-unidentified-non-episode-eviction.md` (Approach 1: `episode_number` is bare-number only).

## Global Constraints

- **TDD strict**: write the failing test first, run it, watch it fail, then the minimal change. Tests are the spec.
- **100% branch coverage per package**, gated. A lone test needs `--no-cov` (the package-wide `--cov-fail-under=100` fails a single test otherwise).
- **The gate is per package**: `( cd packages/<pkg> && uv run pytest )`. Never run bare `pytest` from the repo root.
- `mypy --strict` over `src` AND `tests`; every test function annotated `-> None` with typed params. `ruff` selects `E,F,I,UP,B,SIM`, line length 100. Run `uv run poe fix` before hand-fixing lint/format.
- **English everywhere** (identifiers + prose + commit messages). Conventional commits (`fix(matching):`, `test:`, ...). No em/en-dashes in prose or comments (project rule since 2026-07-07): use colons, parentheses, or hyphens.
- **`deploy/config/crawler/matcher.yml` is the single source of truth** for the policy: edit it in place, never add a duplicate copy or an inline policy dict.
- **Regex tokens compile under stdlib `re` with `re.ASCII`.** An invalid pattern raises `re.error`, caught in `validation.py` as a `ConfigError`.
- The golden corpus engine runs against the **reduced** fixture `packages/matching/tests/fixtures/golden_targets.yaml` (targets: absolute **21, 62, 72, 94**). "Out of range for the test" therefore means any other number, e.g. 155. This matches production, where the last target is 103.

---

### Task 1: Content eviction (comics, the movie, openings)

Widen `not_episode` (comics + movie markers) and re-base `keroro_large` on `is_episode`, so any file carrying a non-episode marker drops out of the catch-all.

**Files:**
- Modify: `deploy/config/crawler/matcher.yml` (token `not_episode`, rule `keroro_large`)
- Test: `packages/matching/tests/fixtures/golden_corpus.yaml` (2 new cases + 1 flipped)

**Interfaces:**
- Produces: `not_episode` now also matches `comic` / `doujinshi` / `同人誌` / `movie`; `keroro_large` = `all: [is_episode, {any:[is_video,is_archive]}]`. Task 2 will further add `{ not: episode_number }` to `keroro_large`.

- [ ] **Step 1: Add the failing corpus cases.** In `golden_corpus.yaml`, add:

```yaml
  - id: japanese_comic_zip_is_discarded
    # [comic] + 軍曹 is a manga, not a broadcast episode. The CJK title escapes foreign_lang
    # and .zip is a generic archive, so before this change it was catalogued as unidentified.
    # not_episode(comic) now makes is_episode false -> discarded.
    filename: "[comic][ケロロ軍曹][KERORO][吉崎観音].vol.09.zip"
    discarded: true

  - id: english_movie_marker_is_discarded
    # The theatrical film is not a TV episode. not_episode(movie) -> discarded. (The real
    # [T-N]Keroro_Gunsou_Movie file is already vetoed by foreign_lang "Gunsou"; this synthetic
    # case guards the new 'movie' marker on its own.)
    filename: "Keroro Movie special edition.avi"
    discarded: true
```

  And flip the existing `not_episode_opening_demotes_title_to_catalog` case from
  `unidentified: true` to `discarded: true`, updating its comment to: `# "opening" now
  excludes it from is_episode, so keroro_large no longer catalogs it -> discarded.`

- [ ] **Step 2: Run the corpus, verify the 3 cases fail.**

Run: `( cd packages/matching && uv run pytest tests/test_golden_corpus.py -k "comic_zip or movie_marker or opening_demotes" --no-cov -q )`
Expected: FAIL (comic zip + movie currently `unidentified`; opening currently `unidentified`, case now expects `discarded`).

- [ ] **Step 3: Apply the policy change.** In `deploy/config/crawler/matcher.yml`:

  Replace the `not_episode` token line with:

```yaml
  not_episode:   { regex: "opening|ending|g[eé]n[eé]rique|\\bsample\\b|preview|trailer|bande.?annonce|\\bcomic\\b|\\bdoujin(?:shi)?\\b|同人誌|\\bmovie\\b" }
```

  Replace the `keroro_large` rule line with (keep its guard comment above it intact):

```yaml
  - { name: keroro_large,        tier: catalog,  all: [is_episode, { any: [is_video, is_archive] }] }
```

- [ ] **Step 4: Run the full matching suite, verify green.**

Run: `( cd packages/matching && uv run pytest -q )`
Expected: PASS (the 3 cases now match; every other corpus case, including the single-catalog-rule guard, stays green; 100% coverage holds, no Python code changed).

- [ ] **Step 5: Commit.**

```bash
git add deploy/config/crawler/matcher.yml packages/matching/tests/fixtures/golden_corpus.yaml
git commit -m "fix(matching): evict comics, the movie and openings from the catch-all"
```

---

### Task 2: Number eviction (out-of-range bare numbers)

Add the target-agnostic `episode_number` token (bare-number arm only, Approach 1) and require `{ not: episode_number }` on `keroro_large`, so a bare number that reaches the catch-all (hence out of range) drops out. Seasonal `SxE` / `NNxNN` forms without a segment letter are deliberately NOT evicted.

**Files:**
- Modify: `deploy/config/crawler/matcher.yml` (new `episode_number` token, `keroro_large` gains a guard)
- Test: `packages/matching/tests/fixtures/golden_corpus.yaml` (1 discard + 1 Approach-1 guard case), `packages/matching/tests/test_golden_corpus.py` (token unit tests)

**Interfaces:**
- Consumes: `keroro_large` from Task 1 (`all: [is_episode, {any:[is_video,is_archive]}]`).
- Produces: token `episode_number` (a `RegexDef`); `keroro_large` = `all: [is_episode, {any:[is_video,is_archive]}, {not: episode_number}]`.

- [ ] **Step 1: Add the failing corpus cases.** In `golden_corpus.yaml`:

```yaml
  - id: out_of_range_bare_number_is_discarded
    # 155 is beyond the last target (103 in prod; the reduced fixture tops out well below).
    # episode_number (bare) fires, no numbered rule claims it -> keroro_large vetoed -> discarded.
    filename: "[Keroro].155.[Xvid.mp3].[9AD4F87C].mkv"
    discarded: true

  - id: seasonal_x_form_no_letter_stays_unidentified
    # Approach 1 guard: "01x37" (whole episode, no A/B letter) is NOT a bare number, so
    # episode_number does not fire and keroro_large still catalogs it as unidentified.
    filename: "Keroro 01x37 rediffusion.mkv"
    unidentified: true
```

- [ ] **Step 2: Add the failing token unit tests.** In `test_golden_corpus.py`, append (mirroring `_is_archive_matcher`):

```python
def _episode_number_matcher() -> RegexMatcher:
    config = parse_matcher_config(yaml.safe_load(_MATCHER.read_text(encoding="utf-8")))
    token = config.tokens["episode_number"]
    assert isinstance(token, RegexDef)
    return RegexMatcher(token.pattern, token.flags)


@pytest.mark.parametrize("filename", ["[Keroro].104.avi", "Keroro 155 rediffusion.mkv"])
def test_episode_number_matches_a_bare_number(filename: str) -> None:
    assert _episode_number_matcher().matches(FileCandidate(filename=filename)) is True


@pytest.mark.parametrize(
    "filename",
    [
        "Keroro 21 septembre 2008 TELETOON.avi",  # date veto (month name)
        "Keroro 2008 rediffusion.avi",  # 4-digit year, no 2-3 digit bounded run
        "Keroro 640x480 BDRip.mkv",  # resolution, 3 digits per side
        "Keroro rediffusion [D6A10367].avi",  # hex CRC, digits bordered by letters
        "Keroro s01e29.avi",  # seasonal form: bare-only does not read it (Approach 1)
        "Keroro 01x37.avi",  # seasonal x-form: bare-only does not read it (Approach 1)
    ],
)
def test_episode_number_ignores_non_episode_numbers(filename: str) -> None:
    assert _episode_number_matcher().matches(FileCandidate(filename=filename)) is False
```

- [ ] **Step 3: Run, verify failures.**

Run: `( cd packages/matching && uv run pytest tests/test_golden_corpus.py -k "out_of_range or seasonal_x_form or episode_number" --no-cov -q )`
Expected: FAIL (`episode_number` token does not exist yet -> `KeyError` in the matcher helpers; `[Keroro].155` currently catalogued as unidentified).

- [ ] **Step 4: Apply the policy change.** In `deploy/config/crawler/matcher.yml`, add the `episode_number` token next to `segment_id_loose`:

```yaml
  episode_number: { regex: "(?:^|[^0-9A-Za-z])\\d{2,3}(?!\\s*(?:janv?(?:ier)?|fevr?(?:ier)?|mars|avr(?:il)?|mai|juin|juil(?:let)?|aout|sep(?:t(?:embre)?)?|oct(?:obre)?|nov(?:embre)?|dec(?:embre)?)\\b)(?!\\s*[/.\\-]\\s*\\d)(?:[^0-9A-Za-z]|$)" }
```

  and replace `keroro_large` with:

```yaml
  - { name: keroro_large,        tier: catalog,  all: [is_episode, { any: [is_video, is_archive] }, { not: episode_number }] }
```

- [ ] **Step 5: Run the full matching suite, verify green.**

Run: `( cd packages/matching && uv run pytest -q )`
Expected: PASS (155 discarded; 01x37 and the existing `Keroro S2E11.mkv` / `1920x1080` cases stay `unidentified`; in-range bare-number fan-out cases stay `notify`; coverage 100%).

- [ ] **Step 6: Commit.**

```bash
git add deploy/config/crawler/matcher.yml packages/matching/tests/fixtures/golden_corpus.yaml packages/matching/tests/test_golden_corpus.py
git commit -m "fix(matching): evict out-of-range bare numbers from the catch-all"
```

---

### Task 3: Catalan foreign-language eviction

Extend `foreign_lang` with Catalan markers so a Catalan dub (which `episode_number` no longer catches, being bare-only) is vetoed on the language axis.

**Files:**
- Modify: `deploy/config/crawler/matcher.yml` (token `foreign_lang`)
- Test: `packages/matching/tests/fixtures/golden_corpus.yaml` (1 discard case)

**Interfaces:**
- Produces: `foreign_lang` also matches `estrenen` / `\bels\b`.

- [ ] **Step 1: Add the failing corpus case.** In `golden_corpus.yaml`:

```yaml
  - id: catalan_dub_is_discarded
    # Catalan dub (real catalogued name) slipped foreign_lang: the Catalan markers
    # 'estrenen' / 'els' now veto it (french_safe false) -> discarded, not unidentified.
    filename: "Srg Keroro s01e29 La Natsumi I La Fuyuki S'estrenen A Escena - En Keroro I Els Paparazzi.avi"
    discarded: true
```

- [ ] **Step 2: Run, verify it fails.**

Run: `( cd packages/matching && uv run pytest tests/test_golden_corpus.py -k "catalan_dub" --no-cov -q )`
Expected: FAIL (currently `unidentified`: no Catalan marker in `foreign_lang`).

- [ ] **Step 3: Apply the policy change.** In `deploy/config/crawler/matcher.yml`, append `|estrenen|\\bels\\b` inside the `foreign_lang` alternation (before the closing quote). The token becomes (unchanged prefix elided for clarity, add only the two markers at the end):

```yaml
  foreign_lang:  { regex: "...existing markers...|BIG5|R3_DVDRIP|XeTe|estrenen|\\bels\\b" }
```

- [ ] **Step 4: Run the full matching suite, verify green.**

Run: `( cd packages/matching && uv run pytest -q )`
Expected: PASS (the Catalan case discarded; no other case regresses, in particular no kept file contains `els`/`estrenen`).

- [ ] **Step 5: Commit.**

```bash
git add deploy/config/crawler/matcher.yml packages/matching/tests/fixtures/golden_corpus.yaml
git commit -m "fix(matching): veto the Catalan dub in foreign_lang"
```

---

### Task 4: Stop catalog-tier decisions leaking under target 001A (webui read)

`catalog`-tier decisions are pinned to `001A`; exclude them from the target-scoped read so `/targets/001A` and `?target=001A` no longer list the catch-all files.

**Files:**
- Modify: `packages/crawler/src/mulewatch/webui/adapters/catalog_read.py` (`_filter_clauses`, the `target` clause)
- Test: `packages/crawler/tests/webui/test_webui_catalog_read.py`

**Interfaces:**
- Consumes: existing `CatalogReader.list_files` / `count_files` and the `db` fixture + seed helpers in the test module.
- Produces: the target filter now reads `... AND fdt.tier != 'catalog'`.

- [ ] **Step 1: Write the failing test.** In `test_webui_catalog_read.py`, add a seed helper and two assertions (follow the existing `_seed` INSERT pattern; use the module's `db` fixture):

```python
def _seed_catalog_tier_on_001a(db: Path) -> None:
    """A keroro_large catch-all file: its only decision is tier 'catalog' pinned to 001A."""
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", ("b" * 32, 50))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("b" * 32, "keroro manga.zip", 50, 1, 0, "[]", "keroro",
             "2026-07-01T10:00:00.000000+00:00", "n1"),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("b" * 32, "001A", "keroro_large", "catalog",
             "2026-07-01T10:00:01.000000+00:00", "n1"),
        )
        conn.commit()


def test_target_scope_excludes_catalog_tier(db: Path) -> None:
    _seed_catalog_tier_on_001a(db)
    reader = CatalogReader(open_reader(db))
    rows = reader.list_files(
        target="001A", tier=None, verdict=None, query=None, page=1
    )
    assert rows == []
    matched, _total = reader.count_files(target="001A", tier=None, verdict=None, query=None)
    assert matched == 0


def test_target_scope_keeps_non_catalog_tier(db: Path) -> None:
    _seed(db)  # existing helper: a 'download' decision on 062A
    reader = CatalogReader(open_reader(db))
    rows = reader.list_files(
        target="062A", tier=None, verdict=None, query=None, page=1
    )
    assert len(rows) == 1
```

- [ ] **Step 2: Run, verify the first test fails.**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_catalog_read.py::test_target_scope_excludes_catalog_tier --no-cov -q )`
Expected: FAIL (`rows` currently contains the catalog-tier file; `matched == 1`).

- [ ] **Step 3: Apply the read fix.** In `catalog_read.py`, in `_filter_clauses`, change the `target` EXISTS clause to exclude catalog:

```python
    if target is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM latest_dec AS fdt"
            " WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.target_id = ? AND fdt.tier != 'catalog')"
        )
        params.append(target)
```

- [ ] **Step 4: Run both new tests, verify green.**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_catalog_read.py -k "target_scope" --no-cov -q )`
Expected: PASS.

- [ ] **Step 5: Run the crawler package gate, verify coverage holds.**

Run: `( cd packages/crawler && uv run pytest -q )`
Expected: PASS, 100% branch coverage (both sides of the new clause exercised: excluded catalog, kept non-catalog).

- [ ] **Step 6: Commit.**

```bash
git add packages/crawler/src/mulewatch/webui/adapters/catalog_read.py packages/crawler/tests/webui/test_webui_catalog_read.py
git commit -m "fix(webui): exclude catalog-tier decisions from the target-scoped read"
```

---

## Final verification (after all tasks)

- [ ] Run the full gate: `uv run poe check`. Expected: green (lint-all + per-package tests, 100% branch).
- [ ] Sanity-read the four commits; confirm `matcher.yml` has exactly one `tier: catalog` rule (`keroro_large`).
- [ ] Deployment note for the operator (goes in the handoff, not code): after merging, deploy the new `matcher.yml` and restart the crawler so the re-evaluation backfill retracts the evicted files; then verify `/files` "unidentified" shrank and `/targets/001A` no longer lists catch-all files, on the real node.
