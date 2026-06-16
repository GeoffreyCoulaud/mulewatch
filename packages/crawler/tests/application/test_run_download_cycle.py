import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.application.run_download_cycle import DownloadDeps, run_download_cycle
from emule_indexer.domain.download.states import DownloadState
from emule_indexer.domain.matching.engine import DownloadCandidate
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.observability.events import DownloadCompleted
from emule_indexer.ports.catalog_repository import ObservedFile
from emule_indexer.ports.mule_client import (
    KadStatus,
    MuleSearchFailedError,
    MuleUnreachableError,
    NetworkStatus,
)
from emule_indexer.ports.mule_download_client import DownloadEntry
from emule_indexer.ports.repository_errors import RepositoryError
from tests.application.fakes import RecordingTelemetry

_A = "a" * 32
_B = "b" * 32

_TARGETS = (
    TargetSegment(season=2, number=62, segment="A", title="t", status="lost"),
    TargetSegment(season=2, number=63, segment="A", title="t2", status="complete"),
)


class FakeDownloadClient:
    """MuleDownloadClient scripté : file de download SCRIPTÉE, capture des liens ajoutés."""

    def __init__(
        self,
        *,
        queue: list[tuple[DownloadEntry, ...]] | None = None,
        connect_failures: list[Exception] | None = None,
        queue_failures: list[Exception] | None = None,
        add_failures: list[Exception] | None = None,
    ) -> None:
        self._queue = list(queue or [()])
        self._connect_failures = list(connect_failures or [])
        self._queue_failures = list(queue_failures or [])
        self._add_failures = list(add_failures or [])
        self.added_links: list[str] = []
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_failures:
            raise self._connect_failures.pop(0)

    async def close(self) -> None:
        return None

    async def add_link(self, ed2k_link: str) -> None:
        if self._add_failures:
            raise self._add_failures.pop(0)
        self.added_links.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        if self._queue_failures:
            raise self._queue_failures.pop(0)
        return self._queue.pop(0) if self._queue else ()

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


class FakeQuarantine:
    """Quarantine fausse : enregistre les promotions, échoue sur les hash de ``fail_for``."""

    def __init__(self, *, fail_for: set[str] | None = None) -> None:
        self.promoted: list[tuple[Path, str]] = []
        self._fail_for = fail_for or set()

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        if ed2k_hash in self._fail_for:
            raise OSError("rename impossible")
        self.promoted.append((staging_path, ed2k_hash))


class FakeDownloadRepo:
    """Repo downloads en mémoire (le contrat de SqliteDownloadRepository, sans SQL)."""

    def __init__(self, *, fail_record: bool = False) -> None:
        self.states: dict[str, DownloadState] = {}
        self.sizes: dict[str, int] = {}
        self._fail_record = fail_record
        self._target_ids: dict[str, str] = {}

    def get_target_id(self, ed2k_hash: str) -> str | None:
        return self._target_ids.get(ed2k_hash)

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool:
        if self._fail_record:
            raise RepositoryError("écriture downloads échouée")
        if ed2k_hash in self.states:
            return False
        self.states[ed2k_hash] = DownloadState.QUEUED
        self.sizes[ed2k_hash] = size_bytes
        return True

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
        self.states[ed2k_hash] = state

    def is_downloaded(self, ed2k_hash: str) -> bool:
        return ed2k_hash in self.states

    def committed_bytes(self) -> int:
        return sum(
            self.sizes.get(h, 0)
            for h, s in self.states.items()
            if s in {DownloadState.QUEUED, DownloadState.DOWNLOADING}
        )

    def active_states(self) -> dict[str, DownloadState]:
        return dict(self.states)


class FakeCatalogReads:
    """Côté lecture du catalogue : download_decisions + last_observation scriptés."""

    def __init__(
        self,
        *,
        candidates: tuple[DownloadCandidate, ...] = (),
        observations: dict[str, ObservedFile] | None = None,
    ) -> None:
        self._candidates = candidates
        self._observations = observations or {}

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return self._candidates

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return self._observations.get(ed2k_hash)


class FakeLocalRepo:
    """enqueue_verification (idempotent) capturé ; ``fail_enqueue`` lève ``RepositoryError``."""

    def __init__(self, *, fail_enqueue: bool = False) -> None:
        self.enqueued: list[str] = []
        self._fail_enqueue = fail_enqueue

    def enqueue_verification(self, ed2k_hash: str) -> bool:
        if self._fail_enqueue:
            raise RepositoryError("enqueue_verification échouée")
        first = ed2k_hash not in self.enqueued
        self.enqueued.append(ed2k_hash)
        return first


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


def _candidate(hash_hex: str, target_id: str) -> DownloadCandidate:
    return DownloadCandidate(ed2k_hash=hash_hex, target_id=target_id)


def _deps(
    *,
    client: FakeDownloadClient,
    quarantine: FakeQuarantine,
    downloads: FakeDownloadRepo,
    catalog: FakeCatalogReads,
    local: FakeLocalRepo,
    disk_cap: int = 1_000_000,
    telemetry: RecordingTelemetry | None = None,
) -> DownloadDeps:
    return DownloadDeps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
        targets=_TARGETS,
        disk_cap_bytes=disk_cap,
        staging_path_for=lambda entry: Path("/staging") / entry.ed2k_hash,
        clock=FakeClock(),
        telemetry=telemetry or RecordingTelemetry(),
    )


@pytest.mark.asyncio
async def test_new_candidate_is_queued_and_link_added() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="Keroro.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.QUEUED
    assert len(client.added_links) == 1
    assert _A in client.added_links[0]


@pytest.mark.asyncio
async def test_already_downloaded_candidate_is_deduped() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING  # déjà connu
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []  # dédup : pas de nouveau lien


@pytest.mark.asyncio
async def test_complete_target_candidate_is_skipped() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E063A"),),  # S2E063A status=complete
        observations={_B: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _B not in downloads.states


@pytest.mark.asyncio
async def test_disk_cap_defers_candidate() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=500)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
        disk_cap=100,  # 500 > 100 → diffère
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_candidate_without_observation_is_skipped() -> None:
    # un candidat dont aucune observation n'a survécu (cas limite) ne peut pas bâtir de lien :
    # on le saute (log), jamais de crash.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(candidates=(_candidate(_A, "S2E062A"),), observations={})
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_marks_downloading_then_completed() -> None:
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    downloads.sizes[_A] = 10
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    # complet → promu + enfilé + quarantined
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert quarantine.promoted == [(Path("/staging") / _A, _A)]
    assert local.enqueued == [_A]


@pytest.mark.asyncio
async def test_monitor_marks_in_progress_when_not_complete() -> None:
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_monitor_ignores_unknown_queue_entries() -> None:
    # une entrée dans la file amuled mais inconnue de downloads (lancée hors crawler) est ignorée.
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_B, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert _B not in downloads.states


@pytest.mark.asyncio
async def test_promote_failure_keeps_completed_and_does_not_enqueue() -> None:
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    quarantine = FakeQuarantine(fail_for={_A})
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED  # reste completed (retry)
    assert local.enqueued == []  # n'enfile PAS


@pytest.mark.asyncio
async def test_already_quarantined_completion_is_skipped() -> None:
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUARANTINED  # déjà promu
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []  # déjà quarantined → sauté
    assert local.enqueued == []


@pytest.mark.asyncio
async def test_unreachable_client_is_tolerated_and_iteration_skipped() -> None:
    client = FakeDownloadClient(queue_failures=[MuleUnreachableError("daemon down")])
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(candidates=(_candidate(_A, "S2E062A"),)),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas
    assert client.added_links == []  # itération sautée (pas de candidats traités)


@pytest.mark.asyncio
async def test_repository_error_is_absorbed() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(fail_record=True)  # record_queued lève RepositoryError
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas (RepositoryError absorbée)


@pytest.mark.asyncio
async def test_intra_cycle_disk_cap_accounts_for_links_added_this_cycle() -> None:
    # deux candidats de 600 o, plafond 1000 : le 1er passe (600 ≤ 1000), le 2e diffère
    # (600 + 600 > 1000) — le committed est recalculé EN MÉMOIRE au fil du cycle.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"), _candidate(_B, "S2E062A")),
        observations={
            _A: ObservedFile(filename="a", size_bytes=600),
            _B: ObservedFile(filename="b", size_bytes=600),
        },
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
        disk_cap=1000,
    )
    await run_download_cycle(deps)
    assert len(client.added_links) == 1  # un seul a tenu dans le plafond


@pytest.mark.asyncio
async def test_candidate_for_unknown_target_is_treated_as_complete() -> None:
    # _target_status : un candidat dont le target_id est ABSENT de _TARGETS → "complete"
    # (conservateur) → politique SKIP_COMPLETE → aucun lien, hash non mis en file.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S9E999Z"),),  # cible fantôme, absente de _TARGETS
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_monitor_no_op_when_state_already_matches() -> None:
    # _monitor : entrée en cours (done=3/full=10) et repo déjà DOWNLOADING → target == current
    # → AUCUN set_state (branche FALSE de `if target != current`).
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING

    class _NoSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise AssertionError("set_state ne doit pas être appelé (état déjà à jour)")

    repo = _NoSetStateRepo()
    repo.states[_A] = DownloadState.DOWNLOADING
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=repo,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert repo.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_queued_download_without_observation_emits_no_link() -> None:
    # _add_links : un download QUEUED en base mais sans observation au catalogue → pas de lien
    # (branche `if observation is None: continue`).
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    downloads.sizes[_A] = 100
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(observations={}),  # aucune observation
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_unreachable_keeps_queued_and_is_tolerated() -> None:
    # add_link lève MuleUnreachableError → toléré au niveau cycle ; le download reste QUEUED
    # (record_queued a déjà eu lieu) → rattrapé au tour suivant. Invariant write-before-network.
    client = FakeDownloadClient(add_failures=[MuleUnreachableError("down")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas
    assert downloads.states[_A] is DownloadState.QUEUED  # reste queued → rattrapé
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_rejected_marks_failed_and_does_not_crash() -> None:
    # add_link lève MuleSearchFailedError (le daemon a répondu EC_OP_FAILED — lien rejeté) :
    # CE hash est marqué FAILED (spec §9 « failed + log »), la boucle continue, ne lève pas.
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas (échec applicatif toléré par hash)
    assert downloads.states[_A] is DownloadState.FAILED  # lien rejeté → marqué failed
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_rejected_for_one_hash_does_not_block_the_next() -> None:
    # add_link rejeté (EC_OP_FAILED) pour _A, accepté pour _B : _A → FAILED, _B → lien émis et
    # reste QUEUED. La rupture n'avorte pas la boucle (continue au hash suivant).
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"), _candidate(_B, "S2E062A")),
        observations={
            _A: ObservedFile(filename="a", size_bytes=1),
            _B: ObservedFile(filename="b", size_bytes=1),
        },
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.FAILED  # rejeté
    assert downloads.states[_B] is DownloadState.QUEUED  # accepté (lien émis)
    assert any(_B in link for link in client.added_links)
    assert all(_A not in link for link in client.added_links)


@pytest.mark.asyncio
async def test_completion_and_new_candidate_in_the_same_cycle() -> None:
    # _A est déjà COMPLETED (promu ce cycle) ; _B est un nouveau candidat (mis en file + lien).
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.COMPLETED
    downloads.sizes[_A] = 10
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.QUARANTINED  # complété → promu + enfilé
    assert local.enqueued == [_A]
    assert downloads.states[_B] is DownloadState.QUEUED  # nouveau → mis en file
    assert any(_B in link for link in client.added_links)  # + lien émis


@pytest.mark.asyncio
async def test_emits_download_queued() -> None:
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="Keroro.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert any(type(e).__name__ == "DownloadQueued" for e in telemetry.events)


@pytest.mark.asyncio
async def test_emits_download_completed_on_promotion() -> None:
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    downloads._target_ids[_A] = "S2E062A"
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert any(
        isinstance(e, DownloadCompleted) and e.target_id == "S2E062A" for e in telemetry.events
    )


@pytest.mark.asyncio
async def test_emits_promotion_failed() -> None:
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    quarantine = FakeQuarantine(fail_for={_A})  # promote lève → PromotionFailed
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert any(type(e).__name__ == "PromotionFailed" for e in telemetry.events)


# ---------------------------------------------------------------------------
# I2 — granularité d'erreur PAR ÉTAPE (anti-famine) : un RepositoryError dans une
# étape (complétions / nouveaux candidats) ne doit PAS empêcher l'AUTRE de tourner.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_repo_failure_does_not_starve_new_candidates() -> None:
    # _handle_completions lève RepositoryError (enqueue_verification échoue sur le hash
    # completed _A) → _queue_new_candidates ET _add_links tournent QUAND MÊME pour _B :
    # un échec repo de l'étape 2 n'affame pas l'étape 3 (anti-famine, I2).
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.COMPLETED  # à promouvoir étape 2 (enqueue lèvera)
    downloads.sizes[_A] = 10
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),  # nouveau candidat étape 3
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    local = FakeLocalRepo(fail_enqueue=True)  # étape 2 lève RepositoryError
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=local,
    )
    await run_download_cycle(deps)  # ne lève pas
    # Effet observable de l'étape 3 : _B mis en file ET son lien émis malgré l'échec étape 2.
    assert downloads.states[_B] is DownloadState.QUEUED
    assert any(_B in link for link in client.added_links)
    # _A reste COMPLETED (l'échec d'enqueue a laissé l'étape 2 incomplète → retry au tour suivant).
    assert downloads.states[_A] is DownloadState.COMPLETED


@pytest.mark.asyncio
async def test_candidate_repo_failure_does_not_starve_completions() -> None:
    # Symétrique : _queue_new_candidates lève RepositoryError (record_queued échoue) → les
    # complétions de l'étape 2 ont QUAND MÊME été promues (effet observable). L'échec de
    # l'étape 3 n'affame pas l'étape 2.
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo(fail_record=True)  # étape 3 lève RepositoryError
    downloads.states[_A] = DownloadState.QUEUED  # _A complet en file → promu étape 2
    downloads.sizes[_A] = 10
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),  # nouveau candidat → record_queued lèvera
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
    )
    await run_download_cycle(deps)  # ne lève pas
    # Effet observable de l'étape 2 : _A promu + enfilé malgré l'échec de l'étape 3.
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert local.enqueued == [_A]
    # _B n'a PAS été mis en file (record_queued a levé) → aucun lien émis pour lui.
    assert _B not in downloads.states
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_unreachable_aborts_subsequent_steps() -> None:
    # MuleUnreachableError dans _monitor (download_queue) = daemon mort → ABORT de l'itération :
    # ni les complétions (étape 2) ni les nouveaux candidats (étape 3) ne doivent tourner.
    # (Doctrine « un daemon mort fait tout échouer » — distincte de l'isolement RepositoryError.)
    client = FakeDownloadClient(queue_failures=[MuleUnreachableError("daemon down")])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.COMPLETED  # une complétion en attente (étape 2)
    downloads.sizes[_A] = 10
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),  # un candidat (étape 3)
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
    )
    await run_download_cycle(deps)  # ne lève pas (toléré) mais TOUT est sauté
    assert quarantine.promoted == []  # étape 2 NON exécutée (abort avant)
    assert local.enqueued == []
    assert downloads.states[_A] is DownloadState.COMPLETED  # inchangé
    assert _B not in downloads.states  # étape 3 NON exécutée
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_repo_failure_is_isolated_and_does_not_starve_candidates() -> None:
    # _monitor lève RepositoryError (set_state échoue lors de la réconciliation) → l'étape 1 est
    # ISOLÉE (log + continue), elle n'affame PAS l'étape 3 : _B est mis en file quand même.
    class _MonitorFailRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise RepositoryError("set_state monitor échouée")

    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = _MonitorFailRepo()
    downloads.states[_A] = DownloadState.QUEUED  # réconcilié → set_state(COMPLETED) lèvera
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E062A"),),
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas
    # L'étape 3 a tourné malgré l'échec de l'étape 1 : _B mis en file + lien émis.
    assert downloads.states[_B] is DownloadState.QUEUED
    assert any(_B in link for link in client.added_links)


@pytest.mark.asyncio
async def test_add_links_repo_failure_is_tolerated_and_does_not_raise() -> None:
    # _add_links lève RepositoryError (set_state échoue en marquant FAILED un lien rejeté) →
    # toléré (log), run_download_cycle ne lève pas. Contrat « ne lève JAMAIS ».
    class _AddLinkSetStateFailRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            if state is DownloadState.FAILED:
                raise RepositoryError("set_state(FAILED) échouée")
            super().set_state(ed2k_hash, state)

    # add_link rejeté (EC_OP_FAILED) → _add_links tente set_state(FAILED), qui lève RepositoryError.
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = _AddLinkSetStateFailRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève PAS (RepositoryError de l'étape 4 toléré)
