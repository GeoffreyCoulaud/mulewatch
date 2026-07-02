"""TDD tests for the uvicorn entry point (catalog_webui.__main__ — Task 12).

``uvicorn.run`` is monkeypatched to capture the call without starting a server.
The SQLite databases are created via the conftest ``catalog_db``/``local_db`` fixtures.
"""

from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette

from catalog_webui.__main__ import _require_env, main

# ---------------------------------------------------------------------------
# Minimal YAML helpers (same as in test_webui_app.py)
# ---------------------------------------------------------------------------


def _write_targets_yaml(tmp: Path) -> Path:
    (tmp / "targets.yaml").write_text(
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
    return tmp / "targets.yaml"


def _write_matcher_yaml(tmp: Path) -> Path:
    (tmp / "matcher.yaml").write_text(
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
    return tmp / "matcher.yaml"


# ---------------------------------------------------------------------------
# _require_env
# ---------------------------------------------------------------------------


def test_require_env_returns_value_when_present() -> None:
    """_require_env returns the value when the key is present."""
    env: Mapping[str, str] = {"FOO": "bar"}
    assert _require_env(env, "FOO") == "bar"


def test_require_env_raises_when_missing() -> None:
    """_require_env raises RuntimeError with the missing variable's name."""
    env: Mapping[str, str] = {}
    with pytest.raises(RuntimeError, match="CATALOG_DB"):
        _require_env(env, "CATALOG_DB")


# ---------------------------------------------------------------------------
# main() — happy path
# ---------------------------------------------------------------------------


def test_main_builds_app_and_runs_uvicorn(
    catalog_db: Path,
    local_db: Path,
    tmp_path: Path,
) -> None:
    """main() builds a Starlette app and calls uvicorn.run with the correct host/port.

    uvicorn.run is monkeypatched — no server is started.
    """
    targets = _write_targets_yaml(tmp_path)
    matcher = _write_matcher_yaml(tmp_path)

    captured: dict[str, object] = {}

    def fake_uvicorn_run(app: object, *, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    env = {
        "CATALOG_DB": str(catalog_db),
        "LOCAL_DB": str(local_db),
        "TARGETS_CONFIG": str(targets),
        "MATCHER_CONFIG": str(matcher),
        "WEBUI_HOST": "0.0.0.0",
        "WEBUI_PORT": "9000",
    }

    with (
        patch("catalog_webui.__main__.uvicorn") as mock_uvicorn,
        patch.dict("os.environ", env, clear=True),
    ):
        mock_uvicorn.run = MagicMock(side_effect=fake_uvicorn_run)
        main()

    assert isinstance(captured["app"], Starlette)
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000


def test_main_uses_default_host_and_port(
    catalog_db: Path,
    local_db: Path,
    tmp_path: Path,
) -> None:
    """main() uses host=127.0.0.1 and port=8080 by default (minimal env)."""
    targets = _write_targets_yaml(tmp_path)
    matcher = _write_matcher_yaml(tmp_path)

    captured: dict[str, object] = {}

    def fake_uvicorn_run(app: object, *, host: str, port: int) -> None:
        captured["host"] = host
        captured["port"] = port

    env = {
        "CATALOG_DB": str(catalog_db),
        "LOCAL_DB": str(local_db),
        "TARGETS_CONFIG": str(targets),
        "MATCHER_CONFIG": str(matcher),
    }

    with (
        patch("catalog_webui.__main__.uvicorn") as mock_uvicorn,
        patch.dict("os.environ", env, clear=True),
    ):
        mock_uvicorn.run = MagicMock(side_effect=fake_uvicorn_run)
        main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8080


# ---------------------------------------------------------------------------
# main() — fail-fast: required variables missing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key",
    ["CATALOG_DB", "LOCAL_DB", "TARGETS_CONFIG", "MATCHER_CONFIG"],
)
def test_main_missing_required_env_raises(
    missing_key: str,
    catalog_db: Path,
    local_db: Path,
    tmp_path: Path,
) -> None:
    """main() raises RuntimeError (fail-fast) if a required variable is missing."""
    targets = _write_targets_yaml(tmp_path)
    matcher = _write_matcher_yaml(tmp_path)

    full_env = {
        "CATALOG_DB": str(catalog_db),
        "LOCAL_DB": str(local_db),
        "TARGETS_CONFIG": str(targets),
        "MATCHER_CONFIG": str(matcher),
    }
    full_env.pop(missing_key)

    with (
        patch.dict("os.environ", full_env, clear=True),
        pytest.raises(RuntimeError, match=missing_key),
    ):
        main()
