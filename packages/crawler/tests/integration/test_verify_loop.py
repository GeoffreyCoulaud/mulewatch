"""VERIFICATION e2e: the loop ↔ the REAL verifier service (verify spec §9 — option A).

Dedicated run: ( cd packages/crawler && uv run pytest -m verify_integration --no-cov )
Without Docker: the ``download_verifier`` service runs IN-PROCESS via ``httpx.ASGITransport``.
A file is PRE-PLACED in quarantine + a task enqueued → ``run_verification_cycle`` (with
a REAL ``HttpContentVerifier``, REAL SQLite repos on ``tmp_path``) produces a
``suspicious`` ``file_verifications`` row (the real analyzer: 3 bytes are not media).
Proves the DTO↔response wire contract + the durable write, without a real download
(the full download→verify = manual homelab validation).
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from download_verifier.app import build_app
from download_verifier.config import AnalysisConfig
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog, open_local
from emule_indexer.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from emule_indexer.adapters.verifier_http import HttpContentVerifier
from emule_indexer.application.edge_state import EdgeState
from emule_indexer.application.run_verification_cycle import VerifyDeps, run_verification_cycle
from emule_indexer.domain.observation import FileObservation
from tests.application.fakes import RecordingTelemetry

pytestmark = pytest.mark.verify_integration

_A = "a" * 32
_NODE = "11111111-2222-3333-4444-555555555555"


class _RealClock:
    """Minimal real clock (aware now + no-op sleep) for the e2e loop."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        return None


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    yield connection
    connection.close()


@pytest.fixture
def local(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_local(tmp_path / "local.db")
    yield connection
    connection.close()


@pytest.mark.asyncio
async def test_verify_loop_produces_suspicious_row(
    tmp_path: Path, catalog: sqlite3.Connection, local: sqlite3.Connection
) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _A).write_bytes(b"\x00\x01\x02")  # PRE-PLACED file (never read by the crawler)

    catalog_repo = SqliteCatalogRepository(catalog, _NODE)
    catalog_repo.record_observation(
        FileObservation(
            ed2k_hash=_A,
            filename="Keroro.avi",
            size_bytes=3,
            source_count=1,
            complete_source_count=0,
            keyword="keroro",
        )
    )
    downloads_repo = SqliteDownloadRepository(local)
    downloads_repo.record_queued(_A, "S2E062A", 3)
    local_repo = SqliteLocalStateRepository(local)
    assert local_repo.enqueue_verification(_A) is True  # task enqueued (the download would do it)

    verifier_config = AnalysisConfig.from_env({"QUARANTINE_DIR": str(quarantine)})
    transport = httpx.ASGITransport(app=build_app(verifier_config))
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    verifier = HttpContentVerifier(client)
    deps = VerifyDeps(
        queue=local_repo,
        verifier=verifier,
        writer=catalog_repo,
        targets=downloads_repo,
        poll_interval_seconds=1.0,
        clock=_RealClock(),
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    try:
        await run_verification_cycle(deps)  # claim → verify (real RPC) → record → complete
    finally:
        await verifier.aclose()

    row = catalog.execute(
        "SELECT ed2k_hash, verdict FROM file_verifications WHERE ed2k_hash = ?", (_A,)
    ).fetchone()
    assert row == (_A, "suspicious")
    # the task is completed (no longer claimable).
    assert local_repo.claim_verification() is None
