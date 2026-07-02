# webui `/files` matched-only default + counter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the webui `/files` page show only matched files by default, with a server-side opt-in to the whole catalogue and a counter of how many files are hidden.

**Architecture:** Push a `matched_only` filter into `CatalogReader.list_files` and add a `count_files` reader for the `(matched, total)` pair; the `/files` handler defaults to matched-only, builds a precomputed `FilesSummary` (W-D8: no template logic), and threads a `show_unmatched` opt-in through the toggle link and pagination. `/targets/{id}` is unaffected (already target-filtered).

**Tech Stack:** Python 3.12, Starlette, Jinja2 (logic-free templates), SQLite (inline parameterized SQL), pytest + httpx `AsyncClient`, mypy strict, ruff.

## Global Constraints

- Python ≥ 3.12. Conventional commits (`feat(webui):`, `test:`, …).
- **100 % branch coverage, webui package**, gated. Exercise both sides of every conditional.
- **Strict TDD**: failing test first, watch it fail, then minimal implementation.
- `mypy --strict` over `src` AND `tests`; every test is `-> None` with typed params.
- `ruff` selects `E,F,I,UP,B,SIM`, line length 100.
- **All code is English** — identifiers AND prose (comments, docstrings, runtime strings).
- Clean/Hexagonal: `domain/` pure; all I/O in `adapters/`; view-models precomputed (spec W-D8 — templates only iterate/interpolate, no `{% if %}`).
- The webui gate (run from repo root):
  ```bash
  ( cd packages/webui && uv run pytest -q )
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy
  uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates
  ```
- Single test (coverage off, else `--cov-fail-under=100` fails a lone test):
  ```bash
  ( cd packages/webui && uv run pytest tests/<file>::<test> --no-cov -q )
  ```

---

### Task 1: Reader — `list_files(matched_only=...)` + shared filter-clause helper

**Files:**
- Modify: `packages/webui/src/catalog_webui/adapters/catalog_read.py` (add module helper `_filter_clauses`; add `matched_only` param to `list_files:188-243`)
- Test: `packages/webui/tests/test_webui_catalog_read.py`

**Interfaces:**
- Produces: module-level `_filter_clauses(target: str | None, tier: str | None, verdict: str | None, query: str | None) -> tuple[list[str], list[str]]`; `CatalogReader.list_files(*, target, tier, verdict, query, page, matched_only: bool = False) -> list[FileRow]`.
- Consumes: existing `_SQL_LIST_FILES_BASE`, `FileRow`, `PAGE_SIZE`.

Notes:
- Default `matched_only=False` **preserves current behaviour** for existing callers (`handle_target`, existing tests). The matched-only *default* lives in the handler (Task 3), not the reader.
- The matched clause is `dec.target_id IS NOT NULL` (parameterless). `dec` is the latest-decision LEFT JOIN, so this reads "has a latest decision" = matched.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_webui_catalog_read.py`. `_seed` inserts a MATCHED file (`a*32`, decision `S2E062A`). Add an unmatched file inline.

```python
def _seed_unmatched(db: Path) -> None:
    """Add a second file (b*32) with an observation but NO match decision."""
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", ("b" * 32, 200))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("b" * 32, "gallego_ep021.ogm", 200, 1, 0, "[]", "keroro",
             "2026-06-22T09:00:00.000000+00:00", "n2"),
        )
        conn.commit()


def test_list_files_matched_only_excludes_unmatched(catalog_db: Path) -> None:
    _seed(catalog_db)
    _seed_unmatched(catalog_db)
    reader = CatalogReader(open_ro(catalog_db))
    rows = reader.list_files(
        target=None, tier=None, verdict=None, query=None, page=1, matched_only=True
    )
    hashes = {r.ed2k_hash for r in rows}
    assert hashes == {"a" * 32}  # only the matched file


def test_list_files_default_includes_unmatched(catalog_db: Path) -> None:
    _seed(catalog_db)
    _seed_unmatched(catalog_db)
    reader = CatalogReader(open_ro(catalog_db))
    rows = reader.list_files(target=None, tier=None, verdict=None, query=None, page=1)
    hashes = {r.ed2k_hash for r in rows}
    assert hashes == {"a" * 32, "b" * 32}  # default matched_only=False → both
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `( cd packages/webui && uv run pytest tests/test_webui_catalog_read.py::test_list_files_matched_only_excludes_unmatched tests/test_webui_catalog_read.py::test_list_files_default_includes_unmatched --no-cov -q )`
Expected: FAIL — `list_files() got an unexpected keyword argument 'matched_only'`.

- [ ] **Step 3: Add the `_filter_clauses` helper**

Insert above the `CatalogReader` class (after the SQL constants, near `# CatalogReader` divider) in `catalog_read.py`:

```python
def _filter_clauses(
    target: str | None,
    tier: str | None,
    verdict: str | None,
    query: str | None,
) -> tuple[list[str], list[str]]:
    """Shared WHERE clauses + params for the explorer list and its counter.

    The matched-only clause and LIMIT/OFFSET are list-specific and are NOT built here.
    """
    clauses: list[str] = []
    params: list[str] = []
    if target is not None:
        clauses.append("dec.target_id = ?")
        params.append(target)
    if tier is not None:
        clauses.append("dec.tier = ?")
        params.append(tier)
    if verdict is not None:
        clauses.append("ver.verdict = ?")
        params.append(verdict)
    if query is not None:
        clauses.append("obs.filename LIKE ?")
        params.append(f"%{query}%")
    return clauses, params
```

- [ ] **Step 4: Rewrite `list_files` to use the helper + `matched_only`**

Replace the body of `list_files` (`catalog_read.py:188-243`) with:

```python
    def list_files(
        self,
        *,
        target: str | None,
        tier: str | None,
        verdict: str | None,
        query: str | None,
        page: int,
        matched_only: bool = False,
    ) -> list[FileRow]:
        """Return a page of ``FileRow`` (size ``_PAGE_SIZE``) with optional filters.

        Filters:
        - ``target`` : filter on ``dec.target_id`` (latest decision).
        - ``tier``   : filter on ``dec.tier`` (latest decision).
        - ``verdict``: filter on ``ver.verdict`` (latest verdict).
        - ``query``  : substring of ``obs.filename`` (LIKE ``%query%``).
        - ``matched_only``: when true, keep only files with a match decision
          (``dec.target_id IS NOT NULL``). Default false = whole catalogue.
        - ``page``   : page number (1-based).
        """
        clauses, str_params = _filter_clauses(target, tier, verdict, query)
        if matched_only:
            clauses.append("dec.target_id IS NOT NULL")
        params: list[str | int] = [*str_params]

        sql = _SQL_LIST_FILES_BASE
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + "\n"
        sql += "ORDER BY obs.observed_at DESC, f.ed2k_hash\n"
        sql += "LIMIT ? OFFSET ?\n"
        params.append(_PAGE_SIZE)
        params.append((page - 1) * _PAGE_SIZE)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            FileRow(
                ed2k_hash=row["ed2k_hash"],
                size_bytes=row["size_bytes"],
                filename=row["filename"] or "",
                source_count=row["source_count"],
                last_seen=row["last_seen"] or "",
                target_id=row["target_id"],
                tier=row["tier"],
                last_verdict=row["last_verdict"],
            )
            for row in rows
        ]
```

- [ ] **Step 5: Run the new + existing reader tests**

Run: `( cd packages/webui && uv run pytest tests/test_webui_catalog_read.py --no-cov -q )`
Expected: PASS (new tests green; existing `test_list_files_*` unchanged because the default is `matched_only=False`).

- [ ] **Step 6: Commit**

```bash
git add packages/webui/src/catalog_webui/adapters/catalog_read.py packages/webui/tests/test_webui_catalog_read.py
git commit -m "feat(webui): list_files matched_only filter + shared clause helper"
```

---

### Task 2: Reader — `count_files` + extract shared `_SQL_FILES_SOURCE`

**Files:**
- Modify: `packages/webui/src/catalog_webui/adapters/catalog_read.py` (extract `_SQL_FILES_SOURCE`; add `_SQL_COUNT_FILES_BASE`; add `count_files` method)
- Test: `packages/webui/tests/test_webui_catalog_read.py`

**Interfaces:**
- Produces: `CatalogReader.count_files(*, target: str | None, tier: str | None, verdict: str | None, query: str | None) -> tuple[int, int]` returning `(matched, total)`.
- Consumes: `_filter_clauses` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_webui_catalog_read.py` (reuses `_seed`, `_seed_unmatched` from Task 1):

```python
def test_count_files_no_filter_returns_matched_and_total(catalog_db: Path) -> None:
    _seed(catalog_db)  # 1 matched file
    _seed_unmatched(catalog_db)  # 1 unmatched file
    reader = CatalogReader(open_ro(catalog_db))
    matched, total = reader.count_files(target=None, tier=None, verdict=None, query=None)
    assert (matched, total) == (1, 2)


def test_count_files_respects_query_filter(catalog_db: Path) -> None:
    _seed(catalog_db)  # filename keroro_062.avi (matched)
    _seed_unmatched(catalog_db)  # filename gallego_ep021.ogm (unmatched)
    reader = CatalogReader(open_ro(catalog_db))
    matched, total = reader.count_files(target=None, tier=None, verdict=None, query="gallego")
    assert (matched, total) == (0, 1)  # only the unmatched file matches the query
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `( cd packages/webui && uv run pytest tests/test_webui_catalog_read.py::test_count_files_no_filter_returns_matched_and_total tests/test_webui_catalog_read.py::test_count_files_respects_query_filter --no-cov -q )`
Expected: FAIL — `'CatalogReader' object has no attribute 'count_files'`.

- [ ] **Step 3: Extract `_SQL_FILES_SOURCE` and add the count constant**

In `catalog_read.py`, replace the `_SQL_LIST_FILES_BASE` constant (`:52-101`) with the extracted source + the two derived queries. The concatenation is byte-identical to the original `_SQL_LIST_FILES_BASE`, so `list_files` is unaffected.

```python
# Shared source: files ⨝ latest observation ⨝ latest decision ⨝ latest verdict.
_SQL_FILES_SOURCE = """\
FROM files AS f
LEFT JOIN file_observations AS obs
    ON obs.ed2k_hash = f.ed2k_hash
    AND (
        SELECT COUNT(*)
        FROM file_observations AS obs2
        WHERE
            obs2.ed2k_hash = obs.ed2k_hash
            AND (
                obs2.observed_at > obs.observed_at
                OR (obs2.observed_at = obs.observed_at AND obs2.id > obs.id)
            )
    ) = 0
LEFT JOIN match_decisions AS dec
    ON dec.ed2k_hash = f.ed2k_hash
    AND (
        SELECT COUNT(*)
        FROM match_decisions AS dec2
        WHERE
            dec2.ed2k_hash = dec.ed2k_hash
            AND (
                dec2.decided_at > dec.decided_at
                OR (dec2.decided_at = dec.decided_at AND dec2.id > dec.id)
            )
    ) = 0
LEFT JOIN file_verifications AS ver
    ON ver.ed2k_hash = f.ed2k_hash
    AND (
        SELECT COUNT(*)
        FROM file_verifications AS ver2
        WHERE
            ver2.ed2k_hash = ver.ed2k_hash
            AND (
                ver2.verified_at > ver.verified_at
                OR (ver2.verified_at = ver.verified_at AND ver2.id > ver.id)
            )
    ) = 0
"""

# Explorer: files + latest joins, driven by files. Optional filters added in list_files().
_SQL_LIST_FILES_BASE = (
    """\
SELECT
    f.ed2k_hash,
    f.size_bytes,
    obs.filename,
    obs.source_count,
    obs.observed_at AS last_seen,
    dec.target_id,
    dec.tier,
    ver.verdict AS last_verdict
"""
    + _SQL_FILES_SOURCE
)

# Counter for the /files summary: (total, matched) over the same source + filters,
# WITHOUT the matched-only clause. COUNT(dec.target_id) counts non-null = matched.
_SQL_COUNT_FILES_BASE = (
    """\
SELECT
    COUNT(*) AS total,
    COUNT(dec.target_id) AS matched
"""
    + _SQL_FILES_SOURCE
)
```

- [ ] **Step 4: Add the `count_files` method**

Insert after `list_files` (before the `# Detail` divider) in `CatalogReader`:

```python
    def count_files(
        self,
        *,
        target: str | None,
        tier: str | None,
        verdict: str | None,
        query: str | None,
    ) -> tuple[int, int]:
        """Return ``(matched, total)`` file counts in the current filter scope.

        ``total`` = files matching the ``target/tier/verdict/query`` filters (the
        matched-only clause is deliberately NOT applied); ``matched`` = of those, how many
        have a match decision. Feeds the /files summary line.
        """
        clauses, params = _filter_clauses(target, tier, verdict, query)
        sql = _SQL_COUNT_FILES_BASE
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + "\n"
        row = self._conn.execute(sql, params).fetchone()
        matched: int = row["matched"]
        total: int = row["total"]
        return (matched, total)
```

- [ ] **Step 5: Run reader tests**

Run: `( cd packages/webui && uv run pytest tests/test_webui_catalog_read.py --no-cov -q )`
Expected: PASS (count tests green; `list_files` tests still green — the SQL constant is byte-identical).

- [ ] **Step 6: Commit**

```bash
git add packages/webui/src/catalog_webui/adapters/catalog_read.py packages/webui/tests/test_webui_catalog_read.py
git commit -m "feat(webui): count_files reader for the /files matched/total summary"
```

---

### Task 3: Handler + view-model + template — matched-only default, summary, toggle

**Files:**
- Modify: `packages/webui/src/catalog_webui/domain/views.py` (add `FilesSummary`)
- Modify: `packages/webui/src/catalog_webui/composition/app.py` (import `FilesSummary`; add `_build_summary`; rewrite `handle_files:162-206`)
- Modify: `packages/webui/src/catalog_webui/adapters/templates/files.html` (summary line + toggle link)
- Modify: `packages/webui/tests/test_webui_app.py` (fix the pagination fixture; add handler tests)

**Interfaces:**
- Consumes: `CatalogReader.list_files(..., matched_only=...)`, `CatalogReader.count_files(...)` (Tasks 1–2), existing `_page_nav`, `_normalize`, `urlencode`.
- Produces: `FilesSummary(summary_text: str, toggle_label: str, toggle_url: str)`; the `/files` handler now defaults to matched-only and passes `summary` to the template.

- [ ] **Step 1: Write the failing handler tests**

Add to `tests/test_webui_app.py`. `app_no_decision` (existing fixture) has exactly one **unmatched** file at `TEST_HASH` (observation, no decision) — perfect for both branches.

```python
@pytest.mark.asyncio
async def test_files_default_hides_unmatched(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """/files defaults to matched-only → an unmatched file is hidden."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] not in resp.text
    assert "Showing matched files only — 0 of 1 catalogued." in resp.text
    assert "Show all catalogued files" in resp.text
    assert "show_unmatched=1" in resp.text


@pytest.mark.asyncio
async def test_files_show_unmatched_reveals_and_toggles_back(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """/files?show_unmatched=1 reveals the whole catalogue; toggle points back to matched-only."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?show_unmatched=1")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert "Showing all catalogued files — 1 catalogued (0 matched)." in resp.text
    assert "Matched only" in resp.text
    assert 'href="/files"' in resp.text  # toggle drops the param (no filters) → bare /files
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `( cd packages/webui && uv run pytest tests/test_webui_app.py::test_files_default_hides_unmatched tests/test_webui_app.py::test_files_show_unmatched_reveals_and_toggles_back --no-cov -q )`
Expected: FAIL — the summary text / toggle strings are absent (and the unmatched file currently renders, so `hash_[:8] not in resp.text` fails).

- [ ] **Step 3: Add the `FilesSummary` view-model**

Append to `packages/webui/src/catalog_webui/domain/views.py` (after `PageNav`):

```python
@dataclass(frozen=True)
class FilesSummary:
    """Precomputed summary line for the /files explorer (spec W-D8: no template logic).

    ``summary_text`` states how many files are shown vs. catalogued; ``toggle_label`` +
    ``toggle_url`` flip between matched-only (default) and the whole catalogue.
    """

    summary_text: str
    toggle_label: str
    toggle_url: str
```

- [ ] **Step 4: Add `_build_summary` + rewrite `handle_files`**

In `packages/webui/src/catalog_webui/composition/app.py`:

1. Add `FilesSummary` to the view-models import (the `from catalog_webui.domain.views import (...)` block).
2. Add this module-level helper next to `_page_nav`:

```python
def _build_summary(
    matched: int, total: int, show_unmatched: bool, filter_query: dict[str, str]
) -> FilesSummary:
    """Precompute the /files summary line + matched/all toggle (W-D8: no template logic).

    The toggle preserves the active filters and drops ``page`` (counts differ between modes,
    so page N may not exist → back to page 1)."""
    if show_unmatched:
        summary_text = (
            f"Showing all catalogued files — {total:,} catalogued ({matched:,} matched)."
        )
        toggle_label = "Matched only"
        toggle_query = dict(filter_query)  # drop show_unmatched → matched only
    else:
        summary_text = f"Showing matched files only — {matched:,} of {total:,} catalogued."
        toggle_label = "Show all catalogued files"
        toggle_query = {**filter_query, "show_unmatched": "1"}
    toggle_url = "/files?" + urlencode(toggle_query) if toggle_query else "/files"
    return FilesSummary(
        summary_text=summary_text, toggle_label=toggle_label, toggle_url=toggle_url
    )
```

3. Replace `handle_files` (`app.py:162-206`) with:

```python
    async def handle_files(request: Request) -> Response:
        # Filters: ``param.strip() or None`` (webui-security#0) — a select with an empty option
        # sent ``?target=`` (empty string) that matched 0 results with no message.
        target_param = _normalize(request.query_params.get("target"))
        tier_param = _normalize(request.query_params.get("tier"))
        verdict_param = _normalize(request.query_params.get("verdict"))
        query_param = _normalize(request.query_params.get("q"))
        # Presence of ``show_unmatched`` (any value) opts into the whole catalogue.
        show_unmatched = request.query_params.get("show_unmatched") is not None
        page_raw = request.query_params.get("page", "1")
        try:
            page = int(page_raw)
        except ValueError:
            page = 1
        # ``max(1, ...)`` (webui-security#2) — ``?page=0`` → OFFSET=-50 which SQLite treats as 0.
        page = max(1, page)

        with contextlib.closing(open_ro(catalog_db)) as catalog_conn:
            catalog = CatalogReader(catalog_conn)
            file_rows = catalog.list_files(
                target=target_param,
                tier=tier_param,
                verdict=verdict_param,
                query=query_param,
                page=page,
                matched_only=not show_unmatched,
            )
            matched, total = catalog.count_files(
                target=target_param,
                tier=tier_param,
                verdict=verdict_param,
                query=query_param,
            )

        display_rows = _to_display_rows(file_rows)
        # Filters shared by the toggle link and the page nav.
        filter_query = {
            k: v
            for k, v in {
                "target": target_param,
                "tier": tier_param,
                "verdict": verdict_param,
                "q": query_param,
            }.items()
            if v is not None
        }
        summary = _build_summary(matched, total, show_unmatched, filter_query)

        # Precomputed prev/next links (webui-security#1); the nav preserves ``show_unmatched``.
        nav_query = dict(filter_query)
        if show_unmatched:
            nav_query["show_unmatched"] = "1"
        nav = _page_nav(page, len(display_rows), "/files", nav_query)
        return templates.TemplateResponse(
            request,
            "files.html",
            {"rows": display_rows, "nav": nav, "summary": summary},
        )
```

- [ ] **Step 5: Add the summary line to the template**

In `packages/webui/src/catalog_webui/adapters/templates/files.html`, insert after `<h1>Files</h1>` (line 4). No logic — plain interpolation of precomputed strings:

```html
<p class="files-summary">
  {{ summary.summary_text }}
  <a href="{{ summary.toggle_url }}">{{ summary.toggle_label }}</a>
</p>
```

- [ ] **Step 6: Fix the existing pagination test (unmatched → matched)**

`test_files_page_shows_pagination_navigation` (`tests/test_webui_app.py:620`) seeds 50 files with **no** decision — they now vanish under the matched-only default. Give each a decision so the test keeps exercising a full page. Inside the `for i in range(50):` loop, after the `file_observations` insert, add:

```python
            conn.execute(
                "INSERT INTO match_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (i + 1, ed2k, "S2E062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
            )
```

- [ ] **Step 7: Run the app tests**

Run: `( cd packages/webui && uv run pytest tests/test_webui_app.py --no-cov -q )`
Expected: PASS — the two new tests, the fixed pagination test, and all existing `/files` tests (`populated_app` has a matched file → still shown under the default).

- [ ] **Step 8: Run the full webui gate**

```bash
( cd packages/webui && uv run pytest -q )
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates
```
Expected: all green, webui coverage 100 %.

- [ ] **Step 9: Commit**

```bash
git add packages/webui/src/catalog_webui/domain/views.py \
        packages/webui/src/catalog_webui/composition/app.py \
        packages/webui/src/catalog_webui/adapters/templates/files.html \
        packages/webui/tests/test_webui_app.py
git commit -m "feat(webui): /files defaults to matched-only with a catalogue counter + toggle"
```

---

## Self-review

- **Spec coverage:**
  - §1 semantics/param → Task 1 (`matched_only`) + Task 3 (`show_unmatched` → `matched_only = not show_unmatched`, default matched-only). `/targets/{id}` unchanged (default `matched_only=False`, target filter implies matched) ✓
  - §2 counter (filter-aware, single query, conditional aggregation) → Task 2 `count_files` (`COUNT(*)`, `COUNT(dec.target_id)`, shared `_filter_clauses`) ✓
  - §3 UI (English summary + toggle, preserve filters, reset page) → Task 3 `_build_summary` (drops `page` from `toggle_query`) + template ✓
  - §4 code changes (list_files, count_files, handle_files, FilesSummary, template) → Tasks 1–3 ✓
  - §5 tests (matched default, show-all, count with/without query, toggle direction, empty-`toggle_query` branch, both `show_unmatched` branches) → Tasks 1–3 tests ✓
- **Placeholder scan:** none — every step carries full code/commands.
- **Type consistency:** `_filter_clauses -> tuple[list[str], list[str]]` used by both readers; `count_files -> tuple[int, int]` = `(matched, total)`, consumed as `matched, total = ...` in the handler; `FilesSummary(summary_text, toggle_label, toggle_url)` matches the template fields ✓
- **Branch coverage watch:** `if clauses` (both readers) — empty via no-filter tests, non-empty via filter tests; `if matched_only` — True (Task 1) / False (existing); `if show_unmatched` and `if toggle_query else` in `_build_summary` — both hit by the two Task 3 tests (default: non-empty toggle_query; show-all no-filter: empty toggle_query); `if show_unmatched` in nav — both branches hit by the same two tests.
