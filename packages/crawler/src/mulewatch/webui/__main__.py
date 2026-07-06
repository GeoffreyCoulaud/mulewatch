"""webui entry point: ``python -m mulewatch.webui`` (webui spec — Task 12).

Reads the configuration from the environment, resolves the templates/static paths
relative to the package, builds the application via ``build_app`` and starts uvicorn.

Environment variables:
- ``CATALOG_DB``     : path to catalog.db (required)
- ``LOCAL_DB``       : path to local.db (required)
- ``TARGETS_CONFIG`` : path to targets.yaml (required)
- ``MATCHER_CONFIG`` : path to matcher.yaml (required)
- ``WEBUI_HOST``     : listen address (default: 127.0.0.1)
- ``WEBUI_PORT``     : listen port (default: 8080)
"""

import os
from collections.abc import Mapping
from pathlib import Path

import uvicorn

from mulewatch.webui.composition.app import build_app


def _require_env(env: Mapping[str, str], key: str) -> str:
    """Return ``env[key]`` or raise ``RuntimeError`` with the missing variable's name."""
    value = env.get(key)
    if value is None:
        raise RuntimeError(f"{key} required")
    return value


def main() -> None:
    """Configure and start the webui application (host/port/paths from the environment)."""
    env = os.environ

    catalog_db = Path(_require_env(env, "CATALOG_DB"))
    local_db = Path(_require_env(env, "LOCAL_DB"))
    targets = Path(_require_env(env, "TARGETS_CONFIG"))
    matcher = Path(_require_env(env, "MATCHER_CONFIG"))

    host = env.get("WEBUI_HOST", "127.0.0.1")
    port = int(env.get("WEBUI_PORT", "8080"))

    templates_dir = Path(__file__).parent / "adapters" / "templates"
    static_dir = Path(__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets,
        matcher=matcher,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
