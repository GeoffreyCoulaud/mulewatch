"""TDD tests for the Starlette application (composition/app.py — Task 11)."""

import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from catalog_matching.models import TargetSegment
from mulewatch.webui.composition.app import _resolve_target_display, _to_display_rows, build_app
from mulewatch.webui.domain.views import FileDecision, FileRow

# ---------------------------------------------------------------------------
# Helpers YAML
# ---------------------------------------------------------------------------

TEST_HASH = "aabbccdd00112233aabbccdd00112233"


def _write_targets_yaml(path: Path) -> Path:
    (path / "targets.yaml").write_text(
        """\
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - letter: a
        title: "La Grenouille Cosmique"
""",
        encoding="utf-8",
    )
    return path / "targets.yaml"


def _write_matcher_yaml(path: Path) -> Path:
    (path / "matcher.yaml").write_text(
        """\
tokens:
  keroro:
    keyword: keroro
rules:
  - name: catalog
    tier: catalog
    any:
      - keroro
""",
        encoding="utf-8",
    )
    return path / "matcher.yaml"


# ---------------------------------------------------------------------------
# Fixture: app with data
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_app(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """Insert test data and build the Starlette app."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-test-001"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.execute(
            "INSERT INTO scheduler_state VALUES (?, ?)",
            ("last_search_cycle", "2024-01-01T00:00:00"),
        )
        conn.commit()

    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.fixture
def app_no_decision(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """File without a match decision (decision=None branch)."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        # No decision
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-no-dec"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.fixture
def app_retracted_decision(
    catalog_db: Path, local_db: Path, tmp_path: Path
) -> tuple[Starlette, str]:
    """File whose LATEST decision for target 062A is a per-target retraction sentinel
    (``target_id="062A", rule_name="", tier="retracted"``) — must be treated exactly like
    ``app_no_decision`` (unmatched), never like a real decision."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        # First a real decision (was matched)...
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "high_confidence", "download", "2024-01-01T00:00:00", "node1"),
        )
        # ...then the per-target retraction sentinel, now the LATEST row for 062A.
        conn.execute(
            "INSERT INTO match_decisions VALUES (2, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "", "retracted", "2024-01-02T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-retracted"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.fixture
def app_no_observations(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """File without observations (last_obs=None branch → link='')."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        # No observations
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-no-obs"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.fixture
def app_unknown_target(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """File with a decision for a target_id unknown to the current config."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        # target_id unknown to the current YAML config
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "S9E999Z", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-unk"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.fixture
def app_download_tier_known_target(
    catalog_db: Path, local_db: Path, tmp_path: Path
) -> tuple[Starlette, str]:
    """A non-catalog decision (tier=download) on a target_id resolvable in the current
    targets.yaml — Task 3 resolution rule: the "resolvable id" case."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "high_confidence", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-dl"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.fixture
def app_download_tier_unknown_target(
    catalog_db: Path, local_db: Path, tmp_path: Path
) -> tuple[Starlette, str]:
    """A non-catalog decision (tier=download) on a target_id NOT in the current
    targets.yaml — Task 3 resolution rule: the "unknown id" case."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s9e999z_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "999Z", "high_confidence", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-dl-unk"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_200(populated_app: tuple[Starlette, str]) -> None:
    """/health → 200 {"status": "ok"}."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_dashboard_returns_200_with_target_id(
    populated_app: tuple[Starlette, str],
) -> None:
    """/ → 200 + contains 062A in the page."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "062A" in resp.text


@pytest.mark.asyncio
async def test_files_returns_200_with_file_row(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files → 200 + contains the inserted file's hash."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text


@pytest.mark.asyncio
async def test_files_filtered_verdict_returns_200_empty(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files?verdict=malicious → 200 (no results, no error)."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?verdict=malicious")
    assert resp.status_code == 200


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
    assert "Showing matched files only: 0 of 1 catalogued." in resp.text
    assert "Show all catalogued files" in resp.text
    assert "show_unmatched=1" in resp.text


@pytest.mark.asyncio
async def test_files_default_hides_retracted(
    app_retracted_decision: tuple[Starlette, str],
) -> None:
    """A retracted latest decision is treated exactly like no decision: the matched-only
    default (/files) hides it and counts it as unmatched in the summary."""
    app, hash_ = app_retracted_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] not in resp.text
    assert "Showing matched files only: 0 of 1 catalogued." in resp.text


@pytest.mark.asyncio
async def test_files_retracted_shows_as_unmatched_in_all_view(
    app_retracted_decision: tuple[Starlette, str],
) -> None:
    """The all-view (show_unmatched=1) renders a retracted file as an unmatched row: "·"
    cells, never "<td>unidentified</td>", never a tier/verdict badge (e.g. never the literal
    "retracted" or "pending" string in a cell)."""
    app, hash_ = app_retracted_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?show_unmatched=1")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert "<td>unidentified</td>" not in resp.text
    assert "<td>pending</td>" not in resp.text
    assert "<td>retracted</td>" not in resp.text
    assert "La Grenouille Cosmique" not in resp.text


@pytest.mark.asyncio
async def test_files_toggle_preserves_active_filter(
    populated_app: tuple[Starlette, str],
) -> None:
    """The matched→all toggle must carry the active q filter (spec §5: filters preserved)."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?q=keroro")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text  # the matched file still shows under the q filter
    # Jinja2 autoescapes "&" to "&amp;" inside the href attribute.
    assert 'href="/files?q=keroro&amp;show_unmatched=1">Show all catalogued files' in resp.text


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
    assert "Showing all catalogued files: 1 catalogued (0 matched)." in resp.text
    assert "Matched only" in resp.text
    assert 'href="/files">Matched only' in resp.text  # toggle drops the param → bare /files


@pytest.mark.asyncio
async def test_file_detail_with_decision_returns_200(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} with a decision → 200, contains the ed2k link + explanation info."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text
    assert "062A" in resp.text


@pytest.mark.asyncio
async def test_file_detail_without_decision_returns_200(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """/files/{hash} without a decision → 200, empty branches rendered."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text
    assert "No matching decision." in resp.text
    assert "No explanation available." in resp.text


@pytest.mark.asyncio
async def test_file_detail_retracted_shows_no_decision(
    app_retracted_decision: tuple[Starlette, str],
) -> None:
    """/files/{hash} whose LATEST decision is a retraction renders exactly like a file with
    no decision at all: "No matching decision.", never a tier/target/rule badge — and never
    the literal "retracted" string anywhere in the page."""
    app, hash_ = app_retracted_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "No matching decision." in resp.text
    assert "No explanation available." in resp.text
    assert "retracted" not in resp.text
    assert "062A" not in resp.text  # the earlier, pre-retraction decision must not leak through


@pytest.mark.asyncio
async def test_file_detail_explanation_none_unknown_target(
    app_unknown_target: tuple[Starlette, str],
) -> None:
    """/files/{hash} with a target_id unknown to the config → 200 (explanation=None)."""
    app, hash_ = app_unknown_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text


@pytest.mark.asyncio
async def test_file_detail_unknown_hash_returns_404(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} nonexistent (32 hex) → 404."""
    app, _ = populated_app
    unknown = "00000000000000000000000000000000"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{unknown}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_targets_shortcut_returns_200(
    populated_app: tuple[Starlette, str],
) -> None:
    """/targets/{target_id} → 200 (shortcut to filtered /files)."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_target_page_has_no_summary_line(
    populated_app: tuple[Starlette, str],
) -> None:
    """/targets/{id} shares files.html, but the matched/all summary is meaningless on a
    target-scoped page — the summary line and its toggle must not render there."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200
    assert "files-summary" not in resp.text
    assert "Showing matched files only" not in resp.text
    assert "Show all catalogued files" not in resp.text


@pytest.mark.asyncio
async def test_node_returns_200_with_node_info(
    populated_app: tuple[Starlette, str],
) -> None:
    """/node → 200 + contains the node id."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/node")
    assert resp.status_code == 200
    assert "node-test-001" in resp.text


@pytest.mark.asyncio
async def test_files_non_numeric_page_defaults_to_1(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files?page=abc → 200 (non-numeric page → default 1)."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?page=abc")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_file_detail_no_observations_returns_200(
    app_no_observations: tuple[Starlette, str],
) -> None:
    """/files/{hash} without observations → 200; no ed2k link, empty branch rendered."""
    app, hash_ = app_no_observations
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" not in resp.text
    assert "No observations." in resp.text


@pytest.mark.asyncio
async def test_base_nav_uses_singular_node_href(
    populated_app: tuple[Starlette, str],
) -> None:
    """The base nav contains href="/node" (singular) and NOT href="/nodes"."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert 'href="/node"' in resp.text
    assert 'href="/nodes"' not in resp.text


@pytest.mark.asyncio
async def test_node_page_renders_scheduler_state(
    populated_app: tuple[Starlette, str],
) -> None:
    """/node → the scheduler_state key last_search_cycle appears in the HTML."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/node")
    assert resp.status_code == 200
    assert "last_search_cycle" in resp.text


@pytest.fixture
def app_with_media_obs(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """File with an observation having non-null media_length_sec and bitrate_kbps."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                1320,  # media_length_sec = 22 minutes
                192,  # bitrate_kbps
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-media"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.mark.asyncio
async def test_file_detail_with_media_fields_returns_200(
    app_with_media_obs: tuple[Starlette, str],
) -> None:
    """/files/{hash} with media_length_sec + bitrate_kbps → 200, explanation computed."""
    app, hash_ = app_with_media_obs
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "062A" in resp.text


@pytest.mark.asyncio
async def test_file_detail_unknown_hash_returns_styled_404(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} nonexistent → 404 with the styled HTML template (contains 'not found')."""
    app, _ = populated_app
    unknown = "00000000000000000000000000000000"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{unknown}")
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


@pytest.fixture
def app_with_hostile_filename(
    catalog_db: Path, local_db: Path, tmp_path: Path
) -> tuple[Starlette, str]:
    """File whose name contains a ``|`` (eD2k link separator) — webui-security#0
    regression: without percent-encoding, the ``|`` in the name shifts size/hash and the
    link points elsewhere."""
    hostile = "weird|name.avi"
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files VALUES (?, ?, ?)", (TEST_HASH, 100_000_000, None))
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                hostile,
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.commit()
    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-hostile"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()
    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)
    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.mark.asyncio
async def test_empty_filter_param_does_not_silently_zero_results(
    populated_app: tuple[Starlette, str],
) -> None:
    # webui-security#0 regression (filters): ``?target=`` (empty, common with a <select>
    # that has an empty option) must be treated as "no filter", not as ``target = ''`` which
    # matches 0 results with no message.
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?target=&tier=&verdict=&q=")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text  # the inserted file is rendered despite the empty filters


@pytest.mark.asyncio
async def test_page_zero_is_clamped_to_first_page(
    populated_app: tuple[Starlette, str],
) -> None:
    # webui-security#2 regression: ``?page=0`` → OFFSET=-50 (SQLite returned page 1 by
    # luck). Now ``max(1, page)`` clamps.
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?page=0")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert "Page 1" in resp.text


@pytest.mark.asyncio
async def test_security_headers_are_set_on_every_response(
    populated_app: tuple[Starlette, str],
) -> None:
    # webui-security#3: CSP + X-Content-Type-Options + Referrer-Policy set by middleware.
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.headers["Content-Security-Policy"] == "default-src 'self'"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_files_page_shows_pagination_navigation(
    catalog_db: Path, local_db: Path, tmp_path: Path
) -> None:
    # webui-security#1: the page lists 50 files max; when it is FULL, a "Next →" link
    # must be rendered (heuristic: we don't have the total count).
    with sqlite3.connect(catalog_db) as conn:
        for i in range(50):
            ed2k = f"{i:032d}"
            conn.execute("INSERT INTO files VALUES (?, ?, ?)", (ed2k, 100, None))
            conn.execute(
                "INSERT INTO file_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i + 1,
                    ed2k,
                    f"file-{i}.bin",
                    100,
                    1,
                    1,
                    None,
                    None,
                    None,
                    None,
                    "{}",
                    "kw",
                    "2024-01-01T00:00:00",
                    "node1",
                ),
            )
            conn.execute(
                "INSERT INTO match_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (i + 1, ed2k, "062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
            )
        conn.commit()
    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-paged"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()
    targets_path = _write_targets_yaml(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)
    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp1 = await client.get("/files")
        resp2 = await client.get("/files?page=2")
    # page 1: full page → "Next" present, "Previous" absent.
    assert "Next" in resp1.text
    assert "Previous" not in resp1.text
    # page 2: not full (0 files) → "Previous" present, "Next" absent.
    assert "Previous" in resp2.text
    assert "Next" not in resp2.text


@pytest.mark.asyncio
async def test_hostile_filename_is_escaped_in_ed2k_link(
    app_with_hostile_filename: tuple[Starlette, str],
) -> None:
    # webui-security#0 regression: a hostile ``|`` in the name must be percent-encoded
    # (%7C). Without this escaping, the link would have 6 ``|`` (instead of 5 structural
    # separators) and size/hash would be shifted → unusable file / pointing elsewhere.
    app, hash_ = app_with_hostile_filename
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    # The link in the response must contain %7C (and NOT a raw ``|`` in the middle of the name).
    assert "%7C" in resp.text
    # We extract the first occurrence of ``ed2k://`` up to the next whitespace/`"` to
    # verify it has EXACTLY 5 ``|`` (the 5 structural separators of the canonical link).
    start = resp.text.index("ed2k://")
    end = start
    while end < len(resp.text) and resp.text[end] not in ('"', "<", " ", "\n"):
        end += 1
    link = resp.text[start:end]
    assert link.count("|") == 5


# ---------------------------------------------------------------------------
# Unit tests — _resolve_target_display / _to_display_rows (Task 3 resolution rule)
# ---------------------------------------------------------------------------

_SEGMENT_062A = TargetSegment(
    season=2, seasonal_number=11, absolute_number=62, segment="a", title="La Grenouille Cosmique"
)
_SEGMENT_062B = TargetSegment(
    season=2, seasonal_number=11, absolute_number=62, segment="b", title="Duel Contre Giroro"
)
_SEGMENT_BY_ID = {_SEGMENT_062A.target_id: _SEGMENT_062A}
_SEGMENTS_AB = {s.target_id: s for s in (_SEGMENT_062A, _SEGMENT_062B)}


def _file_row(*, decisions: tuple[FileDecision, ...], last_verdict: str | None = None) -> FileRow:
    return FileRow(
        ed2k_hash=TEST_HASH,
        size_bytes=1024,
        filename="f.avi",
        source_count=1,
        last_seen="2024-01-01T00:00:00",
        decisions=decisions,
        last_verdict=last_verdict,
    )


def test_resolve_target_display_empty_decisions_is_empty_list() -> None:
    assert _resolve_target_display(_file_row(decisions=()), _SEGMENT_BY_ID) == []


def test_resolve_target_display_catalog_decision_is_unidentified() -> None:
    row = _file_row(decisions=(FileDecision(target_id="062A", tier="catalog"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [("unidentified", "·")]


def test_resolve_target_display_resolvable_id_joins_seasonal_locator_and_title() -> None:
    row = _file_row(decisions=(FileDecision(target_id="062A", tier="download"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [
        ("062A / S02E11A", "La Grenouille Cosmique")
    ]


def test_resolve_target_display_unknown_id_falls_back_to_raw_id() -> None:
    row = _file_row(decisions=(FileDecision(target_id="999Z", tier="download"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [("999Z", "·")]


def test_resolve_target_display_two_segments_returns_a_pair_each() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"), FileDecision("062B", "download")))
    assert _resolve_target_display(row, _SEGMENTS_AB) == [
        ("062A / S02E11A", "La Grenouille Cosmique"),
        ("062B / S02E11B", "Duel Contre Giroro"),
    ]


def test_to_display_rows_empty_decisions_all_dashes() -> None:
    [display] = _to_display_rows([_file_row(decisions=())], _SEGMENTS_AB)
    assert display.target_display == "·"
    assert display.title_display == "·"
    assert display.tier_display == "·"
    assert display.verdict_display == "·"


def test_to_display_rows_two_segments_aggregate_cells_shared_tier() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"), FileDecision("062B", "download")))
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.target_display == "062A / S02E11A · 062B / S02E11B"
    assert display.title_display == "La Grenouille Cosmique · Duel Contre Giroro"
    assert display.tier_display == "download"


def test_to_display_rows_two_segments_differing_tiers_lists_per_target() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"), FileDecision("062B", "notify")))
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.tier_display == "062A: download · 062B: notify"


def test_to_display_rows_verdict_pending_when_decision_without_verdict() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"),))
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.verdict_display == "pending"


def test_to_display_rows_verdict_shows_actual_verdict() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"),), last_verdict="clean")
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.verdict_display == "clean"


def test_to_display_rows_computes_size_and_last_seen_display() -> None:
    row = FileRow(
        ed2k_hash=TEST_HASH,
        size_bytes=1024,
        filename="f.avi",
        source_count=1,
        last_seen="2026-07-03T23:45:24.104990+00:00",
        decisions=(),
        last_verdict=None,
    )
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.size_display == "1 KB"
    assert display.last_seen_display == "2026-07-03 23:45Z"


# ---------------------------------------------------------------------------
# HTTP-level tests — the resolution rule end-to-end through both routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_files_catalog_tier_shows_unidentified_and_pending(
    populated_app: tuple[Starlette, str],
) -> None:
    """populated_app's decision is tier=catalog with no verification row → the /files list
    must show "unidentified" (not the resolved id/title) and "pending" (not a real verdict
    or "·")."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    # Scoped to the table cell (not the static tier legend, which also mentions the word).
    assert "<td>unidentified</td>" in resp.text
    assert "pending" in resp.text
    assert "La Grenouille Cosmique" not in resp.text


@pytest.mark.asyncio
async def test_files_resolvable_target_shows_seasonal_locator_and_title(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert "062A / S02E11A" in resp.text
    assert "La Grenouille Cosmique" in resp.text


@pytest.mark.asyncio
async def test_files_unknown_target_shows_raw_id_and_dash_title(
    app_download_tier_unknown_target: tuple[Starlette, str],
) -> None:
    app, _ = app_download_tier_unknown_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert "999Z" in resp.text
    # The static tier legend also mentions the word "unidentified" — scope to the cell.
    assert "<td>unidentified</td>" not in resp.text
    assert "La Grenouille Cosmique" not in resp.text


@pytest.mark.asyncio
async def test_files_no_decision_shows_dashes(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """No decision at all → target/title/verdict all render as "·" cells, never a "pending"
    or "unidentified" cell value. The row is only visible with show_unmatched (the
    matched-only default hides it, cf. test_files_default_hides_unmatched)."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?show_unmatched=1")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    # The Verdict/Target header tooltips also mention "pending"/"unidentified" — scope to
    # the cell so we assert on the row value, not the static legend text.
    assert "<td>pending</td>" not in resp.text
    assert "<td>unidentified</td>" not in resp.text
    assert "La Grenouille Cosmique" not in resp.text


@pytest.mark.asyncio
async def test_files_tier_header_has_tooltip_and_no_legacy_legend(
    populated_app: tuple[Starlette, str],
) -> None:
    """The tier meanings now live in a "?" header tooltip on the Tier column, replacing the
    old free-standing <p class="tier-legend"> block."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    # The legacy legend block is gone.
    assert 'class="tier-legend"' not in resp.text
    assert "Tier legend" not in resp.text
    # A focusable "?" trigger describes a tooltip that enumerates the three tiers.
    assert 'aria-describedby="tip-tier"' in resp.text
    assert 'id="tip-tier"' in resp.text
    assert 'role="tooltip"' in resp.text
    assert "automatically queued for download" in resp.text
    assert "flagged for manual review" in resp.text


@pytest.mark.asyncio
async def test_files_nonobvious_columns_have_header_tooltips(
    populated_app: tuple[Starlette, str],
) -> None:
    """The other non-obvious columns (Verdict, Target, Sources) also carry a "?" header
    tooltip explaining their values."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert 'id="tip-verdict"' in resp.text
    assert 'id="tip-target"' in resp.text
    assert 'id="tip-sources"' in resp.text
    # A distinctive phrase from each of the three tooltips.
    assert "not yet verified" in resp.text  # verdict
    assert "the episode this file is matched to" in resp.text  # target
    assert "peers" in resp.text  # sources


@pytest.mark.asyncio
async def test_target_page_resolves_title_via_segment_mapping(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    """/targets/{id} shares _to_display_rows with /files — confirm handle_target ALSO
    threads the segment_by_id mapping (both call sites, per the brief)."""
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200
    assert "062A / S02E11A" in resp.text
    assert "La Grenouille Cosmique" in resp.text


# ---------------------------------------------------------------------------
# Whole-episode end-to-end (Task 4 — the multi-target headline behavior):
# one recovered whole-episode file resolves BOTH lost segments, renders as
# ONE <tr> with an aggregated Target/Title/Tier cell, and appears under EACH
# of its targets.
# ---------------------------------------------------------------------------


def _write_targets_yaml_ab(path: Path) -> Path:
    (path / "targets_ab.yaml").write_text(
        """\
episodes:
  - season: 3
    seasonal_number: 6
    absolute_number: 72
    segments:
      - letter: a
        title: "Le Defi"
      - letter: b
        title: "Duel Contre Giroro"
""",
        encoding="utf-8",
    )
    return path / "targets_ab.yaml"


@pytest.fixture
def app_whole_episode(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """One file matched to BOTH 072A and 072B (two current decisions, tier download) against a
    two-segment targets.yaml — the core multi-target end-to-end fixture (spec §9)."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files VALUES (?, ?, ?)", (TEST_HASH, 170_000_000, None))
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_072_vf.avi",
                170_000_000,
                7,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "072A", "numero_nu_confirmed", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (2, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "072B", "numero_nu_confirmed", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-whole"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml_ab(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.mark.asyncio
async def test_files_whole_episode_renders_one_row_with_aggregated_targets(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert "<td>072A / S03E06A · 072B / S03E06B</td>" in resp.text
    assert "<td>Le Defi · Duel Contre Giroro</td>" in resp.text
    assert "<td>download</td>" in resp.text


@pytest.mark.asyncio
async def test_whole_episode_appears_under_each_target(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_a = await client.get("/targets/072A")
        resp_b = await client.get("/targets/072B")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert hash_[:8] in resp_a.text
    assert hash_[:8] in resp_b.text


@pytest.mark.asyncio
async def test_file_detail_whole_episode_shows_both_targets(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "072A" in resp.text
    assert "072B" in resp.text
