# Observabilité — Plan E.2 (instrumentation du crawler) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) ou superpowers:executing-plans. Steps en checkbox (`- [ ]`). **Prérequis : Plan E.1 mergé** (le socle `domain/observability/`, `ports/telemetry.py`, `adapters/observability/`, `EdgeState`, `ObservabilityConfig`/`NotificationTarget` existent).

**Goal:** Câbler le socle E.1 dans le crawler — émettre les événements depuis les 5 use-cases, exposer `/metrics`, notifier `CrawlerStarted` — sans changer aucune décision métier.

**Architecture:** Chaque use-case reçoit un `Telemetry` (injecté dans ses `*Deps`) et émet `await telemetry.emit(...)` aux points saillants. Les transitions edge (`AllInstancesBlind`, `VerifierUnavailable`) passent par un `EdgeState` partagé détenu par `CrawlerApp`. `CrawlerApp` construit registre Prometheus + `PrometheusSink` + `AppriseNotifier` + `ObservabilityDispatcher`, démarre le serveur de métriques si activé, et émet `CrawlerStarted`. Le label réseau (`ed2k`/`kad`) est dérivé du `SearchChannel` par construction.

**Tech Stack:** idem E.1 + `prometheus_client.start_http_server`. `mypy --strict`, 100 % branch.

**Réfs :** spec `docs/superpowers/specs/2026-06-15-observability-design.md` (§4, §10) ; plan E.1.

**Gate (vert après CHAQUE tâche, depuis `packages/crawler/`) :**
```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

**⚠️ Changement de signature propagé :** `record_observation` devient **async** ; `WorkerDeps`/`DownloadDeps`/`VerifyDeps` gagnent un champ `telemetry` (et `edge` pour deux d'entre eux). Tous les **tests existants** de ces use-cases doivent être mis à jour (construire les deps avec `telemetry=RecordingTelemetry()`, `await`, `@pytest.mark.asyncio`). Chaque tâche le précise.

---

## File Structure

**Créés :**
- `packages/crawler/src/emule_indexer/application/networks.py` — mapping `SearchChannel → "ed2k"/"kad"`.
- Tests miroirs des modules modifiés (ajouts).

**Modifiés (prod) :**
- `ports/local_state_repository.py` — `count_pending_verifications`.
- `adapters/persistence_sqlite/local_state_repository.py` — impl `count_pending_verifications`.
- `application/record_observations.py` — async + émissions + params `telemetry`/`network`.
- `application/search_worker.py` — `WorkerDeps.telemetry`, émissions, propagation `channel`.
- `application/run_search_cycle.py` — params `telemetry`/`edge`, émissions cycle/coverage.
- `application/run_download_cycle.py` — `DownloadDeps.telemetry`, `get_target_id` au Protocol, helpers async, émissions.
- `application/run_verification_cycle.py` — `VerifyDeps.telemetry`/`edge`, `count_pending` au Protocol, émissions.
- `composition/app.py` — construction observabilité + injection + `CrawlerStarted` + serveur métriques.
- `composition/__main__.py` — bootstrap logging deux-temps (déjà `basicConfig`) ; `setLevel` dans `build_app`.
- `tests/application/fakes.py` — `RecordingTelemetry`.
- `config/crawler.yaml`, `config/local.example.yaml` — exemples `observability`.

---

## Task 1 : `count_pending_verifications` (port + SQLite)

**Files:**
- Modify: `packages/crawler/src/emule_indexer/ports/local_state_repository.py`
- Modify: `packages/crawler/src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py`
- Test: `packages/crawler/tests/adapters/persistence_sqlite/test_local_state_repository.py` (existant)

- [ ] **Step 1 : test** — ajouter au fichier de test du repo (réutiliser sa fixture `repository`/`connection`) :

```python
def test_count_pending_verifications(repository: SqliteLocalStateRepository) -> None:
    assert repository.count_pending_verifications() == 0
    repository.enqueue_verification("a" * 32)
    repository.enqueue_verification("b" * 32)
    assert repository.count_pending_verifications() == 2
    claimed = repository.claim_verification()
    assert claimed is not None  # une tâche passe en in_progress → plus 'pending'
    assert repository.count_pending_verifications() == 1
```

- [ ] **Step 2 : lancer → échoue** — `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -k count_pending --no-cov -q )` ; Expected: FAIL (`AttributeError`).

- [ ] **Step 3 : impl** — dans `adapters/persistence_sqlite/local_state_repository.py`, ajouter la requête (près des autres `_…`) et la méthode :

```python
_COUNT_PENDING = "SELECT COUNT(*) FROM verification_tasks WHERE status = 'pending'"
```

```python
    def count_pending_verifications(self) -> int:
        """Nombre de tâches en attente (jauge d'observabilité — lecture inoffensive)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_COUNT_PENDING).fetchone()
        return int(row[0])
```

Dans `ports/local_state_repository.py`, ajouter au Protocol `LocalStateRepository` :

```python
    def count_pending_verifications(self) -> int: ...
```

- [ ] **Step 4 : lancer → passe** — même commande ; Expected: PASS.

- [ ] **Step 5 : gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint packages/crawler/src
git add packages/crawler/src/emule_indexer/ports/local_state_repository.py packages/crawler/src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py packages/crawler/tests/adapters/persistence_sqlite/test_local_state_repository.py
git commit -m "feat: count_pending_verifications on local state repo (Plan E.2)"
```

---

## Task 2 : `RecordingTelemetry` (fake) + `networks.py` (label réseau)

**Files:**
- Modify: `packages/crawler/tests/application/fakes.py`
- Create: `packages/crawler/src/emule_indexer/application/networks.py`
- Test: `packages/crawler/tests/application/test_networks.py`

- [ ] **Step 1 : ajouter `RecordingTelemetry` à `tests/application/fakes.py`** (en tête, importer `Event`) :

```python
from emule_indexer.domain.observability.events import Event


class RecordingTelemetry:
    """Telemetry faux : capture les événements émis (le test asserte la séquence)."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)
```

(Pas de test dédié au fake — il est exercé par les tâches suivantes.)

- [ ] **Step 2 : test `networks.py`** — Create `tests/application/test_networks.py` :

```python
"""Le label réseau dérive du SearchChannel par construction (E-D6)."""

from emule_indexer.application.networks import ED2K, KAD, network_label
from emule_indexer.ports.mule_client import SearchChannel


def test_global_is_ed2k() -> None:
    assert network_label(SearchChannel.GLOBAL) == ED2K == "ed2k"


def test_kad_is_kad() -> None:
    assert network_label(SearchChannel.KAD) == KAD == "kad"
```

- [ ] **Step 3 : lancer → échoue** — `( cd packages/crawler && uv run pytest tests/application/test_networks.py --no-cov -q )` ; Expected: FAIL (`ImportError`).

- [ ] **Step 4 : impl `application/networks.py`**

```python
"""Label réseau d'observabilité dérivé du canal de recherche (E-D6).

``SearchChannel.GLOBAL`` = serveurs eD2k → ``"ed2k"`` ; ``SearchChannel.KAD`` → ``"kad"``. La
source réseau d'une observation est connue PAR CONSTRUCTION (la recherche est lancée par canal),
sans toucher la persistance."""

from emule_indexer.ports.mule_client import SearchChannel

ED2K = "ed2k"
KAD = "kad"

_LABELS = {SearchChannel.GLOBAL: ED2K, SearchChannel.KAD: KAD}


def network_label(channel: SearchChannel) -> str:
    """``"ed2k"`` pour GLOBAL, ``"kad"`` pour KAD."""
    return _LABELS[channel]
```

- [ ] **Step 5 : lancer → passe** ; **gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/application/networks.py packages/crawler/tests/application/test_networks.py packages/crawler/tests/application/fakes.py
git commit -m "feat: network label helper + RecordingTelemetry fake (Plan E.2)"
```

---

## Task 3 : instrumenter `record_observation` (async + émissions)

**Files:**
- Modify: `packages/crawler/src/emule_indexer/application/record_observations.py`
- Test: `packages/crawler/tests/application/test_record_observations.py` (existant)

- [ ] **Step 1 : mettre à jour + étendre les tests** — dans `test_record_observations.py` : tout appel `record_observation(...)` devient `await record_observation(..., telemetry=telemetry, network="ed2k")` avec `telemetry = RecordingTelemetry()` ; marquer les tests `@pytest.mark.asyncio`. Ajouter :

```python
@pytest.mark.asyncio
async def test_emits_observation_then_decision_on_change(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    obs = _matching_observation()  # observation qui matche en tier=download (helper du fichier)
    await record_observation(
        obs, catalog=catalog, engine=engine, signal=signal, telemetry=telemetry, network="ed2k"
    )
    kinds = [type(e).__name__ for e in telemetry.events]
    assert kinds == ["ObservationRecorded", "DecisionRecorded"]
    assert telemetry.events[0] == ObservationRecorded(network="ed2k")


@pytest.mark.asyncio
async def test_emits_only_observation_when_discarded(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    telemetry = RecordingTelemetry()
    obs = _non_matching_observation()  # écarté par le moteur (helper du fichier)
    await record_observation(
        obs, catalog=catalog, engine=engine, signal=RecordingSignal(),
        telemetry=telemetry, network="kad",
    )
    assert [type(e).__name__ for e in telemetry.events] == ["ObservationRecorded"]
```

(Importer `RecordingTelemetry` depuis `tests.application.fakes`, `ObservationRecorded` depuis le domaine. Réutiliser/forger les helpers d'observation déjà présents dans le fichier.)

- [ ] **Step 2 : lancer → échoue** — `( cd packages/crawler && uv run pytest tests/application/test_record_observations.py --no-cov -q )` ; Expected: FAIL (`TypeError`/`AttributeError`).

- [ ] **Step 3 : impl** — réécrire `record_observation` (signature async + 2 params + 2 emits). Remplacer la fonction entière :

```python
async def record_observation(
    observation: FileObservation,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
    telemetry: Telemetry,
    network: str,
) -> bool:
    """Traite UNE observation (spec §4). Rend ``True`` ssi un NOUVEAU verdict a été persisté.

    Émet ``ObservationRecorded`` dès l'enregistrement (toujours), et ``DecisionRecorded`` au
    changement de verdict. Une ``RepositoryError`` est absorbée (log + ``False``), le cycle
    continue (spec §7)."""
    try:
        catalog.record_observation(observation)
        await telemetry.emit(ObservationRecorded(network=network))
        decision = engine.evaluate(observation.to_candidate())
        if decision is None:
            return False
        fresh = to_record(decision)
        if catalog.last_decision(observation.ed2k_hash) == fresh:
            return False
        catalog.record_decision(observation.ed2k_hash, decision)
    except RepositoryError as error:
        _logger.error(
            "persistance échouée sur hash=%s (%s) — observation ignorée, cycle continue",
            observation.ed2k_hash,
            error,
        )
        return False
    _logger.info(
        "verdict changé hash=%s target=%s tier=%s règle=%s",
        observation.ed2k_hash,
        decision.target_id,
        decision.tier,
        decision.rule_name,
    )
    await telemetry.emit(DecisionRecorded(target_id=decision.target_id, tier=decision.tier))
    signal.signal(observation.ed2k_hash)
    if decision.tier == "download":
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)
    return True
```

Ajouter en tête les imports :

```python
from emule_indexer.domain.observability.events import DecisionRecorded, ObservationRecorded
from emule_indexer.ports.telemetry import Telemetry
```

- [ ] **Step 4 : lancer → passe** ; **gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/application/record_observations.py packages/crawler/tests/application/test_record_observations.py
git commit -m "feat(application): emit observation/decision events (Plan E.2)"
```

---

## Task 4 : instrumenter `search_worker`

**Files:**
- Modify: `packages/crawler/src/emule_indexer/application/search_worker.py`
- Test: `packages/crawler/tests/application/test_search_worker.py` (existant)

- [ ] **Step 1 : mettre à jour + étendre les tests** — toute construction de `WorkerDeps(...)` reçoit `telemetry=RecordingTelemetry()`. Ajouter des assertions :

```python
@pytest.mark.asyncio
async def test_emits_search_executed_with_network(...) -> None:
    telemetry = RecordingTelemetry()
    # ... construire WorkerDeps(..., telemetry=telemetry), un FakeMuleClient avec 1 résultat
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="kw", channel=SearchChannel.GLOBAL))
    assert SearchExecuted(network="ed2k", n_results=1) in telemetry.events


@pytest.mark.asyncio
async def test_emits_search_failed_on_channel_error(...) -> None:
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(search_failures=[make_search_failed()])
    # ... WorkerDeps(..., telemetry=telemetry)
    await SearchWorker("amule-1", client, deps).run_task(
        SearchTask(keyword="kw", channel=SearchChannel.KAD)
    )
    assert SearchFailed(instance="amule-1", network="kad") in telemetry.events


@pytest.mark.asyncio
async def test_emits_instance_unreachable_on_connect_failure(...) -> None:
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(connect_failures=[make_unreachable()])
    # ... WorkerDeps(..., telemetry=telemetry) ; worker NON connecté
    await SearchWorker("amule-1", client, deps).run_task(
        SearchTask(keyword="kw", channel=SearchChannel.GLOBAL)
    )
    assert InstanceUnreachable(instance="amule-1") in telemetry.events
```

(Importer les événements depuis le domaine + `RecordingTelemetry`.)

- [ ] **Step 2 : lancer → échoue** — Expected: FAIL (`TypeError: WorkerDeps … telemetry`).

- [ ] **Step 3 : impl** — modifications dans `search_worker.py` :

(a) Imports en tête :

```python
from emule_indexer.application.networks import network_label
from emule_indexer.domain.observability.events import (
    InstanceUnreachable,
    SearchExecuted,
    SearchFailed,
)
from emule_indexer.ports.telemetry import Telemetry
```

(b) Champ sur `WorkerDeps` (après `backoff`) :

```python
    telemetry: Telemetry
```

(c) `_ensure_connected` — dans le `except MuleUnreachableError`, après le `_logger.warning(...)` et avant `return False`, ajouter :

```python
            await self._deps.telemetry.emit(InstanceUnreachable(instance=self._instance))
```

(d) `_poll_then_fetch` — prend le canal, émet `SearchExecuted`, passe `network` à `record_observation` (désormais `await`) :

```python
    async def _poll_then_fetch(self, channel: SearchChannel) -> int:
        waited = 0.0
        while waited < self._deps.policy.poll_budget_seconds:
            progress = await self._client.search_progress()
            if progress is not None and progress >= _PROGRESS_DONE:
                break
            await self._deps.clock.sleep(self._deps.policy.poll_interval_seconds)
            waited += self._deps.policy.poll_interval_seconds
        results = await self._client.fetch_results()
        network = network_label(channel)
        await self._deps.telemetry.emit(SearchExecuted(network=network, n_results=len(results)))
        changed = 0
        for observation in results:
            if await record_observation(
                observation,
                catalog=self._deps.catalog,
                engine=self._deps.engine,
                signal=self._deps.signal,
                telemetry=self._deps.telemetry,
                network=network,
            ):
                changed += 1
        return changed
```

(e) `run_task` — passer le canal à `_poll_then_fetch` et émettre aux deux `except` :

```python
        try:
            await self._client.start_search(task.keyword, task.channel)
            changed = await self._poll_then_fetch(task.channel)
        except MuleSearchFailedError as error:
            delay = self._deps.backoff.record_failure(channel_key)
            _logger.warning(
                "instance %s canal %s en échec (%s) — backoff %.1fs",
                self._instance, task.channel, error, delay,
            )
            await self._deps.telemetry.emit(
                SearchFailed(instance=self._instance, network=network_label(task.channel))
            )
            return
        except MuleUnreachableError as error:
            self._connected = False
            delay = self._deps.backoff.record_failure(self._instance)
            _logger.warning(
                "instance %s : flux EC mort (%s) — instance down, backoff %.1fs",
                self._instance, error, delay,
            )
            await self._deps.telemetry.emit(InstanceUnreachable(instance=self._instance))
            return
```

- [ ] **Step 4 : lancer → passe** ; **gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/application/search_worker.py packages/crawler/tests/application/test_search_worker.py
git commit -m "feat(application): emit search executed/failed/unreachable (Plan E.2)"
```

---

## Task 5 : instrumenter `run_search_cycle` (cycle + coverage + blind)

**Files:**
- Modify: `packages/crawler/src/emule_indexer/application/run_search_cycle.py`
- Test: `packages/crawler/tests/application/test_run_search_cycle.py` (existant)

- [ ] **Step 1 : mettre à jour + étendre les tests** — `run_search_cycle(...)` reçoit `telemetry=RecordingTelemetry()`, `edge=EdgeState()`. Ajouter :

```python
@pytest.mark.asyncio
async def test_emits_cycle_completed_and_connected_gauges(...) -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    # ... 1 client search-capable (ed2k_high=True, kad CONNECTED)
    await run_search_cycle(..., telemetry=telemetry, edge=edge)
    types = [type(e).__name__ for e in telemetry.events]
    assert "ConnectedInstancesSampled" in types
    assert types[-1] == "SearchCycleCompleted"


@pytest.mark.asyncio
async def test_blind_coverage_is_edge_triggered(...) -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    # ... clients TOUS non search-capable (UnreachableStatusClient)
    await run_search_cycle(..., cycle_index=0, telemetry=telemetry, edge=edge)
    blind = [e for e in telemetry.events if type(e).__name__ == "AllInstancesBlind"]
    assert blind and blind[0].first_occurrence is True
    # 2e cycle aveugle consécutif → first_occurrence False (anti-spam)
    telemetry.events.clear()
    await run_search_cycle(..., cycle_index=1, telemetry=telemetry, edge=edge)
    blind = [e for e in telemetry.events if type(e).__name__ == "AllInstancesBlind"]
    assert blind and blind[0].first_occurrence is False
```

- [ ] **Step 2 : lancer → échoue** — Expected: FAIL (`TypeError`).

- [ ] **Step 3 : impl** — dans `run_search_cycle.py` :

(a) Imports :

```python
from emule_indexer.application.edge_state import EdgeState
from emule_indexer.application.networks import ED2K, KAD
from emule_indexer.domain.observability.events import (
    AllInstancesBlind,
    ConnectedInstancesSampled,
    SearchCycleCompleted,
)
from emule_indexer.ports.telemetry import Telemetry
```

(b) `_aggregate_coverage` — compte par réseau, émet les deux gauges + `AllInstancesBlind` (edge) :

```python
async def _aggregate_coverage(
    clients: Sequence[MuleClient], telemetry: Telemetry, edge: EdgeState
) -> None:
    """Relève le statut → gauges connected{network} + couverture agrégée (loggée, spec §7)."""
    capable: list[bool] = []
    ed2k_count = 0
    kad_count = 0
    for client in clients:
        try:
            status = await client.network_status()
        except MuleUnreachableError as error:
            _logger.warning("instance injoignable au relevé de statut (%s) — non capable", error)
            capable.append(False)
            continue
        if status.ed2k_high:
            ed2k_count += 1
        if status.kad_status == KadStatus.CONNECTED:
            kad_count += 1
        capable.append(_is_search_capable(ed2k_high=status.ed2k_high, kad_status=status.kad_status))
    await telemetry.emit(ConnectedInstancesSampled(network=ED2K, count=ed2k_count))
    await telemetry.emit(ConnectedInstancesSampled(network=KAD, count=kad_count))
    coverage = effective_coverage(capable)
    if coverage == Coverage.BLIND:
        _logger.warning("effective_coverage=%s (blind)", coverage)
        await telemetry.emit(AllInstancesBlind(first_occurrence=edge.enter("coverage_blind")))
    else:
        _logger.info("effective_coverage=%s (%d instance(s))", coverage, len(capable))
        edge.leave("coverage_blind")
```

(c) `run_search_cycle` — ajouter les deux params, mesurer la durée, émettre `SearchCycleCompleted`. Modifier la signature (ajouter après `clock: Clock`) :

```python
    telemetry: Telemetry,
    edge: EdgeState,
```

Et le corps (début + fin) :

```python
    started = clock.now()
    await _aggregate_coverage(clients, telemetry, edge)
    # ... (génération keywords / fan-out / drain : INCHANGÉ) ...
    scheduler_state.write_cycle_state(cycle_index + 1, clock.now())
    scheduler_state.save_channel_backoff(backoff.snapshot())
    duration = (clock.now() - started).total_seconds()
    await telemetry.emit(
        SearchCycleCompleted(cycle_index=cycle_index, duration_seconds=duration)
    )
    _logger.info("cycle %d terminé", cycle_index)
```

- [ ] **Step 4 : lancer → passe** ; **gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/application/run_search_cycle.py packages/crawler/tests/application/test_run_search_cycle.py
git commit -m "feat(application): emit cycle/coverage/blind events (Plan E.2)"
```

---

## Task 6 : instrumenter `run_download_cycle`

**Files:**
- Modify: `packages/crawler/src/emule_indexer/application/run_download_cycle.py`
- Test: `packages/crawler/tests/application/test_run_download_cycle.py` (existant)

- [ ] **Step 1 : mettre à jour + étendre les tests** — `DownloadDeps(...)`/`DownloadLoopDeps(...)` reçoivent `telemetry=RecordingTelemetry()`. Le faux repo downloads gagne `get_target_id`. Ajouter :

```python
@pytest.mark.asyncio
async def test_emits_download_queued(...) -> None:
    telemetry = RecordingTelemetry()
    # ... deps avec un candidat tier=download éligible
    await run_download_cycle(deps)
    assert any(type(e).__name__ == "DownloadQueued" for e in telemetry.events)


@pytest.mark.asyncio
async def test_emits_download_completed_on_promotion(...) -> None:
    telemetry = RecordingTelemetry()
    # ... un hash 'completed' + quarantine qui réussit ; downloads.get_target_id → "S2E062A"
    await run_download_cycle(deps)
    assert any(
        type(e).__name__ == "DownloadCompleted" and e.target_id == "S2E062A"
        for e in telemetry.events
    )


@pytest.mark.asyncio
async def test_emits_promotion_failed(...) -> None:
    telemetry = RecordingTelemetry()
    # ... quarantine.promote lève → PromotionFailed
    await run_download_cycle(deps)
    assert any(type(e).__name__ == "PromotionFailed" for e in telemetry.events)
```

(Si le faux `DownloadRepository` de test n'a pas `get_target_id`, l'ajouter : `def get_target_id(self, h): return self._target_ids.get(h)`.)

- [ ] **Step 2 : lancer → échoue** — Expected: FAIL (`TypeError`/`AttributeError`).

- [ ] **Step 3 : impl** — dans `run_download_cycle.py` :

(a) Imports :

```python
from emule_indexer.domain.observability.events import (
    DownloadCompleted,
    DownloadQueued,
    PromotionFailed,
)
from emule_indexer.ports.telemetry import Telemetry
```

(b) Ajouter `get_target_id` au Protocol `DownloadRepository` (stub une ligne) :

```python
    def get_target_id(self, ed2k_hash: str) -> str | None: ...
```

(c) Champ sur `DownloadDeps` (après `clock`) :

```python
    telemetry: Telemetry
```

(d) `_promote_completion` → **async**, émet `DownloadCompleted`/`PromotionFailed` :

```python
async def _promote_completion(deps: DownloadDeps, ed2k_hash: str) -> None:
    """Promeut un hash ``completed`` → quarantaine + enqueue + ``quarantined`` (étape 2, §5)."""
    entry = DownloadEntry(ed2k_hash=ed2k_hash, size_done=0, size_full=0)
    staging_path = deps.staging_path_for(entry)
    try:
        deps.quarantine.promote(staging_path, ed2k_hash)
    except Exception as error:  # noqa: BLE001 — toute panne FS laisse completed (retry idempotent)
        _logger.warning(
            "quarantaine échouée pour hash=%s (%s) — reste completed, retry", ed2k_hash, error
        )
        await deps.telemetry.emit(PromotionFailed(ed2k_hash=ed2k_hash))
        return
    deps.local.enqueue_verification(ed2k_hash)
    deps.downloads.set_state(ed2k_hash, DownloadState.QUARANTINED)
    target_id = deps.downloads.get_target_id(ed2k_hash) or "inconnu"
    await deps.telemetry.emit(DownloadCompleted(target_id=target_id, ed2k_hash=ed2k_hash))
    _logger.info("hash=%s mis en quarantaine + vérification enfilée", ed2k_hash)
```

(e) `_handle_completions` → **async** :

```python
async def _handle_completions(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Promeut chaque hash ``completed`` pas encore ``quarantined`` (étape 2, spec §5)."""
    for ed2k_hash, state in list(states.items()):
        if state is DownloadState.COMPLETED:
            await _promote_completion(deps, ed2k_hash)
```

(f) `_queue_new_candidates` → **async**, émet `DownloadQueued`. Après le `deps.downloads.record_queued(...)` + `committed += ...`, ajouter :

```python
        await deps.telemetry.emit(DownloadQueued(target_id=candidate.target_id))
```

(et changer la signature en `async def _queue_new_candidates(deps: DownloadDeps) -> None:`).

(g) `run_download_cycle` — `await` les deux helpers désormais async :

```python
        states = deps.downloads.active_states()
        await _monitor(deps, states)
        await _handle_completions(deps, states)
        await _queue_new_candidates(deps)
        await _add_links(deps)
```

- [ ] **Step 4 : lancer → passe** ; **gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/application/run_download_cycle.py packages/crawler/tests/application/test_run_download_cycle.py
git commit -m "feat(application): emit download queued/completed/promotion-failed (Plan E.2)"
```

---

## Task 7 : instrumenter `run_verification_cycle`

**Files:**
- Modify: `packages/crawler/src/emule_indexer/application/run_verification_cycle.py`
- Test: `packages/crawler/tests/application/test_run_verification_cycle.py` (existant)

- [ ] **Step 1 : mettre à jour + étendre les tests** — `VerifyDeps(...)`/`VerifyLoopDeps(...)` reçoivent `telemetry=RecordingTelemetry()`, `edge=EdgeState()`. Le faux `queue` gagne `count_pending_verifications`. Ajouter :

```python
@pytest.mark.asyncio
async def test_emits_verification_completed_and_queue_depth(...) -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    # ... une tâche claimable, verifier rend verdict=clean, targets.get_target_id → "S2E062A"
    await run_verification_cycle(deps)
    assert VerificationQueueDepthSampled(count=...) in telemetry.events  # selon le faux queue
    assert any(
        type(e).__name__ == "VerificationCompleted" and e.verdict == "clean"
        for e in telemetry.events
    )


@pytest.mark.asyncio
async def test_verifier_unavailable_is_edge_triggered(...) -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    # ... verifier qui lève VerifierUnavailableError
    await run_verification_cycle(deps)
    unav = [e for e in telemetry.events if type(e).__name__ == "VerifierUnavailable"]
    assert unav and unav[0].first_occurrence is True
```

(Faux `queue` : ajouter `def count_pending_verifications(self) -> int: return len(self._pending)` ou une constante.)

- [ ] **Step 2 : lancer → échoue** — Expected: FAIL (`TypeError`/`AttributeError`).

- [ ] **Step 3 : impl** — dans `run_verification_cycle.py` :

(a) Imports :

```python
from emule_indexer.application.edge_state import EdgeState
from emule_indexer.domain.observability.events import (
    VerificationCompleted,
    VerificationQueueDepthSampled,
    VerifierUnavailable,
)
from emule_indexer.ports.telemetry import Telemetry
```

(b) Ajouter au Protocol `VerificationTaskQueue` (stub une ligne) :

```python
    def count_pending_verifications(self) -> int: ...
```

(c) Champs sur `VerifyDeps` (après `clock`) :

```python
    telemetry: Telemetry
    edge: EdgeState
```

(d) `run_verification_cycle` — émettre la jauge après `reclaim_expired`, `VerificationCompleted` après `complete`, `VerifierUnavailable(edge)` dans le `except`. Modifs ciblées du corps :

Après `deps.queue.reclaim_expired()` :

```python
        deps.queue.reclaim_expired()
        await deps.telemetry.emit(
            VerificationQueueDepthSampled(count=deps.queue.count_pending_verifications())
        )
```

Dans le bloc succès (après `deps.queue.complete_verification(task.task_id)`) :

```python
            deps.queue.complete_verification(task.task_id)
            deps.edge.leave("verifier_unavailable")
            await deps.telemetry.emit(
                VerificationCompleted(
                    target_id=str(expected.get("target_id", "inconnu")), verdict=result.verdict
                )
            )
```

Dans `except VerifierUnavailableError`, après `deps.queue.fail_verification(task.task_id)` :

```python
            await deps.telemetry.emit(
                VerifierUnavailable(first_occurrence=deps.edge.enter("verifier_unavailable"))
            )
```

- [ ] **Step 4 : lancer → passe** ; **gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/application/run_verification_cycle.py packages/crawler/tests/application/test_run_verification_cycle.py
git commit -m "feat(application): emit verification completed/unavailable + queue depth (Plan E.2)"
```

---

## Task 8 : câblage `CrawlerApp` + bootstrap logging

**Files:**
- Modify: `packages/crawler/src/emule_indexer/composition/app.py`
- Modify: `packages/crawler/src/emule_indexer/composition/__main__.py`
- Test: `packages/crawler/tests/composition/test_app.py` (existant)

> `CrawlerApp` construit l'observabilité dans `run()` (après résolution du `node_id`), injecte `telemetry`/`edge` partout, démarre le serveur métriques **via une factory injectée** (testable sans bind), émet `CrawlerStarted`. `build_app` applique `log_level` après parsing.

- [ ] **Step 1 : étendre les tests de composition** — dans `tests/composition/test_app.py`, ajouter une factory de serveur métriques espion + asserter `CrawlerStarted` et le démarrage du serveur quand activé. Squelette :

```python
@pytest.mark.asyncio
async def test_emits_crawler_started_observer_mode(tmp_path: Path) -> None:
    started_ports: list[int] = []
    app = _app(  # helper existant qui monte une CrawlerApp avec FakeMuleClient, sans verifier_url
        tmp_path,
        metrics_server=lambda port, registry: started_ports.append(port),
        observability=ObservabilityConfig(
            log_level="INFO", metrics=MetricsConfig(enabled=True, port=9123),
            notification_timeout_seconds=5.0,
        ),
    )
    # arrêter tout de suite : poser le shutdown puis run (le pattern d'arrêt existant du fichier)
    ...
    assert started_ports == [9123]  # serveur démarré car metrics.enabled


@pytest.mark.asyncio
async def test_metrics_server_not_started_when_disabled(tmp_path: Path) -> None:
    started_ports: list[int] = []
    app = _app(tmp_path, metrics_server=lambda port, registry: started_ports.append(port),
               observability=None)
    ...
    assert started_ports == []
```

(Adapter au harnais d'arrêt déjà utilisé dans `test_app.py` — typiquement poser `app._shutdown.set()` avant/au début du `run`, ou le helper existant.)

- [ ] **Step 2 : lancer → échoue** — Expected: FAIL (`TypeError: __init__ … metrics_server`).

- [ ] **Step 3 : impl `app.py`** :

(a) Imports :

```python
from prometheus_client import CollectorRegistry, start_http_server

from emule_indexer.adapters.observability.apprise_notifier import AppriseNotifier
from emule_indexer.adapters.observability.dispatcher import ObservabilityDispatcher
from emule_indexer.adapters.observability.prometheus_sink import PrometheusSink
from emule_indexer.application.edge_state import EdgeState
from emule_indexer.domain.observability.events import CrawlerStarted
```

(b) Type + factory par défaut du serveur métriques (près des autres factories) :

```python
MetricsServer = Callable[[int, CollectorRegistry], None]


def default_metrics_server(port: int, registry: CollectorRegistry) -> None:
    """Démarre le serveur HTTP /metrics (thread daemon). Wrapper pour fixer l'ordre des args."""
    start_http_server(port, registry=registry)  # pragma: no cover
```

(c) `__init__` — nouveau paramètre injectable (après `verifier_factory`) :

```python
        metrics_server: MetricsServer = default_metrics_server,
```

et `self._metrics_server = metrics_server`.

(d) Dans `run()`, juste APRÈS `node_id = self._local_config.node_id or local_repo.node_id()`, construire l'observabilité :

```python
            obs = self._crawler_config.observability
            registry = CollectorRegistry()
            notifier = AppriseNotifier(
                tuple((target.url, target.tag) for target in self._local_config.notifications),
                node_id=node_id,
            )
            telemetry = ObservabilityDispatcher(
                metrics=PrometheusSink(registry),
                notifier=notifier,
                notify_timeout_seconds=(
                    obs.notification_timeout_seconds if obs is not None else 5.0
                ),
            )
            edge = EdgeState()
            if obs is not None and obs.metrics is not None and obs.metrics.enabled:
                self._metrics_server(obs.metrics.port, registry)
```

(e) Injecter `telemetry` dans `WorkerDeps` (le `deps = WorkerDeps(...)` existant gagne `telemetry=telemetry`).

(f) Threader `telemetry`/`edge` par **paramètres** (PAS `self._…` : `mypy --strict` refuse un attribut d'instance non déclaré dans `__init__`, et ils ne sont construits qu'en `run()`). Concrètement :
- `_run_loop` gagne `telemetry: Telemetry, edge: EdgeState` et passe `telemetry=telemetry, edge=edge` à `run_search_cycle(...)`.
- `_supervise` gagne `telemetry: Telemetry, edge: EdgeState` et les transmet à `self._run_loop(...)` (dans `group.create_task(...)`).
- L'appel `await self._supervise(...)` dans `run()` passe `telemetry=telemetry, edge=edge`.

(g) `_build_full_loops` — gagne `telemetry: Telemetry, edge: EdgeState` (paramètres), injectés dans `DownloadLoopDeps(..., telemetry=telemetry)` et `VerifyLoopDeps(..., telemetry=telemetry, edge=edge)`. L'appel `await self._build_full_loops(...)` dans `run()` passe `telemetry=telemetry, edge=edge`. (Importer `Telemetry` depuis `emule_indexer.ports.telemetry`.)

(h) Émettre `CrawlerStarted` — juste avant `async with asyncio.timeout(None)` (donc après le montage complet, mode connu) :

```python
            mode = "full" if self._local_config.verifier_url is not None else "observer"
            await telemetry.emit(CrawlerStarted(mode=mode))
```

> NOTE conversion config : `AppriseNotifier` attend `Sequence[tuple[str, Audience]]` ; `local_config.notifications` est un `tuple[NotificationTarget, ...]` → la compréhension `(t.url, t.tag)` fait le pont (E.1 a délibérément découplé l'adapter de la dataclass de config).

- [ ] **Step 4 : impl `__main__.py`** — appliquer `log_level` après parsing dans `build_app`. Après `crawler_config = parse_crawler_config(load_yaml(args.crawler))` :

```python
    if crawler_config.observability is not None:
        logging.getLogger().setLevel(crawler_config.observability.log_level)
```

(Le `basicConfig(level=INFO)` de `main()` reste le bootstrap : les erreurs de parsing AVANT ce `setLevel` se loggent en INFO ; le niveau cible s'applique ensuite. E-D2.)

- [ ] **Step 5 : lancer → passe** ; **gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/composition packages/crawler/tests/composition/test_app.py
git commit -m "feat(composition): wire observability + metrics server + CrawlerStarted (Plan E.2)"
```

---

## Task 9 : exemples de config

**Files:**
- Modify: `config/crawler.yaml`
- Modify: `config/local.example.yaml`

- [ ] **Step 1 : `config/crawler.yaml`** — ajouter une section (non secrète) :

```yaml
observability:
  log_level: INFO
  metrics:
    enabled: true
    port: 9090
  notification_timeout_seconds: 5.0
```

- [ ] **Step 2 : `config/local.example.yaml`** — documenter les notifications (secrètes), commentées :

```yaml
# Notifications apprise (E-D7) — secrets, ne JAMAIS versionner les vraies URLs.
# tag = audience : community (découvertes) / operations (santé/sécurité).
# observability:
#   notifications:
#     - { url: "discord://WEBHOOK_ID/TOKEN", tag: community }
#     - { url: "discord://WEBHOOK_ID/TOKEN", tag: operations }
```

- [ ] **Step 3 : valider que les exemples parsent** — le smoke/compose les charge déjà ; vérifier vite que `crawler.yaml` reste accepté :

```bash
( cd packages/crawler && uv run python -c "from emule_indexer.adapters.config.crawler_config import parse_crawler_config; from emule_indexer.adapters.config.yaml_loader import load_yaml; from pathlib import Path; print(parse_crawler_config(load_yaml(Path('../../config/crawler.yaml'))).observability)" )
```

Expected: affiche un `ObservabilityConfig(...)` (pas d'exception).

- [ ] **Step 4 : commit**

```bash
git add config/crawler.yaml config/local.example.yaml
git commit -m "docs(config): observability examples (Plan E.2)"
```

---

## Vérification finale E.2

- [ ] Gate complet vert (crawler **100 % branch**, verifier inchangé, ruff/mypy/sqlfluff).
- [ ] Lancer un run d'intégration orchestration si Docker dispo : `( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )` (le crawl réel émet désormais — vérifier qu'il ne casse pas).
- [ ] AUCUNE décision de crawl/download/verify n'a changé (seuls des `emit` ajoutés ; les branches métier sont identiques).
- [ ] Le crawler expose `/metrics` quand `observability.metrics.enabled` ; `CrawlerStarted` notifié au boot.

## Hors périmètre E.2 (→ E.3)

- Le **verifier** (mini-loader YAML, `/metrics`, instrumentation `/verify`, logging) → **Plan E.3**.
- L'exposition réseau de `/metrics` pour un Prometheus externe (compose/runbook) → documenté en E.3 / runbook.
