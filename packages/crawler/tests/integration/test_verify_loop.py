"""E2e DE VÉRIFICATION : la boucle ↔ le VRAI service verifier (spec verify §9 — option A).

Run dédié : ( cd packages/crawler && uv run pytest -m verify_integration --no-cov )
Sans Docker : le service ``download_verifier`` tourne IN-PROCESS via ``httpx.ASGITransport``.
Un fichier est PRÉ-PLACÉ en quarantaine + une tâche enfilée → ``run_verification_cycle`` (avec
un VRAI ``HttpContentVerifier``, de VRAIS repos SQLite sur ``tmp_path``) produit une ligne
``file_verifications`` ``unverified``. Prouve le contrat de fil DTO↔réponse + l'écriture
durable, sans vrai download (le download→verify complet = validation homelab manuelle).
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from download_verifier.app import build_app
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog, open_local
from emule_indexer.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from emule_indexer.adapters.verifier_http import HttpContentVerifier
from emule_indexer.application.run_verification_cycle import VerifyDeps, run_verification_cycle
from emule_indexer.domain.observation import FileObservation

pytestmark = pytest.mark.verify_integration

_A = "a" * 32
_NODE = "11111111-2222-3333-4444-555555555555"


class _RealClock:
    """Horloge réelle minimale (now aware + sleep no-op) pour la boucle de l'e2e."""

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
async def test_verify_loop_produces_unverified_row(
    tmp_path: Path, catalog: sqlite3.Connection, local: sqlite3.Connection
) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _A).write_bytes(b"\x00\x01\x02")  # fichier PRÉ-PLACÉ (jamais lu par le crawler)

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
    assert local_repo.enqueue_verification(_A) is True  # tâche enfilée (le download le ferait)

    transport = httpx.ASGITransport(app=build_app(quarantine))
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    verifier = HttpContentVerifier(client)
    deps = VerifyDeps(
        queue=local_repo,
        verifier=verifier,
        writer=catalog_repo,
        targets=downloads_repo,
        poll_interval_seconds=1.0,
        clock=_RealClock(),
    )
    try:
        await run_verification_cycle(deps)  # claim → verify (RPC réel) → record → complete
    finally:
        await verifier.aclose()

    row = catalog.execute(
        "SELECT ed2k_hash, verdict FROM file_verifications WHERE ed2k_hash = ?", (_A,)
    ).fetchone()
    assert row == (_A, "unverified")
    # la tâche est complétée (plus claimable).
    assert local_repo.claim_verification() is None
