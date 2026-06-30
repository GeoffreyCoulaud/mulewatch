import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from catalog_matching.engine import DownloadCandidate
from catalog_matching.models import TargetSegment
from emule_indexer.application.run_download_cycle import DownloadDeps, run_download_cycle
from emule_indexer.domain.download.states import DownloadState
from emule_indexer.domain.observability.events import DownloadCompleted, PromotionFailed
from emule_indexer.ports.catalog_repository import ObservedFile
from emule_indexer.ports.mule_client import (
    KadStatus,
    MuleSearchFailedError,
    MuleUnreachableError,
    NetworkStatus,
)
from emule_indexer.ports.mule_download_client import DownloadEntry, SharedFileEntry
from emule_indexer.ports.repository_errors import RepositoryError
from tests.application.fakes import RecordingTelemetry

_A = "a" * 32
_B = "b" * 32

_TARGETS = (
    TargetSegment(
        season=2, seasonal_number=11, absolute_number=62, segment="A", title="t", status="lost"
    ),
    TargetSegment(
        season=2,
        seasonal_number=63,
        absolute_number=63,
        segment="A",
        title="t2",
        status="complete",
    ),
)


class FakeDownloadClient:
    """MuleDownloadClient scripté : file de download SCRIPTÉE, capture des liens ajoutés."""

    def __init__(
        self,
        *,
        queue: list[tuple[DownloadEntry, ...]] | None = None,
        shared: list[tuple[SharedFileEntry, ...]] | None = None,
        connect_failures: list[Exception] | None = None,
        queue_failures: list[Exception] | None = None,
        add_failures: list[Exception] | None = None,
        shared_failures: list[Exception] | None = None,
    ) -> None:
        self._queue = list(queue or [()])
        self._shared = list(shared or [()])
        self._connect_failures = list(connect_failures or [])
        self._queue_failures = list(queue_failures or [])
        self._add_failures = list(add_failures or [])
        self._shared_failures = list(shared_failures or [])
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

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        if self._shared_failures:
            raise self._shared_failures.pop(0)
        return self._shared.pop(0) if self._shared else ()

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


class FakeQuarantine:
    """Quarantine fausse FIDÈLE au contrat : ``promote`` fait un ``os.replace`` qui CONSOMME la
    source. Un re-promote du même hash (source déjà consommée, cible déjà en place) reproduit le
    comportement du vrai ``FilesystemQuarantine.promote`` — voir cette branche. ``fail_for``
    simule une panne FS (``OSError``) au premier promote."""

    def __init__(self, *, fail_for: set[str] | None = None) -> None:
        self.promoted: list[tuple[Path, str]] = []
        self._fail_for = fail_for or set()
        self._consumed: set[str] = set()

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        if ed2k_hash in self._fail_for:
            raise OSError("rename impossible")
        if ed2k_hash in self._consumed:
            # source déjà consommée par une promotion antérieure (cible quarantine/<hash> en
            # place) : le vrai FilesystemQuarantine.promote est idempotent → no-op succès.
            return
        self._consumed.add(ed2k_hash)
        self.promoted.append((staging_path, ed2k_hash))


class FakeDownloadRepo:
    """Repo downloads en mémoire (le contrat de SqliteDownloadRepository, sans SQL).

    ``fail_set_state_for`` : hashes pour lesquels ``set_state`` lève ``RepositoryError`` —
    permet de simuler un échec repo MILIEU de cycle (cf. logic-download#2/error-boundary#2).
    ``fail_active_states`` : ``active_states()`` lève — permet de simuler la persistance KO
    aussi à la relecture (toutes les étapes du cycle absorbent)."""

    def __init__(
        self,
        *,
        fail_record: bool = False,
        fail_set_state_for: set[str] | None = None,
        fail_active_states: bool = False,
    ) -> None:
        self.states: dict[str, DownloadState] = {}
        self.sizes: dict[str, int] = {}
        self._fail_record = fail_record
        self._fail_set_state_for = fail_set_state_for or set()
        self._fail_active_states = fail_active_states
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
        if ed2k_hash in self._fail_set_state_for:
            raise RepositoryError(f"set_state({ed2k_hash}) en échec")
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
        if self._fail_active_states:
            raise RepositoryError("active_states en échec")
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
        staging_dir=Path("/staging"),
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
async def test_monitor_does_not_regress_terminal_or_completed_queue_entry() -> None:
    # _monitor : un hash suivi mais TERMINAL/déjà complété (quarantined/failed/completed) présent
    # dans la file amuled NE DOIT PAS régresser vers DOWNLOADING (branche de skip du monitor).
    class _NoSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise AssertionError("set_state ne doit pas être appelé (état terminal/complété)")

    for terminal in (
        DownloadState.QUARANTINED,
        DownloadState.FAILED,
        DownloadState.COMPLETED,
    ):
        client = FakeDownloadClient(
            queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)]
        )
        repo = _NoSetStateRepo()
        repo.states[_A] = terminal
        deps = _deps(
            client=client,
            quarantine=FakeQuarantine(),
            downloads=repo,
            catalog=FakeCatalogReads(),
            local=FakeLocalRepo(),
        )
        await run_download_cycle(deps)
        assert repo.states[_A] is terminal  # inchangé


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
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
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
async def test_monitor_repo_error_still_promotes_completions_in_same_cycle() -> None:
    # Régression logic-download#2 : si ``_monitor`` lève ``RepositoryError`` (set_state KO sur
    # un autre hash), l'ancien code posait ``states={}`` puis appelait ``_handle_completions``
    # → chaque hash partagé → ``states.get(...) is None`` → ignoré → AUCUNE complétion promue
    # du cycle entier (latence +1 cycle alors qu'on a déjà le signal). Le fix relit
    # ``active_states()`` AVANT ``_handle_completions`` pour que les complétions soient vues.
    client = FakeDownloadClient(
        queue=[
            (
                DownloadEntry(ed2k_hash=_A, size_done=0, size_full=0),
                DownloadEntry(ed2k_hash=_B, size_done=0, size_full=0),
            )
        ],
        shared=[(SharedFileEntry(ed2k_hash=_A, name="keroro_062a.avi"),)],
    )
    downloads = FakeDownloadRepo(fail_set_state_for={_B})
    # _A déjà DOWNLOADING (pas de transition par _monitor) ; _B QUEUED → _monitor tentera
    # set_state(_B, DOWNLOADING) qui lève → l'étape 1 plante, mais _A est complet dans shared.
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.states[_B] = DownloadState.QUEUED
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
    assert local.enqueued == [_A]  # la complétion d'_A est promue malgré l'échec de _monitor
    assert downloads.states[_A] is DownloadState.QUARANTINED


@pytest.mark.asyncio
async def test_active_states_repo_failure_is_absorbed_at_step_2() -> None:
    # ``active_states`` qui lève EN BOUCLE (panne repo persistante) : l'étape 1 absorbe, puis la
    # relecture de l'étape 2 (logic-download#2) absorbe à son tour → le cycle se termine sans
    # crasher la boucle (le cycle suivant rejouera). On exerce ICI la branche
    # ``except RepositoryError`` de l'étape 2 séparément de celle de l'étape 1.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(fail_active_states=True)
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas (les deux RepositoryError sont absorbés)


@pytest.mark.asyncio
async def test_one_hash_repo_failure_does_not_starve_other_completions() -> None:
    # Régression error-boundary#2 : une ``RepositoryError`` dans ``_promote_completion`` du hash N
    # remontait jusqu'au handler de cycle, abandonnant N+1, N+2 du même shared_files. Le fix isole
    # PAR HASH (try/except autour de _promote_completion), respectant l'intention « isolé par
    # étape » du commentaire (I2).
    client = FakeDownloadClient(
        shared=[
            (
                SharedFileEntry(ed2k_hash=_A, name="a.avi"),
                SharedFileEntry(ed2k_hash=_B, name="b.avi"),
            )
        ],
    )
    # _A et _B en DOWNLOADING ; set_state(_A, ...) plante → _promote_completion d'_A lève ;
    # _B doit néanmoins être promu (continuité intra-cycle).
    downloads = FakeDownloadRepo(fail_set_state_for={_A})
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.states[_B] = DownloadState.DOWNLOADING
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
    assert _B in local.enqueued  # _B est promu malgré l'échec sur _A
    assert downloads.states[_B] is DownloadState.QUARANTINED


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
    # _A complété via les fichiers PARTAGÉS (promu ce cycle) ; _B est un nouveau candidat
    # (mis en file + lien).
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="a.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
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
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
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
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
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
    # _handle_completions lève RepositoryError (enqueue_verification échoue sur le hash partagé
    # _A) → _queue_new_candidates ET _add_links tournent QUAND MÊME pour _B :
    # un échec repo de l'étape 2 n'affame pas l'étape 3 (anti-famine, I2).
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="a.avi"),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING  # partagé → promu étape 2 (enqueue lèvera)
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
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="a.avi"),)])
    downloads = FakeDownloadRepo(fail_record=True)  # étape 3 lève RepositoryError
    downloads.states[_A] = DownloadState.DOWNLOADING  # _A partagé → promu étape 2
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
async def test_completion_recovers_after_transient_enqueue_failure() -> None:
    # Régression logic-download#0 : un échec TRANSITOIRE d'enqueue_verification APRÈS un promote
    # réussi (source déjà consommée par os.replace) ne doit PAS bloquer le fichier pour toujours.
    # Au cycle suivant, enqueue rétablie + promote idempotent → le fichier finit QUARANTINED +
    # enfilé, au lieu de boucler indéfiniment sur PromotionFailed (source consommée introuvable).
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()  # PARTAGÉ entre les cycles : modélise la consommation de source
    shared = (SharedFileEntry(ed2k_hash=_A, name="a.avi"),)

    # Cycle 1 : promote réussit (source consommée) puis enqueue lève RepositoryError.
    await run_download_cycle(
        _deps(
            client=FakeDownloadClient(shared=[shared]),
            quarantine=quarantine,
            downloads=downloads,
            catalog=FakeCatalogReads(),
            local=FakeLocalRepo(fail_enqueue=True),
        )
    )
    assert downloads.states[_A] is DownloadState.COMPLETED  # bloqué à completed ce tour-ci
    assert (Path("/staging") / "a.avi", _A) in quarantine.promoted  # source DÉJÀ consommée

    # Cycle 2 : enqueue rétablie. Le hash est toujours partagé, état COMPLETED → re-promotion.
    local_ok = FakeLocalRepo()
    await run_download_cycle(
        _deps(
            client=FakeDownloadClient(shared=[shared]),
            quarantine=quarantine,
            downloads=downloads,
            catalog=FakeCatalogReads(),
            local=local_ok,
        )
    )
    assert downloads.states[_A] is DownloadState.QUARANTINED  # récupéré, plus de boucle infinie
    assert local_ok.enqueued == [_A]


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


# ---------------------------------------------------------------------------
# Complétion via les fichiers PARTAGÉS EC (signal positif) + promotion au VRAI NOM (DV10-Q2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_file_for_tracked_hash_is_promoted_with_real_name() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="Keroro 62a.avi"),)])
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert (Path("/staging") / "Keroro 62a.avi", _A) in quarantine.promoted
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert local.enqueued == [_A]


@pytest.mark.asyncio
async def test_shared_name_with_traversal_is_confined_to_basename() -> None:
    # nom partagé = input HOSTILE (CLAUDE.md « filenames are hostile input ») : un nom avec
    # traversal NE DOIT PAS sortir de staging_dir — la SOURCE d'os.replace reste confinée au
    # basename (_safe_basename, branche non-None).
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="../../etc/passwd"),)])
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    (path, _) = quarantine.promoted[0]
    assert path == Path("/staging") / "passwd"  # confiné au basename
    assert ".." not in path.parts
    assert path.parent == Path("/staging")


@pytest.mark.asyncio
async def test_already_completed_shared_hash_is_promoted_without_restamping() -> None:
    # _A déjà COMPLETED (un tour précédent l'a stampé mais promote avait échoué) réapparaît dans
    # les partagés → promotion réussit cette fois SANS re-stamper COMPLETED (branche
    # `current is COMPLETED` de _promote_completion : on saute le set_state, on promeut direct).
    class _NoCompletedSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            if state is DownloadState.COMPLETED:
                raise AssertionError("ne doit pas re-stamper COMPLETED (déjà completed)")
            super().set_state(ed2k_hash, state)

    downloads = _NoCompletedSetStateRepo()
    downloads.states[_A] = DownloadState.COMPLETED
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert (Path("/staging") / "x.avi", _A) in quarantine.promoted
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert local.enqueued == [_A]


@pytest.mark.asyncio
async def test_shared_file_for_untracked_hash_is_ignored() -> None:
    downloads = FakeDownloadRepo()  # _A non suivi
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []


@pytest.mark.asyncio
async def test_already_quarantined_shared_hash_is_not_repromoted() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUARANTINED
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []


@pytest.mark.asyncio
async def test_degenerate_shared_name_is_skipped() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name=".."),)])
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []
    # nom dégénéré → garde-fou AVANT le stamp completed : l'état reste inchangé (DOWNLOADING),
    # rejugé au tour suivant si amuled rapporte enfin un nom utilisable. (Concern relevé : le
    # spec du test disait COMPLETED, mais le code prod du spec retourne AVANT de stamper.)
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_promotion_failure_leaves_completed_for_retry() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine(fail_for={_A})
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    local = FakeLocalRepo()
    telemetry = RecordingTelemetry()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED
    assert local.enqueued == []
    assert any(isinstance(e, PromotionFailed) for e in telemetry.events)


@pytest.mark.asyncio
async def test_monitor_promotes_queued_to_downloading_not_completed() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)],
        shared=[()],  # pas encore partagé → pas de complétion
    )
    quarantine = FakeQuarantine()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING  # PAS completed (octets ignorés)
    assert quarantine.promoted == []


@pytest.mark.asyncio
async def test_shared_files_unreachable_aborts_iteration_gracefully() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    client = FakeDownloadClient(shared_failures=[MuleUnreachableError("flux mort")])
    quarantine = FakeQuarantine()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas
    assert quarantine.promoted == []
