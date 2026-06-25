"""Tests TDD pour l'application Starlette (composition/app.py — Task 11)."""

import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from catalog_webui.composition.app import build_app

# ---------------------------------------------------------------------------
# Helpers YAML
# ---------------------------------------------------------------------------

TEST_HASH = "aabbccdd00112233aabbccdd00112233"


def _write_targets_yaml(path: Path) -> Path:
    (path / "targets.yaml").write_text(
        """\
episodes:
  - season: 2
    number: 62
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
# Fixture : app avec données
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_app(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """Insère des données de test et construit l'app Starlette."""
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
            (TEST_HASH, "S2E062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
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
    """Fichier sans décision de matching (branche decision=None)."""
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
        # Pas de décision
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
    """Fichier sans observation (branche last_obs=None → link='')."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        # Pas d'observations
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
    """Fichier avec une décision pour un target_id inconnu de la config courante."""
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
        # target_id inconnu de la config YAML courante
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
    """/ → 200 + contient S2E062A dans la page."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "S2E062A" in resp.text


@pytest.mark.asyncio
async def test_files_returns_200_with_file_row(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files → 200 + contient le hash du fichier inséré."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text


@pytest.mark.asyncio
async def test_files_filtered_verdict_returns_200_empty(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files?verdict=malicious → 200 (aucun résultat, pas d'erreur)."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?verdict=malicious")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_file_detail_with_decision_returns_200(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} avec décision → 200, contient lien ed2k + infos d'explication."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text
    assert "S2E062A" in resp.text


@pytest.mark.asyncio
async def test_file_detail_without_decision_returns_200(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """/files/{hash} sans décision → 200, branches vides rendues."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text
    assert "Aucune décision de matching." in resp.text
    assert "Aucune explication disponible." in resp.text


@pytest.mark.asyncio
async def test_file_detail_explanation_none_unknown_target(
    app_unknown_target: tuple[Starlette, str],
) -> None:
    """/files/{hash} avec target_id inconnu de la config → 200 (explanation=None)."""
    app, hash_ = app_unknown_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text


@pytest.mark.asyncio
async def test_file_detail_unknown_hash_returns_404(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} inexistant (32 hex) → 404."""
    app, _ = populated_app
    unknown = "00000000000000000000000000000000"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{unknown}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_targets_shortcut_returns_200(
    populated_app: tuple[Starlette, str],
) -> None:
    """/targets/{target_id} → 200 (raccourci vers /files filtré)."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/S2E062A")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_node_returns_200_with_node_info(
    populated_app: tuple[Starlette, str],
) -> None:
    """/node → 200 + contient l'id du nœud."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/node")
    assert resp.status_code == 200
    assert "node-test-001" in resp.text


@pytest.mark.asyncio
async def test_files_non_numeric_page_defaults_to_1(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files?page=abc → 200 (page non numérique → défaut 1)."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?page=abc")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_file_detail_no_observations_returns_200(
    app_no_observations: tuple[Starlette, str],
) -> None:
    """/files/{hash} sans observation → 200 ; pas de lien ed2k, branche vide rendue."""
    app, hash_ = app_no_observations
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" not in resp.text
    assert "Aucune observation." in resp.text


@pytest.mark.asyncio
async def test_base_nav_uses_singular_node_href(
    populated_app: tuple[Starlette, str],
) -> None:
    """La nav de base contient href="/node" (singulier) et PAS href="/nodes"."""
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
    """/node → la clé last_search_cycle du scheduler_state apparaît dans le HTML."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/node")
    assert resp.status_code == 200
    assert "last_search_cycle" in resp.text


@pytest.fixture
def app_with_media_obs(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """Fichier avec observation ayant media_length_sec et bitrate_kbps non-null."""
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
            (TEST_HASH, "S2E062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
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
    """/files/{hash} avec media_length_sec + bitrate_kbps → 200, explication calculée."""
    app, hash_ = app_with_media_obs
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "S2E062A" in resp.text


@pytest.mark.asyncio
async def test_file_detail_unknown_hash_returns_styled_404(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} inexistant → 404 avec le template HTML stylé (contient 'introuvable')."""
    app, _ = populated_app
    unknown = "00000000000000000000000000000000"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{unknown}")
    assert resp.status_code == 404
    assert "introuvable" in resp.text.lower()


@pytest.fixture
def app_with_hostile_filename(
    catalog_db: Path, local_db: Path, tmp_path: Path
) -> tuple[Starlette, str]:
    """Fichier dont le nom contient un ``|`` (séparateur du lien eD2k) — régression
    webui-security#0 : sans percent-encoding, le ``|`` du nom décale taille/hash et le lien
    pointe ailleurs."""
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
async def test_hostile_filename_is_escaped_in_ed2k_link(
    app_with_hostile_filename: tuple[Starlette, str],
) -> None:
    # Régression webui-security#0 : un ``|`` hostile dans le nom doit être percent-encodé
    # (%7C). Sans cet échappement, le lien aurait 6 ``|`` (au lieu de 5 séparateurs structurels)
    # et la taille/hash seraient décalés → fichier inutilisable / pointant ailleurs.
    app, hash_ = app_with_hostile_filename
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    # Le lien dans la réponse doit contenir %7C (et NON un ``|`` brut au milieu du nom).
    assert "%7C" in resp.text
    # On extrait la première occurrence d'``ed2k://`` jusqu'au prochain whitespace/`"` pour
    # vérifier qu'elle a EXACTEMENT 5 ``|`` (les 5 séparateurs structurels du lien canonique).
    start = resp.text.index("ed2k://")
    end = start
    while end < len(resp.text) and resp.text[end] not in ('"', "<", " ", "\n"):
        end += 1
    link = resp.text[start:end]
    assert link.count("|") == 5
