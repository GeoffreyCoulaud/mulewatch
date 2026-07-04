"""TDD tests for the Starlette application (composition/app.py — Task 11)."""

import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from catalog_matching.models import TargetSegment
from catalog_webui.composition.app import _resolve_target_display, _to_display_rows, build_app
from catalog_webui.domain.views import FileRow

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

    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"

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

    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"

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

    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"

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

    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"

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

    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"

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

    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"

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

    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"

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
    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"
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
    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"
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
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="a",
    title="La Grenouille Cosmique",
)
_SEGMENT_BY_ID = {_SEGMENT_062A.target_id: _SEGMENT_062A}


def _file_row(
    *, target_id: str | None, tier: str | None, last_verdict: str | None = None
) -> FileRow:
    return FileRow(
        ed2k_hash=TEST_HASH,
        size_bytes=1024,
        filename="f.avi",
        source_count=1,
        last_seen="2024-01-01T00:00:00",
        target_id=target_id,
        tier=tier,
        last_verdict=last_verdict,
    )


def test_resolve_target_display_no_decision_is_all_dashes() -> None:
    row = _file_row(target_id=None, tier=None)
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == ("·", "·")


def test_resolve_target_display_catalog_tier_is_unidentified() -> None:
    """``keroro_large`` is the only catalog-tier rule — tier=="catalog" always means "not
    identified to a specific episode", regardless of the (possibly resolvable) target_id it
    was recorded against."""
    row = _file_row(target_id="062A", tier="catalog")
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == ("unidentified", "·")


def test_resolve_target_display_resolvable_id_joins_seasonal_locator_and_title() -> None:
    row = _file_row(target_id="062A", tier="download")
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == (
        "062A / S02E11A",
        "La Grenouille Cosmique",
    )


def test_resolve_target_display_unknown_id_falls_back_to_raw_id() -> None:
    """A target_id no longer present in the current targets.yaml (e.g. after an edit)."""
    row = _file_row(target_id="999Z", tier="download")
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == ("999Z", "·")


def test_to_display_rows_verdict_dash_when_no_decision() -> None:
    row = _file_row(target_id=None, tier=None)
    [display] = _to_display_rows([row], _SEGMENT_BY_ID)
    assert display.verdict_display == "·"


def test_to_display_rows_verdict_pending_when_decision_without_verdict() -> None:
    row = _file_row(target_id="062A", tier="download")
    [display] = _to_display_rows([row], _SEGMENT_BY_ID)
    assert display.verdict_display == "pending"


def test_to_display_rows_verdict_shows_actual_verdict() -> None:
    row = _file_row(target_id="062A", tier="download", last_verdict="clean")
    [display] = _to_display_rows([row], _SEGMENT_BY_ID)
    assert display.verdict_display == "clean"


def test_to_display_rows_computes_size_and_last_seen_display() -> None:
    row = FileRow(
        ed2k_hash=TEST_HASH,
        size_bytes=1024,
        filename="f.avi",
        source_count=1,
        last_seen="2026-07-03T23:45:24.104990+00:00",
        target_id=None,
        tier=None,
        last_verdict=None,
    )
    [display] = _to_display_rows([row], _SEGMENT_BY_ID)
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
