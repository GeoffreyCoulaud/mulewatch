# `/files` explorer: sorting, search, tier facet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use subagent-driven-development to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the already-supported `/files` filters as a discovery UI: bidirectional
column sorting, filename search, and a tier facet with live counts, for a passive external
researcher.

**Architecture:** Server-side, URL-driven, injection-safe. The read layer (`catalog_read.py`)
gains a sort allowlist + a `best_tier_rank` computed column + a `tier_counts` facet read. The
handler (`app.py`) normalizes params against the allowlist and precomputes view-models
(`domain/views.py`); the logic-free template (`files.html`) renders them. No schema change, no
engine change.

**Tech stack:** Python 3.14, SQLite (stdlib `sqlite3`), Starlette + Jinja2, pytest (strict TDD).

## Global Constraints

Every task's requirements implicitly include these:

- **Strict TDD**: write the failing test first, watch it fail, then the minimal code. Every test
  function is `-> None` with typed params.
- **100% branch coverage, crawler package** (`--cov-fail-under=100`, `branch=true`). Exercise
  both sides of every conditional. Run the package gate from `packages/crawler`.
- **`mypy --strict`** over `src` AND `tests`. **`ruff`** (E,F,I,UP,B,SIM, line length 100).
- **W-D8 (logic-free templates)**: templates carry NO logic. `check_templates` forbids
  `{% if %}`/`{% elif %}`/`{% set %}`/`{% macro %}` and any `{{ ... }}` containing an operator
  (`+ - * / % = ! < >`), a filter (`|`), or a call (`(`). ALL branching/formatting is
  precomputed in the handler/domain into view-model string fields. `{% for x in (a,) if x %}`
  IS allowed (it is a `for`, not an `if` tag). The 0-or-1-element-tuple pattern (like the
  existing `summaries`) is how "render only if present" is expressed.
- **Injection-safe sorting**: no query-param value is ever interpolated into SQL. Sort maps
  through a fixed column ALLOWLIST; direction through a fixed `{asc,desc}→{ASC,DESC}` map. An
  unknown/malformed value falls back to the default, never errors, never reaches SQL raw.
- **`TIER_RANK` is the single source of tier order.** The SQL tier-rank CASE is GENERATED in
  Python from `catalog_matching.config.TIER_RANK` (trusted constants), not hand-duplicated.
- **No em-dashes or en-dashes** (`—` / `–`) anywhere a user or reader sees them: UI strings,
  labels, docstrings, comments, commit messages. Use `:`/`.`/`(...)`/`-`/`·`.
- **All code is English** (identifiers, comments, docstrings, commit messages). Conventional
  commits (`feat(webui):`, `test:`, ...).
- **No schema change, no matcher.yml change, no engine change.** `catalog_matching.config` is
  imported read-only.
- Commit message trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## File Structure

- `packages/crawler/src/mulewatch/webui/adapters/catalog_read.py` — sort allowlist +
  `best_tier_rank` in the `dec_agg` CTE + `list_files` sort params + new `tier_counts`.
- `packages/crawler/src/mulewatch/webui/domain/views.py` — new frozen view-models:
  `SortHeader`, `SortHeaders`, `TierFacet`, `SearchBar`, `HiddenInput`, `FilterBar`.
- `packages/crawler/src/mulewatch/webui/composition/app.py` — sort/dir normalization, the
  URL-building precompute helpers, `handle_files` wiring, `handle_target` empties.
- `packages/crawler/src/mulewatch/webui/adapters/templates/files.html` — sortable `<thead>` +
  the filter bar (search form + tier facet).
- `packages/crawler/src/mulewatch/webui/adapters/static/app.css` — filter-bar layout, tier-facet
  chips, sort-direction indicator.
- `packages/crawler/tests/webui/test_webui_catalog_read.py` — read-layer tests.
- `packages/crawler/tests/webui/test_webui_app.py` — builder + handler + rendered-HTML tests.

## Conventions used by every task

Run the package gate from the crawler package:

```bash
( cd packages/crawler && uv run pytest tests/webui/<file>::<test> --no-cov -q )   # single test
( cd packages/crawler && uv run pytest )                                          # full package (100% cov)
```

`uv run poe fix` (from repo root) auto-fixes ruff + format + sqlfluff — run it before hand-fixing
lint. `uv run poe lint-all` runs ruff + mypy + template-check + sqlfluff.

---

### Task 1: Read layer — sortable `list_files` + `best_tier_rank` + tier-rank CASE

**Files:**
- Modify: `packages/crawler/src/mulewatch/webui/adapters/catalog_read.py`
- Test: `packages/crawler/tests/webui/test_webui_catalog_read.py`

**Interfaces:**
- Consumes: `catalog_matching.config.TIER_RANK` (`{"catalog":0,"notify":1,"download":2}`).
- Produces (used by Task 3):
  - `SORT_COLUMNS: dict[str, str]` — allowlist key → ORDER BY expression.
  - `SORT_DIRECTIONS: dict[str, str]` — `{"asc":"ASC","desc":"DESC"}`.
  - `DEFAULT_SORT = "last_seen"`, `DEFAULT_DIR = "desc"`.
  - `list_files(..., sort: str = DEFAULT_SORT, direction: str = DEFAULT_DIR)` — two NEW
    keyword params, defaulted so existing call sites are unchanged.

- [ ] **Step 1: Write the failing tests** (append to `test_webui_catalog_read.py`)

Add a seed helper and the sort/rank tests. `_seed_sortable` creates three single-decision files
with distinct name/size/sources/last_seen and one decision each at a distinct tier:

```python
from catalog_matching.config import TIER_RANK
from mulewatch.webui.adapters.catalog_read import (
    DEFAULT_DIR,
    DEFAULT_SORT,
    SORT_COLUMNS,
    _tier_rank_case,
)


def _seed_sortable(db: Path) -> None:
    """Three single-decision files with distinct sort keys and one decision each at a distinct
    tier (download > notify > catalog), for the sort-order tests.

    hash 'a': beta.avi  size 300 sources  5 seen 2026-01-02 tier notify   (062A)
    hash 'b': alpha.avi size 100 sources 15 seen 2026-01-03 tier download (072A)
    hash 'c': gamma.avi size 200 sources 10 seen 2026-01-01 tier catalog  (001A)

    last_seen is deliberately DECORRELATED from size (a is the largest but not the newest) so a
    size/last_seen column swap cannot pass unnoticed; every sort key yields its own order.
    """
    rows = [
        ("a" * 32, "beta.avi", 300, 5, "2026-01-02T10:00:00.000000+00:00", "062A", "notify"),
        ("b" * 32, "alpha.avi", 100, 15, "2026-01-03T10:00:00.000000+00:00", "072A", "download"),
        ("c" * 32, "gamma.avi", 200, 10, "2026-01-01T10:00:00.000000+00:00", "001A", "catalog"),
    ]
    with sqlite3.connect(db) as conn:
        for h, name, size, sources, seen, tid, tier in rows:
            conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, size))
            conn.execute(
                "INSERT INTO file_observations"
                " (ed2k_hash, filename, size_bytes, source_count,"
                " complete_source_count, raw_meta, keyword, observed_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (h, name, size, sources, 0, "[]", "keroro", seen, "n1"),
            )
            conn.execute(
                "INSERT INTO match_decisions"
                " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (h, tid, "rule", tier, "2026-01-04T10:00:00.000000+00:00", "n1"),
            )
        conn.commit()


def _hashes(rows: list[FileRow]) -> list[str]:  # from mulewatch.webui.domain.views import FileRow
    return [r.ed2k_hash for r in rows]


@pytest.mark.parametrize(
    ("sort", "direction", "expected"),
    [
        ("name", "asc", ["b", "a", "c"]),          # alpha, beta, gamma
        ("name", "desc", ["c", "a", "b"]),
        ("size", "asc", ["b", "c", "a"]),          # 100, 200, 300
        ("size", "desc", ["a", "c", "b"]),
        ("sources", "asc", ["a", "c", "b"]),       # 5, 10, 15
        ("sources", "desc", ["b", "c", "a"]),
        ("last_seen", "desc", ["b", "a", "c"]),    # 01-03, 01-02, 01-01 (the default)
        ("last_seen", "asc", ["c", "a", "b"]),
        ("tier", "desc", ["b", "a", "c"]),         # download(2), notify(1), catalog(0)
        ("tier", "asc", ["c", "a", "b"]),
    ],
)
def test_list_files_sort_orders(
    catalog_db: Path, sort: str, direction: str, expected: list[str]
) -> None:
    _seed_sortable(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(
        target=None, tier=None, verdict=None, query=None, page=1, sort=sort, direction=direction
    )
    assert _hashes(rows) == [c * 32 for c in expected]


def test_list_files_unknown_sort_falls_back_to_default(catalog_db: Path) -> None:
    _seed_sortable(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(
        target=None, tier=None, verdict=None, query=None, page=1, sort="bogus", direction="desc"
    )
    # default sort is last_seen desc
    assert _hashes(rows) == [c * 32 for c in ["b", "a", "c"]]


def test_list_files_unknown_direction_falls_back_to_default(catalog_db: Path) -> None:
    _seed_sortable(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(
        target=None, tier=None, verdict=None, query=None, page=1, sort="size", direction="bogus"
    )
    # default direction is desc → size desc → 300, 200, 100
    assert _hashes(rows) == [c * 32 for c in ["a", "c", "b"]]


def test_list_files_sort_injection_is_rejected_not_interpolated(catalog_db: Path) -> None:
    _seed_sortable(catalog_db)
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(
        target=None,
        tier=None,
        verdict=None,
        query=None,
        page=1,
        sort="size; drop table files",
        direction="desc",
    )
    # falls back to the default last_seen desc [b,a,c], NOT size desc [a,c,b]: the value was
    # dropped by the allowlist, not interpolated (a raw interpolation would also raise, since
    # sqlite3 refuses multi-statement execute). The table still exists too.
    assert _hashes(rows) == [c * 32 for c in ["b", "a", "c"]]
    with sqlite3.connect(catalog_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3


def test_list_files_sort_tiebreak_is_ed2k_hash(catalog_db: Path) -> None:
    """Two files with the same sort key keep a deterministic order via the ed2k_hash tiebreak."""
    with sqlite3.connect(catalog_db) as conn:
        for h in ("b" * 32, "a" * 32):  # inserted b-first on purpose
            conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 100))
            conn.execute(
                "INSERT INTO file_observations"
                " (ed2k_hash, filename, size_bytes, source_count,"
                " complete_source_count, raw_meta, keyword, observed_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (h, "same.avi", 100, 1, 0, "[]", "keroro", "2026-01-01T10:00:00.000000+00:00", "n"),
            )
        conn.commit()
    reader = CatalogReader(open_reader(catalog_db))
    rows = reader.list_files(
        target=None, tier=None, verdict=None, query=None, page=1, sort="size", direction="asc"
    )
    assert _hashes(rows) == ["a" * 32, "b" * 32]  # ed2k_hash asc breaks the tie


def test_tier_rank_case_matches_TIER_RANK() -> None:
    """The generated CASE maps every tier to its TIER_RANK integer (single-source guard: red if
    the CASE and the dict diverge)."""
    sql = _tier_rank_case("ld.tier")
    for tier, rank in TIER_RANK.items():
        assert f"WHEN '{tier}' THEN {rank}" in sql
```

- [ ] **Step 2: Run the tests, watch them fail**

```bash
( cd packages/crawler && uv run pytest tests/webui/test_webui_catalog_read.py -k "sort or tier_rank" --no-cov -q )
```
Expected: FAIL (ImportError on `_tier_rank_case`/`SORT_COLUMNS`/... , then `list_files` TypeError
on unexpected `sort` kwarg).

- [ ] **Step 3: Implement in `catalog_read.py`**

Add the import and the CASE generator near the top (after the existing imports):

```python
from catalog_matching.config import TIER_RANK
```

```python
def _tier_rank_case(column: str) -> str:
    """Generate a SQL CASE mapping a tier column to its ``TIER_RANK`` integer, from the trusted
    constant. ``TIER_RANK`` keys are the closed ``TIERS`` enum and its values are ints, so
    interpolating them is safe (no user input); this keeps ONE source of truth for tier order
    (the file's strongest tier = ``MAX`` of this expression)."""
    whens = " ".join(f"WHEN '{tier}' THEN {rank}" for tier, rank in TIER_RANK.items())
    return f"CASE {column} {whens} END"


_TIER_RANK_CASE = _tier_rank_case("ld.tier")
```

Turn `_SQL_CTES` into an f-string and add `best_tier_rank` to `dec_agg` (the rest of the block is
unchanged and contains no `{` or `}`):

```python
_SQL_CTES = f"""\
WITH latest_dec AS (
    ...unchanged...
),
dec_agg AS (
    SELECT
        ld.ed2k_hash,
        group_concat(ld.target_id, char(31) ORDER BY ld.target_id) AS target_ids,
        group_concat(ld.tier, char(31) ORDER BY ld.target_id) AS tiers,
        MAX({_TIER_RANK_CASE}) AS best_tier_rank
    FROM latest_dec AS ld
    GROUP BY ld.ed2k_hash
),
latest_obs AS (
    ...unchanged...
),
latest_ver AS (
    ...unchanged...
)
"""
```

Add the sort allowlist as module constants (near `PAGE_SIZE`):

```python
# Sort allowlist (webui spec §3.1): a query-param key maps to a FIXED ORDER BY expression; no
# param value is ever interpolated into SQL. ``tier`` sorts by the file's strongest tier rank
# (``dec.best_tier_rank``, MAX of the TIER_RANK CASE). Direction maps through a fixed set too.
SORT_COLUMNS: dict[str, str] = {
    "name": "obs.filename",
    "size": "f.size_bytes",
    "sources": "obs.source_count",
    "last_seen": "obs.observed_at",
    "tier": "dec.best_tier_rank",
}
SORT_DIRECTIONS: dict[str, str] = {"asc": "ASC", "desc": "DESC"}
DEFAULT_SORT = "last_seen"
DEFAULT_DIR = "desc"
```

Add the two params to `list_files` and use the allowlist for the ORDER BY (replacing the fixed
`ORDER BY obs.observed_at DESC, f.ed2k_hash`):

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
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIR,
    ) -> list[FileRow]:
        ...
        # ORDER BY from the allowlist (spec §3.1): unknown sort/direction fall back to the
        # default; the ed2k_hash tiebreak keeps paging stable. Both operands come from fixed
        # maps, never from the raw param, so the f-string is injection-safe.
        column_expr = SORT_COLUMNS.get(sort, SORT_COLUMNS[DEFAULT_SORT])
        dir_sql = SORT_DIRECTIONS.get(direction, SORT_DIRECTIONS[DEFAULT_DIR])
        sql += f"ORDER BY {column_expr} {dir_sql}, f.ed2k_hash\n"
        sql += "LIMIT ? OFFSET ?\n"
```

Extend the `list_files` docstring with the `sort`/`direction` params (allowlist keys, default
`last_seen`/`desc`, SQLite sorts NULLs first in ASC so NULL filename/observed_at cluster
predictably).

- [ ] **Step 4: Run the tests, watch them pass; run the full package gate**

```bash
( cd packages/crawler && uv run pytest tests/webui/test_webui_catalog_read.py --no-cov -q )
( cd packages/crawler && uv run pytest )   # 100% branch coverage over the whole package
```
Expected: PASS, coverage 100%.

- [ ] **Step 5: Commit**

```bash
git add packages/crawler/src/mulewatch/webui/adapters/catalog_read.py \
        packages/crawler/tests/webui/test_webui_catalog_read.py
git commit -m "feat(webui): sortable list_files via a column allowlist + best_tier_rank"
```

---

### Task 2: Read layer — `tier_counts` facet read

**Files:**
- Modify: `packages/crawler/src/mulewatch/webui/adapters/catalog_read.py`
- Test: `packages/crawler/tests/webui/test_webui_catalog_read.py`

**Interfaces:**
- Consumes: the `_SQL_CTES` (with `latest_dec`, `latest_obs`, `latest_ver`), `_filter_clauses`.
- Produces (used by Task 4): `tier_counts(*, target, verdict, query) -> dict[str, int]` — one
  entry per tier present, `{tier: file_count}`, `{}` on an empty/no-decision catalogue.

- [ ] **Step 1: Write the failing tests** (append to `test_webui_catalog_read.py`)

```python
def _seed_mixed_tier_file(db: Path) -> None:
    """One file with TWO current decisions in DIFFERENT tiers (download + notify): it must count
    under both facets."""
    h = "d" * 32
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 100))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (h, "mixed.avi", 100, 1, 0, "[]", "keroro", "2026-01-01T10:00:00.000000+00:00", "n"),
        )
        for tid, tier in (("062A", "download"), ("062B", "notify")):
            conn.execute(
                "INSERT INTO match_decisions"
                " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (h, tid, "rule", tier, "2026-01-02T10:00:00.000000+00:00", "n"),
            )
        conn.commit()


def test_tier_counts_groups_by_tier(catalog_db: Path) -> None:
    _seed_sortable(catalog_db)  # one file per tier
    counts = CatalogReader(open_reader(catalog_db)).tier_counts(
        target=None, verdict=None, query=None
    )
    assert counts == {"download": 1, "notify": 1, "catalog": 1}


def test_tier_counts_empty_catalogue_is_empty(catalog_db: Path) -> None:
    counts = CatalogReader(open_reader(catalog_db)).tier_counts(
        target=None, verdict=None, query=None
    )
    assert counts == {}


def test_tier_counts_multi_tier_file_counts_in_both(catalog_db: Path) -> None:
    _seed_mixed_tier_file(catalog_db)
    counts = CatalogReader(open_reader(catalog_db)).tier_counts(
        target=None, verdict=None, query=None
    )
    assert counts == {"download": 1, "notify": 1}


def test_tier_counts_respects_query_filter(catalog_db: Path) -> None:
    """The facet honours the OTHER filters (here ``query``): only alpha.avi (download) matches."""
    _seed_sortable(catalog_db)
    counts = CatalogReader(open_reader(catalog_db)).tier_counts(
        target=None, verdict=None, query="alpha"
    )
    assert counts == {"download": 1}
```

- [ ] **Step 2: Run the tests, watch them fail**

```bash
( cd packages/crawler && uv run pytest tests/webui/test_webui_catalog_read.py -k tier_counts --no-cov -q )
```
Expected: FAIL (`AttributeError: 'CatalogReader' object has no attribute 'tier_counts'`).

- [ ] **Step 3: Implement `tier_counts` in `catalog_read.py`**

Add the SQL base (after `_SQL_COUNT_FILES_BASE`):

```python
# Tier facet counts (webui spec §3.3): one row per tier, ``COUNT(DISTINCT ed2k_hash)`` files that
# have at least one CURRENT decision of that tier. Grouped over ``latest_dec`` (per-decision), so
# a multi-tier file counts once under each of its tiers. The join to ``latest_obs``/``latest_ver``
# is only there for the ``query``/``verdict`` filter clauses. The tier filter itself is NEVER
# applied here (a facet shows the count you would get by choosing each option).
_SQL_TIER_COUNTS_BASE = (
    _SQL_CTES
    + """\
SELECT ld.tier AS tier, COUNT(DISTINCT ld.ed2k_hash) AS n
FROM latest_dec AS ld
JOIN files AS f ON f.ed2k_hash = ld.ed2k_hash
LEFT JOIN latest_obs AS obs ON obs.ed2k_hash = ld.ed2k_hash
LEFT JOIN latest_ver AS ver ON ver.ed2k_hash = ld.ed2k_hash
"""
)
```

Add the method (after `count_files`):

```python
    def tier_counts(
        self,
        *,
        target: str | None,
        verdict: str | None,
        query: str | None,
    ) -> dict[str, int]:
        """Return ``{tier: file_count}`` for the tier facet, applying the ``target``/``verdict``/
        ``query`` filters but NEVER a tier filter (facet-lite: each option shows the count you
        would get by choosing it). A file counts under every tier it currently holds a decision
        in (a multi-tier file appears in two facets). ``{}`` on an empty/undecided catalogue.
        """
        clauses, params = _filter_clauses(target, None, verdict, query)
        sql = _SQL_TIER_COUNTS_BASE
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + "\n"
        sql += "GROUP BY ld.tier\n"
        rows = self._conn.execute(sql, params).fetchall()
        return {row["tier"]: row["n"] for row in rows}
```

- [ ] **Step 4: Run the tests, watch them pass; run the full package gate**

```bash
( cd packages/crawler && uv run pytest tests/webui/test_webui_catalog_read.py -k tier_counts --no-cov -q )
( cd packages/crawler && uv run pytest )
```
Expected: PASS, coverage 100%.

- [ ] **Step 5: Commit**

```bash
git add packages/crawler/src/mulewatch/webui/adapters/catalog_read.py \
        packages/crawler/tests/webui/test_webui_catalog_read.py
git commit -m "feat(webui): tier_counts facet read for the /files tier filter"
```

---

### Task 3: UI — bidirectional column sorting (view-models + handler + thead + CSS)

**Files:**
- Modify: `packages/crawler/src/mulewatch/webui/domain/views.py` (add `SortHeader`, `SortHeaders`)
- Modify: `packages/crawler/src/mulewatch/webui/composition/app.py` (normalize + precompute +
  wire `handle_files`; `handle_target` passes `headers=()`)
- Modify: `packages/crawler/src/mulewatch/webui/adapters/templates/files.html` (sortable `<thead>`)
- Modify: `packages/crawler/src/mulewatch/webui/adapters/static/app.css` (sort indicator)
- Test: `packages/crawler/tests/webui/test_webui_app.py`

**Interfaces:**
- Consumes (Task 1): `SORT_COLUMNS`, `SORT_DIRECTIONS`, `DEFAULT_SORT`, `DEFAULT_DIR`,
  `list_files(..., sort=, direction=)`.
- Produces (Task 4 builds on the same `filters`/`sort_dir` state dicts inside `handle_files`).

**Design notes (read before writing):**
- The handler builds two ordered dicts of active, non-default params (page excluded):
  `filters` (insertion order: `target`, `tier`, `verdict`, `q`, `show_unmatched`) and `sort_dir`
  (`sort`, `dir`, each present only when non-default). Every URL derives from these, so param
  order is deterministic and testable.
- A sort header link keeps ALL `filters` but OVERRIDES sort/dir for its column. The active
  column flips direction and shows an indicator; an inactive column uses a per-column default
  direction (Name asc, everything else desc) and no indicator.
- `indicator` is `""`/`"asc"`/`"desc"` (spec §3.1). The template renders it as `data-sort="..."`;
  CSS draws the arrow. No glyph in the view-model or the HTML body.
- The `<thead>` is fixed (9 columns). Each sortable cell wraps its label in
  `{% for h in headers %}<a ...>Label</a>{% else %}Label{% endfor %}` where `headers` is a
  0-or-1-element tuple: on `/files` it is `(SortHeaders(...),)` (renders the link), on
  `/targets/{id}` it is `()` (renders the plain label). Same pattern as `summaries`.

- [ ] **Step 1: Write the failing tests** (append to `test_webui_app.py`)

Direct builder tests + handler/render tests. Import the builders:

```python
from mulewatch.webui.composition.app import (
    _normalize_dir,
    _normalize_sort,
    _sort_headers,
)
from mulewatch.webui.domain.views import SortHeader, SortHeaders
```

```python
def test_normalize_sort_valid_unknown_missing() -> None:
    assert _normalize_sort("size") == "size"
    assert _normalize_sort("bogus") == "last_seen"
    assert _normalize_sort(None) == "last_seen"


def test_normalize_dir_valid_unknown_missing() -> None:
    assert _normalize_dir("asc") == "asc"
    assert _normalize_dir("bogus") == "desc"
    assert _normalize_dir(None) == "desc"


def test_sort_headers_default_state_no_filters() -> None:
    headers = _sort_headers(sort="last_seen", direction="desc", filters={})
    # active column: last_seen, showing desc; its link flips to asc. sort=last_seen is the
    # DEFAULT sort so it is OMITTED from the URL (the omit-defaults invariant); only dir=asc shows.
    assert headers.last_seen.indicator == "desc"
    assert headers.last_seen.url == "/files?dir=asc"
    # inactive columns: no indicator, per-column default direction, sort/dir omitted when default
    assert headers.name.indicator == ""
    assert headers.name.url == "/files?sort=name&dir=asc"   # Name default dir is asc
    assert headers.size.url == "/files?sort=size"           # Size default dir desc == DEFAULT_DIR → omitted
    assert headers.tier.url == "/files?sort=tier"


def test_sort_headers_active_column_flips_and_preserves_filters() -> None:
    headers = _sort_headers(sort="size", direction="asc", filters={"q": "keroro", "tier": "notify"})
    # active column size, currently asc → indicator asc, link flips to desc (== default → omitted)
    assert headers.size.indicator == "asc"
    assert headers.size.url == "/files?q=keroro&tier=notify&sort=size"
    # an inactive column keeps the filters and sets its own sort + default dir
    assert headers.name.indicator == ""
    assert headers.name.url == "/files?q=keroro&tier=notify&sort=name&dir=asc"


@pytest.mark.asyncio
async def test_files_sort_by_size_orders_rows(sortable_app: tuple[Starlette, list[str]]) -> None:
    app, ordered_desc = sortable_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?sort=size&dir=asc")
    assert resp.status_code == 200
    # smallest first: the three short-hashes appear in ascending-size order in the body
    positions = [resp.text.index(h[:8]) for h in reversed(ordered_desc)]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_files_renders_sortable_header_link(populated_app: tuple[Starlette, str]) -> None:
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    # the Name header is a sort link with a data-sort attribute (default state → not active)
    assert 'href="/files?sort=name&amp;dir=asc"' in resp.text
    assert 'data-sort="desc"' in resp.text  # last_seen is the active default column


@pytest.mark.asyncio
async def test_target_page_headers_are_plain_text(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200
    # no sort links on a target page (headers tuple is empty → the {% else %} plain label renders)
    assert "?sort=" not in resp.text
```

Add a `sortable_app` fixture (mirrors `populated_app` but seeds three files with distinct sizes;
returns the app and the hashes in descending-size order):

```python
@pytest.fixture
def sortable_app(catalog_db: Path, local_db: Path) -> tuple[Starlette, list[str]]:
    """App over three matched files with distinct sizes (300/200/100), for the sort-order test.
    Returns (app, hashes-in-descending-size-order)."""
    big, mid, small = "a" * 32, "b" * 32, "c" * 32
    with sqlite3.connect(catalog_db) as conn:
        for h, size, name in ((big, 300, "x.avi"), (mid, 200, "y.avi"), (small, 100, "z.avi")):
            conn.execute("INSERT INTO files VALUES (?, ?, ?)", (h, size, None))
            conn.execute(
                "INSERT INTO file_observations VALUES"
                " (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (h, name, size, 1, 0, None, None, None, None, "{}", "keroro",
                 "2024-01-01T00:00:00", "n"),
            )
            conn.execute(
                "INSERT INTO match_decisions VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (h, "062A", "rule", "download", "2024-01-01T00:00:00", "n"),
            )
        conn.commit()
    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=_matcher(),
        targets=_targets(),
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
    )
    return app, [big, mid, small]
```

- [ ] **Step 2: Run the tests, watch them fail**

```bash
( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py -k "sort or header or normalize" --no-cov -q )
```
Expected: FAIL (ImportError on the builders / `SortHeader`; then render assertions fail).

- [ ] **Step 3: Add the view-models to `domain/views.py`**

```python
@dataclass(frozen=True)
class SortHeader:
    """A sortable column header, fully precomputed (W-D8). ``url`` re-sorts by this column
    (flipping direction when it is the active one, else a per-column default direction),
    preserving every active filter; ``indicator`` is ``""``, ``"asc"``, or ``"desc"`` (the
    template renders it as ``data-sort`` and CSS draws the arrow)."""

    label: str
    url: str
    indicator: str  # "" | "asc" | "desc"


@dataclass(frozen=True)
class SortHeaders:
    """The five sortable headers of the /files table, one attribute per column so the fixed
    thead can interpolate ``{{ headers.name.url }}`` etc. with no template logic (W-D8)."""

    name: SortHeader
    size: SortHeader
    sources: SortHeader
    last_seen: SortHeader
    tier: SortHeader
```

- [ ] **Step 4: Add the constants + builders to `composition/app.py`**

Import the read-layer allowlist and add the UI-side maps:

```python
from mulewatch.webui.adapters.catalog_read import (
    DEFAULT_DIR,
    DEFAULT_SORT,
    PAGE_SIZE,
    SORT_COLUMNS,
    SORT_DIRECTIONS,
    CatalogReader,
)
from mulewatch.webui.domain.views import (
    ...,
    SortHeader,
    SortHeaders,
)
```

```python
# Sortable columns in display order: allowlist key → (header label, default direction when this
# column is NOT the active sort). Name reads best ascending; the metrics and last-seen read best
# descending (webui spec §3.1).
_SORT_LABELS: tuple[tuple[str, str], ...] = (
    ("name", "Name"),
    ("size", "Size"),
    ("sources", "Sources"),
    ("last_seen", "Last seen"),
    ("tier", "Tier"),
)
_COLUMN_DEFAULT_DIR: dict[str, str] = {
    "name": "asc",
    "size": "desc",
    "sources": "desc",
    "last_seen": "desc",
    "tier": "desc",
}
_FLIP: dict[str, str] = {"asc": "desc", "desc": "asc"}


def _normalize_sort(raw: str | None) -> str:
    """Map a raw ``sort`` param to an allowlist key, or the default (unknown/missing → default)."""
    return raw if raw in SORT_COLUMNS else DEFAULT_SORT


def _normalize_dir(raw: str | None) -> str:
    """Map a raw ``dir`` param to ``asc``/``desc``, or the default (unknown/missing → default)."""
    return raw if raw in SORT_DIRECTIONS else DEFAULT_DIR


def _sort_header(col: str, label: str, sort: str, direction: str, filters: dict[str, str]) -> SortHeader:
    """Build one column header. Keeps every ``filters`` param and OVERRIDES sort/dir: the active
    column flips direction and shows its indicator; an inactive column uses its default direction
    and no indicator. Params equal to the default are omitted from the URL (clean, deterministic)."""
    active = col == sort
    next_dir = _FLIP[direction] if active else _COLUMN_DEFAULT_DIR[col]
    indicator = direction if active else ""
    params = dict(filters)
    if col != DEFAULT_SORT:
        params["sort"] = col
    if next_dir != DEFAULT_DIR:
        params["dir"] = next_dir
    url = "/files?" + urlencode(params) if params else "/files"
    return SortHeader(label=label, url=url, indicator=indicator)


def _sort_headers(*, sort: str, direction: str, filters: dict[str, str]) -> SortHeaders:
    """Precompute all five sortable headers (W-D8)."""
    built = {
        col: _sort_header(col, label, sort, direction, filters) for col, label in _SORT_LABELS
    }
    return SortHeaders(
        name=built["name"],
        size=built["size"],
        sources=built["sources"],
        last_seen=built["last_seen"],
        tier=built["tier"],
    )
```

- [ ] **Step 5: Wire `handle_files` (and `handle_target`)**

In `handle_files`, after the existing param reads, add sort/dir normalization and build the state
dicts; pass sort/direction to `list_files`; thread `sort_dir` through nav + summary; add
`headers` to the context:

```python
        sort_param = _normalize_sort(request.query_params.get("sort"))
        dir_param = _normalize_dir(request.query_params.get("dir"))
        ...
        file_rows = catalog.list_files(
            target=target_param,
            tier=tier_param,
            verdict=verdict_param,
            query=query_param,
            page=page,
            matched_only=not show_unmatched,
            sort=sort_param,
            direction=dir_param,
        )
        ...
        # Active, non-default params (page excluded). Every URL derives from these two ordered
        # dicts, so param order is deterministic. ``filters`` is what a re-sort keeps; ``sort_dir``
        # is what a filter/toggle/page keeps.
        filters: dict[str, str] = {}
        if target_param is not None:
            filters["target"] = target_param
        if tier_param is not None:
            filters["tier"] = tier_param
        if verdict_param is not None:
            filters["verdict"] = verdict_param
        if query_param is not None:
            filters["q"] = query_param
        if show_unmatched:
            filters["show_unmatched"] = "1"
        sort_dir: dict[str, str] = {}
        if sort_param != DEFAULT_SORT:
            sort_dir["sort"] = sort_param
        if dir_param != DEFAULT_DIR:
            sort_dir["dir"] = dir_param

        headers = _sort_headers(sort=sort_param, direction=dir_param, filters=filters)

        # summary toggle preserves sort/dir (but not show_unmatched, which it manages itself)
        summary_base = {k: v for k, v in filters.items() if k != "show_unmatched"}
        summary_base.update(sort_dir)
        summary = _build_summary(matched, total, show_unmatched, summary_base)

        nav = _page_nav(page, len(display_rows), "/files", {**filters, **sort_dir})
        return templates.TemplateResponse(
            request,
            "files.html",
            {"rows": display_rows, "nav": nav, "summaries": (summary,), "headers": (headers,)},
        )
```

Remove the now-dead `filter_query`/`nav_query` blocks they replace. In `handle_target`, add
`"headers": ()` to the context dict.

- [ ] **Step 6: Update the `<thead>` in `files.html`**

Wrap each sortable label (Name, Size, Sources, Last seen, Tier) in the 0-or-1-tuple `for`. Example
for Name (no tooltip) and Sources (with tooltip); apply the same to Size, Last seen, Tier:

```html
      <th>{% for h in headers %}<a href="{{ h.name.url }}" data-sort="{{ h.name.indicator }}">Name</a>{% else %}Name{% endfor %}</th>
      ...
      <th class="th-tip-host">{% for h in headers %}<a href="{{ h.sources.url }}" data-sort="{{ h.sources.indicator }}">Sources</a>{% else %}Sources{% endfor %}
        <button type="button" class="th-help" ...>?</button>
        <div id="tip-sources" ...>...</div>
      </th>
```

Hash, Target, Title, Verdict stay plain. Do NOT introduce any `{% if %}`, filter, or call.

- [ ] **Step 7: Add the sort indicator to `app.css`**

```css
.files-table thead a[data-sort="asc"]::after { content: " \2191"; }   /* up arrow */
.files-table thead a[data-sort="desc"]::after { content: " \2193"; }  /* down arrow */
.files-table thead a { text-decoration: none; }
```

- [ ] **Step 8: Run the tests + template guard + full gate**

```bash
( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py -k "sort or header or normalize" --no-cov -q )
uv run poe template-check
( cd packages/crawler && uv run pytest )
```
Expected: PASS; template-check green (no logic); coverage 100%.

- [ ] **Step 9: Commit**

```bash
git add packages/crawler/src/mulewatch/webui/domain/views.py \
        packages/crawler/src/mulewatch/webui/composition/app.py \
        packages/crawler/src/mulewatch/webui/adapters/templates/files.html \
        packages/crawler/src/mulewatch/webui/adapters/static/app.css \
        packages/crawler/tests/webui/test_webui_app.py
git commit -m "feat(webui): bidirectional column sorting on /files"
```

---

### Task 4: UI — filename search + tier facet (view-models + handler + filter bar + CSS)

**Files:**
- Modify: `packages/crawler/src/mulewatch/webui/domain/views.py` (`TierFacet`, `HiddenInput`,
  `SearchBar`, `FilterBar`)
- Modify: `packages/crawler/src/mulewatch/webui/composition/app.py` (`_tier_facets`,
  `_search_bar`; call `tier_counts`; build `FilterBar`; `handle_target` passes `filter_bar=()`)
- Modify: `packages/crawler/src/mulewatch/webui/adapters/templates/files.html` (filter bar)
- Modify: `packages/crawler/src/mulewatch/webui/adapters/static/app.css` (bar + chips)
- Test: `packages/crawler/tests/webui/test_webui_app.py`

**Interfaces:**
- Consumes (Task 2): `CatalogReader.tier_counts`. Consumes (Task 3): the `filters`/`sort_dir`
  state dicts inside `handle_files`.

**Design notes:**
- `filter_bar` is a 0-or-1-element tuple (like `headers`): `(FilterBar(...),)` on `/files`, `()`
  on `/targets/{id}`. It holds a `SearchBar` (the `q` prefill + hidden inputs) and a tuple of
  `TierFacet`.
- Search form is `GET action="/files"`. Hidden inputs carry every active param EXCEPT `q` and
  `page` (so submitting a search preserves target/tier/verdict/sort/dir/show_unmatched and resets
  page). The tier facet links carry every active param EXCEPT `tier` and `page` (selecting a tier
  replaces it and resets page); the "all" reset link clears `tier`.
- Facet order is fixed: `download`, `notify`, `catalog` (only tiers present in the counts appear),
  preceded by an "all" reset. `catalog` is masked to `"unidentified"` (same rule the row
  rendering uses). The "all" option has no count; each tier shows `(<count>)`.

- [ ] **Step 1: Write the failing tests** (append to `test_webui_app.py`)

```python
from mulewatch.webui.composition.app import _search_bar, _tier_facets
from mulewatch.webui.domain.views import FilterBar, HiddenInput, SearchBar, TierFacet


def test_tier_facets_all_reset_and_masks_catalog() -> None:
    facets = _tier_facets(
        counts={"download": 1, "notify": 2, "catalog": 5}, active_tier=None, base={}
    )
    assert facets[0].label == "all"
    assert facets[0].selected_flag == "1"           # no tier active → all selected
    assert facets[0].url == "/files"
    labels = [f.label for f in facets[1:]]
    assert labels == ["download", "notify", "unidentified"]   # fixed order, catalog masked
    assert facets[1].count_display == "(1)"
    assert facets[1].url == "/files?tier=download"


def test_tier_facets_selected_and_preserves_base() -> None:
    facets = _tier_facets(
        counts={"download": 1, "notify": 2},
        active_tier="notify",
        base={"q": "keroro", "sort": "size"},
    )
    notify = next(f for f in facets if f.label == "notify")
    assert notify.selected_flag == "1"
    assert notify.url == "/files?q=keroro&sort=size&tier=notify"
    assert facets[0].url == "/files?q=keroro&sort=size"   # all-reset keeps base, drops tier


def test_tier_facets_only_present_tiers_appear() -> None:
    facets = _tier_facets(counts={"notify": 3}, active_tier=None, base={})
    assert [f.label for f in facets] == ["all", "notify"]


def test_search_bar_prefill_and_hidden_excludes_q() -> None:
    bar = _search_bar(query="keroro", hidden_state={"tier": "notify", "sort": "size"})
    assert bar.query == "keroro"
    assert bar.hidden == (
        HiddenInput(name="tier", value="notify"),
        HiddenInput(name="sort", value="size"),
    )


def test_search_bar_no_query_is_empty_string() -> None:
    bar = _search_bar(query=None, hidden_state={})
    assert bar.query == ""
    assert bar.hidden == ()


@pytest.mark.asyncio
async def test_files_renders_search_prefill_and_facet(populated_app: tuple[Starlette, str]) -> None:
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?q=keroro&show_unmatched=1")
    assert 'name="q"' in resp.text
    assert 'value="keroro"' in resp.text                       # prefill
    assert 'name="show_unmatched"' in resp.text                # carried as a hidden input
    # populated_app's single file is a catalog-tier decision → facet shows "unidentified (1)"
    assert "unidentified" in resp.text
    assert 'href="/files?q=keroro&amp;show_unmatched=1&amp;tier=catalog"' in resp.text


@pytest.mark.asyncio
async def test_target_page_has_no_filter_bar(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert 'name="q"' not in resp.text          # no search form on a target page
    assert "tier-facet" not in resp.text        # no facet on a target page
```

- [ ] **Step 2: Run the tests, watch them fail**

```bash
( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py -k "facet or search_bar or filter_bar" --no-cov -q )
```
Expected: FAIL (ImportError on `_tier_facets`/`_search_bar`/`TierFacet`).

- [ ] **Step 3: Add the view-models to `domain/views.py`**

```python
@dataclass(frozen=True)
class TierFacet:
    """One tier-filter option with its live count, precomputed (W-D8). ``label`` is the display
    tier (``catalog`` masked to ``"unidentified"``, or the literal ``"all"`` reset);
    ``count_display`` is ``"(<n>)"`` for a tier and ``""`` for the reset; ``url`` selects (or, for
    the reset, clears) this tier while preserving other params and resetting page;
    ``selected_flag`` is ``"1"`` or ``""`` (rendered as ``data-selected`` for CSS)."""

    label: str
    count_display: str
    url: str
    selected_flag: str


@dataclass(frozen=True)
class HiddenInput:
    """One hidden form field carried by the search GET form (name/value already stringified)."""

    name: str
    value: str


@dataclass(frozen=True)
class SearchBar:
    """The filename search form, precomputed (W-D8): ``query`` prefills the text input, ``hidden``
    carries every active param except ``q`` and ``page`` so submitting a search preserves them."""

    query: str
    hidden: tuple[HiddenInput, ...]


@dataclass(frozen=True)
class FilterBar:
    """The /files filter bar: the search form + the tier facet. Passed as a 0-or-1-element tuple
    so ``handle_target`` can reuse ``files.html`` with an empty bar (like ``summaries``)."""

    searchbar: SearchBar
    facets: tuple[TierFacet, ...]
```

- [ ] **Step 4: Add the builders to `composition/app.py`**

```python
# Tier facet display order (strongest first) and the catalog→"unidentified" mask, matching the
# row rendering. Only tiers PRESENT in the counts are rendered.
_FACET_TIER_ORDER: tuple[str, ...] = ("download", "notify", "catalog")


def _facet_label(tier: str) -> str:
    """Display label for a tier facet: ``catalog`` is masked to ``"unidentified"`` (the
    keroro_large catch-all), every other tier shows its own name."""
    return "unidentified" if tier == "catalog" else tier


def _tier_facets(
    *, counts: Mapping[str, int], active_tier: str | None, base: dict[str, str]
) -> tuple[TierFacet, ...]:
    """Precompute the tier facet (W-D8): an "all" reset (no count) followed by one entry per tier
    present, in ``_FACET_TIER_ORDER``. ``base`` is the params to preserve (filters minus ``tier``,
    plus sort/dir; page already excluded); a tier entry appends ``tier=<t>``, the reset omits it."""
    all_url = "/files?" + urlencode(base) if base else "/files"
    facets = [
        TierFacet(
            label="all",
            count_display="",
            url=all_url,
            selected_flag="1" if active_tier is None else "",
        )
    ]
    for tier in _FACET_TIER_ORDER:
        if tier not in counts:
            continue
        params = {**base, "tier": tier}
        facets.append(
            TierFacet(
                label=_facet_label(tier),
                count_display=f"({counts[tier]})",
                url="/files?" + urlencode(params),
                selected_flag="1" if active_tier == tier else "",
            )
        )
    return tuple(facets)


def _search_bar(*, query: str | None, hidden_state: dict[str, str]) -> SearchBar:
    """Precompute the search form (W-D8): the ``q`` prefill (empty string when none) + hidden
    inputs from ``hidden_state`` (already excludes ``q`` and ``page``)."""
    hidden = tuple(HiddenInput(name=k, value=v) for k, v in hidden_state.items())
    return SearchBar(query=query or "", hidden=hidden)
```

- [ ] **Step 5: Wire `handle_files` (and `handle_target`)**

In `handle_files`, after the `sort_dir` dict is built (Task 3), call `tier_counts` and build the
`FilterBar`; add it to the context:

```python
        tier_count_map = catalog.tier_counts(
            target=target_param, verdict=verdict_param, query=query_param
        )
        facet_base = {k: v for k, v in filters.items() if k != "tier"}
        facet_base.update(sort_dir)
        facets = _tier_facets(counts=tier_count_map, active_tier=tier_param, base=facet_base)

        hidden_state = {k: v for k, v in filters.items() if k != "q"}
        hidden_state.update(sort_dir)
        searchbar = _search_bar(query=query_param, hidden_state=hidden_state)

        filter_bar = FilterBar(searchbar=searchbar, facets=facets)
        return templates.TemplateResponse(
            request,
            "files.html",
            {
                "rows": display_rows,
                "nav": nav,
                "summaries": (summary,),
                "headers": (headers,),
                "filter_bar": (filter_bar,),
            },
        )
```

In `handle_target`, add `"filter_bar": ()` to the context dict.

Import the new names:

```python
from mulewatch.webui.domain.views import (
    ...,
    FilterBar,
    HiddenInput,
    SearchBar,
    TierFacet,
)
```

- [ ] **Step 6: Add the filter bar to `files.html`** (above `<div class="files-scroll">`)

```html
{% for fb in filter_bar %}
<form class="filter-bar" action="/files" method="get" role="search">
  <input type="search" name="q" value="{{ fb.searchbar.query }}" placeholder="Search filename">
  {% for h in fb.searchbar.hidden %}<input type="hidden" name="{{ h.name }}" value="{{ h.value }}">{% endfor %}
  <button type="submit">Search</button>
</form>
<nav class="tier-facet" aria-label="Filter by tier">
  {% for f in fb.facets %}<a href="{{ f.url }}" data-selected="{{ f.selected_flag }}">{{ f.label }} {{ f.count_display }}</a>{% endfor %}
</nav>
{% endfor %}
```

No `{% if %}`, no filter, no call. `{{ f.count_display }}` interpolates the precomputed `"(3)"`.

- [ ] **Step 7: Style the bar in `app.css`**

```css
.filter-bar { display: flex; gap: 0.5rem; align-items: center; margin: 0.75rem 0 0.25rem; }
.tier-facet { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.75rem; }
.tier-facet a { padding: 0.15rem 0.6rem; border: 1px solid currentColor; border-radius: 1rem; text-decoration: none; }
.tier-facet a[data-selected="1"] { font-weight: 600; }
```

- [ ] **Step 8: Run the tests + template guard + full gate**

```bash
( cd packages/crawler && uv run pytest tests/webui/test_webui_app.py -k "facet or search or filter" --no-cov -q )
uv run poe template-check
( cd packages/crawler && uv run pytest )
```
Expected: PASS; template-check green; coverage 100%.

- [ ] **Step 9: Commit**

```bash
git add packages/crawler/src/mulewatch/webui/domain/views.py \
        packages/crawler/src/mulewatch/webui/composition/app.py \
        packages/crawler/src/mulewatch/webui/adapters/templates/files.html \
        packages/crawler/src/mulewatch/webui/adapters/static/app.css \
        packages/crawler/tests/webui/test_webui_app.py
git commit -m "feat(webui): filename search + tier facet with live counts on /files"
```

---

## Self-review (run after the plan, before execution)

- **Spec coverage:** §3.1 sorting → Tasks 1+3; §3.2 search → Task 4; §3.3 tier facet + counts →
  Tasks 2+4; §3.4 filter bar → Tasks 3+4; §4 tests → each task's Step 1. Covered.
- **Type consistency:** `list_files(sort=, direction=)` (Task 1) ↔ handler passes `sort=sort_param,
  direction=dir_param` (Task 3). `SORT_COLUMNS`/`DEFAULT_SORT`/`DEFAULT_DIR`/`SORT_DIRECTIONS`
  exported by Task 1, imported by Task 3. `tier_counts(*, target, verdict, query)` (Task 2) ↔
  handler call (Task 4). View-model field names match template interpolations.
- **Placeholder scan:** none (SQL/`...unchanged...` markers reference existing verified code).
- **W-D8:** every branch/format precomputed; template uses only `{% for %}`/`{% else %}` and
  attribute interpolation; `data-sort`/`data-selected` + CSS carry the visual state.

## Not validated against real hardware

The CSS (filter bar, facet chips, sort arrows) and the whole-page layout are verified by eye on
the real node after merge (the "verify UI changes in the real container" habit), including the
wide-body layout from the 2026-07-09 files-layout work. Unit tests cover the read layer, the
builders, and the rendered HTML, but not the visual result.
