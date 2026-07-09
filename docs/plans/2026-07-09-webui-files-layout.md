# WebUI `/files` Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `/files` explorer readable by widening the page and stopping cells from wrapping mid-value: atoms never break, multi-segment Target/Title cells stack per segment, long name/title truncate with an ellipsis.

**Architecture:** Two changes. (1) Structured rendering: the `FileRowDisplay` view-model exposes a per-decision `decisions_display` tuple instead of two `" · "`-joined strings, and `files.html` stacks each segment on its own line for Target and Title. (2) Presentation: a viewport-aware body width in `rem`, per-column `white-space`/ellipsis rules, and a horizontal-scroll wrapper around the table. Both `/files` and `/targets/{id}` share the same template + view-model, so both benefit.

**Tech Stack:** Python 3.14, Starlette + Jinja2 (autoescaped `.html`), frozen dataclasses, pytest + httpx `AsyncClient`/`ASGITransport`, plain CSS (vendored, no CDN).

## Global Constraints

- **Python ≥ 3.14 only.** `mypy --strict` over both `src` and `tests`. `ruff` selects `E,F,I,UP,B,SIM`, line-length **100**.
- **100% branch coverage per package**, gated. Every test function is `-> None` with typed params. Exercise both sides of every conditional.
- **Strict TDD:** write the failing test first, watch it fail, then the minimal implementation.
- **All code is English** (identifiers, prose, commit messages). Genuine domain data (VF episode titles, filenames) stays as-is.
- **No em-dash / en-dash (`—` / `–`)** anywhere written: UI, comments, docstrings, commit messages. Use `:`, `.`, `()`, or a short `-`.
- **Template guard (`webui/_dev/check_templates.py`):** `{% if %}` is forbidden; `{% for %}{% else %}` is allowed and is the idiom for conditional rendering. No logic in templates beyond iteration/interpolation (spec W-D8).
- **`domain/` is pure:** `views.py` holds only frozen dataclasses, no I/O.
- **Per-package gate:** run tests as `( cd packages/crawler && uv run pytest ... )`, never a bare root `pytest`.

---

## Task 1: Structured multi-value rendering (view-model + builder + template)

Replace the two joined strings with a per-decision tuple and stack the segments in the template. This is one atomic change: the template references the fields the view-model exposes, so the view-model, the builder, the template, and their tests must move together to keep the suite green at the commit.

**Files:**
- Modify: `packages/crawler/src/mulewatch/webui/domain/views.py` (add `DecisionCell`; replace `target_display` + `title_display` on `FileRowDisplay` with `decisions_display`)
- Modify: `packages/crawler/src/mulewatch/webui/composition/app.py:77-115` (`_to_display_rows`)
- Modify: `packages/crawler/src/mulewatch/webui/adapters/templates/files.html:69-70` (Target + Title `<td>`s)
- Test: `packages/crawler/tests/webui/test_webui_app.py` (unit builder tests + HTTP rendering tests)

**Interfaces:**
- Consumes: `FileRow.decisions: tuple[FileDecision, ...]`, `_resolve_target_display(row, segment_by_id) -> list[tuple[str, str]]` (unchanged; still returns per-decision `(target, title)` pairs in decision order).
- Produces:
  - `DecisionCell(target: str, title: str)` frozen dataclass.
  - `FileRowDisplay.decisions_display: tuple[DecisionCell, ...]` (0..N, one per current decision; empty tuple when the file has no current decision). `tier_display: str` and `verdict_display: str` are UNCHANGED.
  - `files.html` renders each Target cell as `<td>{% for d in row.decisions_display %}<div class="cell-line">{{ d.target }}</div>{% else %}·{% endfor %}</td>` and each Title cell as `<td>{% for d in row.decisions_display %}<div class="cell-line cell-title" title="{{ d.title }}">{{ d.title }}</div>{% else %}·{% endfor %}</td>`. The `cell-line` / `cell-title` class names are the contract styled by Task 2.

### Sub-cycle A: view-model + builder

- [ ] **Step 1: Rewrite the two builder unit tests to expect `decisions_display`**

In `packages/crawler/tests/webui/test_webui_app.py`, add `DecisionCell` to the existing `mulewatch.webui.domain.views` import, then replace these two tests:

```python
def test_to_display_rows_empty_decisions_all_dashes() -> None:
    [display] = _to_display_rows([_file_row(decisions=())], _SEGMENTS_AB)
    assert display.decisions_display == ()
    assert display.tier_display == "·"
    assert display.verdict_display == "·"


def test_to_display_rows_two_segments_aggregate_cells_shared_tier() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"), FileDecision("062B", "download")))
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.decisions_display == (
        DecisionCell(target="062A / S02E11A", title="La Grenouille Cosmique"),
        DecisionCell(target="062B / S02E11B", title="Duel Contre Giroro"),
    )
    assert display.tier_display == "download"
```

- [ ] **Step 2: Run the builder tests, verify they fail**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py::test_to_display_rows_two_segments_aggregate_cells_shared_tier tests/webui/test_webui_app.py::test_to_display_rows_empty_decisions_all_dashes --no-cov -q )`
Expected: FAIL with `ImportError: cannot import name 'DecisionCell'` (import at module load fails).

- [ ] **Step 3: Add `DecisionCell` and swap the fields in `views.py`**

In `domain/views.py`, add above `FileRowDisplay`:

```python
@dataclass(frozen=True)
class DecisionCell:
    """One current decision, resolved for display: the target locator and its episode title.

    ``target`` is the canonical id joined with its seasonal locator (``"062A / S02E11A"``), a raw
    id no longer in the catalogue, or ``"unidentified"`` (the ``catalog``-tier mask). ``title`` is
    the episode title, or ``"·"`` when there is none (unidentified / unknown id)."""

    target: str
    title: str
```

In `FileRowDisplay`, delete the `target_display: str` and `title_display: str` fields and add:

```python
    decisions_display: tuple[DecisionCell, ...]  # one per current decision, 0..N; () when none
```

Update the `FileRowDisplay` docstring: the per-decision `(target, title)` pair is now one
`DecisionCell` per element of `decisions_display` (drop the "joined with `" · "`" wording for
target/title; `tier_display` and `verdict_display` keep their existing description).

- [ ] **Step 4: Build `decisions_display` in `_to_display_rows`**

In `composition/app.py`, import `DecisionCell` alongside `FileRowDisplay`, then in `_to_display_rows` replace the target/title join block. The `if row.decisions:` branch becomes:

```python
        if row.decisions:
            pairs = _resolve_target_display(row, segment_by_id)
            decisions_display = tuple(DecisionCell(target=t, title=ti) for t, ti in pairs)
            tier_values = {dec.tier for dec in row.decisions}
            if len(tier_values) == 1:
                tier_display = row.decisions[0].tier
            else:
                tier_display = " · ".join(f"{dec.target_id}: {dec.tier}" for dec in row.decisions)
            verdict_display = row.last_verdict if row.last_verdict is not None else "pending"
        else:
            decisions_display = ()
            tier_display = "·"
            verdict_display = "·"
```

and in the `FileRowDisplay(...)` construction replace the `target_display=...` / `title_display=...`
keyword arguments with `decisions_display=decisions_display,`.

- [ ] **Step 5: Run the builder tests, verify they pass**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py::test_to_display_rows_two_segments_aggregate_cells_shared_tier tests/webui/test_webui_app.py::test_to_display_rows_empty_decisions_all_dashes --no-cov -q )`
Expected: PASS. (The full suite is still red: the template and HTTP tests come next.)

### Sub-cycle B: template + HTTP rendering tests

- [ ] **Step 6: Rewrite the HTTP rendering tests for the stacked markup**

In `packages/crawler/tests/webui/test_webui_app.py`, update the cell-scoped assertions:

In `test_files_catalog_tier_shows_unidentified_and_pending`, replace `assert "<td>unidentified</td>" in resp.text` with:

```python
    assert '<div class="cell-line">unidentified</div>' in resp.text
```

In `test_files_unknown_target_shows_raw_id_and_dash_title`, replace `assert "<td>unidentified</td>" not in resp.text` with:

```python
    assert '<div class="cell-line">unidentified</div>' not in resp.text
```

In `test_files_no_decision_shows_dashes`, replace `assert "<td>unidentified</td>" not in resp.text` with:

```python
    assert '<div class="cell-line">unidentified</div>' not in resp.text
```

(Its `assert "<td>pending</td>" not in resp.text` stays: `verdict_display` is still a bare `<td>` value.)

In `test_files_whole_episode_renders_one_row_with_aggregated_targets`, replace the two joined-cell assertions:

```python
    assert '<div class="cell-line">072A / S03E06A</div>' in resp.text
    assert '<div class="cell-line">072B / S03E06B</div>' in resp.text
    assert "Le Defi" in resp.text
    assert "Duel Contre Giroro" in resp.text
    assert "<td>download</td>" in resp.text
```

- [ ] **Step 7: Run the four HTTP tests, verify they fail**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py -k "catalog_tier_shows_unidentified or unknown_target_shows_raw_id or no_decision_shows_dashes or whole_episode_renders_one_row" --no-cov -q )`
Expected: FAIL. The template still emits `{{ row.target_display }}` (now an undefined attribute), so the stacked `<div class="cell-line">` markup is absent.

- [ ] **Step 8: Stack the segments in `files.html`**

In `adapters/templates/files.html`, replace the Target and Title cells (currently lines 69-70):

```html
      <td>{% for d in row.decisions_display %}<div class="cell-line">{{ d.target }}</div>{% else %}·{% endfor %}</td>
      <td>{% for d in row.decisions_display %}<div class="cell-line cell-title" title="{{ d.title }}">{{ d.title }}</div>{% else %}·{% endfor %}</td>
```

(Autoescape is on for `.html`, so `title="{{ d.title }}"` is safely escaped, consistent with `test_hostile_filename_is_escaped_in_ed2k_link`.)

- [ ] **Step 9: Run the full webui suite, verify green**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py -q )`
Expected: PASS (all tests, including the untouched substring tests `test_files_resolvable_target_shows_seasonal_locator_and_title` and `test_target_page_resolves_title_via_segment_mapping`, which still find `062A / S02E11A` and `La Grenouille Cosmique` in the stacked markup).

- [ ] **Step 10: Commit**

```bash
git add packages/crawler/src/mulewatch/webui/domain/views.py \
        packages/crawler/src/mulewatch/webui/composition/app.py \
        packages/crawler/src/mulewatch/webui/adapters/templates/files.html \
        packages/crawler/tests/webui/test_webui_app.py
git commit -m "feat(webui): stack multi-segment Target/Title cells in /files"
```

---

## Task 2: Presentation (viewport width + per-column rules + horizontal scroll)

Give the table room and stop long content from wrapping: a wider viewport-aware body, `nowrap` on the short atoms, an ellipsis on the name and on each stacked title, and an `overflow-x` wrapper so a narrow desktop window scrolls instead of crushing columns. CSS is not unit-tested (verified by eye on the real node); the added markup hooks (`title=` on the name, the scroll wrapper) get one guard test.

**Files:**
- Modify: `packages/crawler/src/mulewatch/webui/adapters/static/app.css` (body width; `.files-scroll`; column rules)
- Modify: `packages/crawler/src/mulewatch/webui/adapters/templates/files.html` (wrap the table in `.files-scroll`; add `col-*` classes + `title=` on the Name cell)
- Test: `packages/crawler/tests/webui/test_webui_app.py` (one markup guard test)

**Interfaces:**
- Consumes: `FileRowDisplay.filename`, and the `cell-line` / `cell-title` classes emitted by Task 1.
- Produces: a `<div class="files-scroll">` wrapping `<table class="files-table">`; a `<td class="col-name"><span class="cell-name" title="{{ row.filename }}">{{ row.filename }}</span></td>` Name cell.

- [ ] **Step 1: Write the markup guard test**

In `packages/crawler/tests/webui/test_webui_app.py`, add (near the other `/files` HTTP tests):

```python
@pytest.mark.asyncio
async def test_files_table_is_scroll_wrapped_and_name_has_full_title(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    """The table is wrapped for horizontal scroll on narrow desktop widths, and the (possibly
    truncated) filename keeps its full text in a title= tooltip."""
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert '<div class="files-scroll">' in resp.text
    assert 'class="cell-name" title="keroro_s2e62a_vf.avi"' in resp.text
```

(The `app_download_tier_known_target` fixture's latest observation is named `keroro_s2e62a_vf.avi`.)

- [ ] **Step 2: Run the guard test, verify it fails**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py::test_files_table_is_scroll_wrapped_and_name_has_full_title --no-cov -q )`
Expected: FAIL: neither `files-scroll` nor `cell-name` markup exists yet.

- [ ] **Step 3: Wrap the table and annotate the Name cell in `files.html`**

Wrap `<table class="files-table"> ... </table>` in a scroll container:

```html
<div class="files-scroll">
<table class="files-table">
  ...
</table>
</div>
```

Replace the Name cell (currently `<td>{{ row.filename }}</td>`, line 65) with:

```html
      <td class="col-name"><span class="cell-name" title="{{ row.filename }}">{{ row.filename }}</span></td>
```

- [ ] **Step 4: Add the width and column rules to `app.css`**

Change the body width (line 17): replace `max-width: 1100px;` with:

```css
  max-width: min(100rem, 94vw);
```

Append a `/files` layout block after the existing header-tooltip rules:

```css
/* /files table: keep short atoms on one line, truncate the unbounded fields, and let a narrow
   desktop window scroll the table horizontally instead of crushing the columns. */
.files-scroll { overflow-x: auto; }

/* Short atoms never break (fixes the split datetime). */
.files-table td, .files-table th { white-space: nowrap; }

/* One stacked line per decision in the Target / Title cells. */
.files-table .cell-line { white-space: nowrap; }

/* The two unbounded fields truncate with an ellipsis; full text lives in the title= tooltip. */
.files-table .cell-name,
.files-table .cell-title {
  display: inline-block;
  max-width: 22rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.files-table .cell-title { max-width: 20rem; }
```

- [ ] **Step 5: Run the guard test, verify it passes**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py::test_files_table_is_scroll_wrapped_and_name_has_full_title --no-cov -q )`
Expected: PASS.

- [ ] **Step 6: Run the full webui suite**

Run: `( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py -q )`
Expected: PASS. (The `test_files_tier_header_has_tooltip*` and `*_nonobvious_columns_have_header_tooltips` tests still pass: the `.th-tip` rules are untouched, and `.files-table { overflow: visible }` in `app.css` still lets the header tooltips escape; note the new `.files-scroll { overflow-x: auto }` is a different element wrapping the table, so it does not clip the header tooltips, which open downward within the table's own box. Verify this by eye on the node in the Verify phase.)

- [ ] **Step 7: Commit**

```bash
git add packages/crawler/src/mulewatch/webui/adapters/static/app.css \
        packages/crawler/src/mulewatch/webui/adapters/templates/files.html \
        packages/crawler/tests/webui/test_webui_app.py
git commit -m "feat(webui): widen the page and truncate long /files cells"
```

---

## Final verification (Verify phase, after both tasks)

- [ ] **Full gate:** `uv run poe check` (lint-all + per-package tests at 100% branch). Expected: green.
- [ ] **By eye on the real node** (per the "verify UI changes in the real container" habit): load `/files` and `/targets/{id}` on the operator's node; confirm the stacked Target/Title alignment, the name/title ellipsis with a working `title=` tooltip, the datetime no longer splitting, the wider layout, and that the header `?` tooltips still open and are readable (not clipped by the scroll wrapper).

## Self-review notes

- **Spec coverage:** §3.1 body width → Task 2 Step 4. §3.2 per-column table → Task 2 Steps 3-4. §3.3 structured rendering (view-model, builder, template) → Task 1. §4 testing → Task 1 (unit + HTTP) and Task 2 (guard). §5 out of scope respected (no mobile breakpoint; other tables inherit only the body width).
- **Type consistency:** `DecisionCell(target, title)` defined in Task 1 Step 3, consumed in Step 4 (builder) and Steps 6/8 (template `d.target` / `d.title`). Class names `cell-line`, `cell-title`, `cell-name`, `files-scroll`, `col-name` are consistent between Task 1 (markup) and Task 2 (CSS + guard test).
- **Coverage risk:** the `if row.decisions:` / `else` branches in `_to_display_rows` remain exercised by `test_to_display_rows_two_segments_*` (true) and `test_to_display_rows_empty_decisions_all_dashes` (false); the shared-vs-disagree tier branch stays covered by the untouched `*_shared_tier` / `*_differing_tiers` tests.
