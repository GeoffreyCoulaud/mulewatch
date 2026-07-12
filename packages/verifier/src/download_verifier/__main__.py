"""Verifier entry point: ``python -m download_verifier`` (verify spec §4; logging E-D2).

Two-step bootstrap: ``basicConfig(INFO)`` then ``setLevel`` from the observability YAML
(``VERIFIER_CONFIG``) before ``uvicorn.run``. The quarantine directory comes from
``QUARANTINE_DIR`` (read by ``app.py`` at import time)."""

import logging
import os
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path

import uvicorn

from download_verifier.obs_config import load_observability

_logger = logging.getLogger("download_verifier.__main__")


def configure_logging(env: Mapping[str, str]) -> None:
    """Arm logging (INFO).

    If ``VERIFIER_CONFIG`` is present, then apply the YAML's ``log_level``.
    """
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger().setLevel(logging.INFO)
    config_path = env.get("VERIFIER_CONFIG")
    if config_path:
        log_level = load_observability(Path(config_path)).log_level
        logging.getLogger().setLevel(log_level)


def main() -> None:
    """Configure logging then serve the verifier app (host/port from the environment)."""
    configure_logging(os.environ)
    # Startup version line (spec 2026-07-10-git-driven-versioning): the number baked into the
    # installed wheel by the build, so a running verifier can be correlated to a release.
    _logger.info("download-verifier version %s", version("download-verifier"))
    uvicorn.run(
        "download_verifier.app:app",
        host=os.environ.get("VERIFIER_HOST", "127.0.0.1"),
        port=int(os.environ.get("VERIFIER_PORT", "8000")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
