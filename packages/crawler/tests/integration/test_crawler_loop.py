"""Bout-en-bout léger : la boucle de crawl RÉELLE contre un amuled testcontainers (spec §8).

Run dédié : uv run pytest -m orchestration_integration --no-cov
Valide qu'un ``CrawlerApp`` réel — vrais ``AmuleEcClient`` + vraies bases SQLite — tourne
UN cycle complet contre un ``amuled`` Docker puis s'arrête PROPREMENT. Les résultats
peuvent être vides (pas d'accès réseau eD2k garanti) : c'est la BOUCLE (démarrage,
recherche, catalogage, arrêt borné) qui est validée, pas la richesse des résultats.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from catalog_matching.models import TargetSegment
from catalog_matching.validation import parse_matcher_config
from emule_indexer.adapters.clock_asyncio import AsyncioClock, SeededRng
from emule_indexer.adapters.config.crawler_config import (
    AmuleEndpoint,
    BackoffConfig,
    CrawlerConfig,
)
from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from emule_indexer.adapters.persistence_sqlite.connection import open_local
from emule_indexer.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from emule_indexer.composition.app import CrawlerApp
from emule_indexer.ports.mule_client import NetworkStatus

pytestmark = pytest.mark.orchestration_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"
_MATCHER = Path(__file__).resolve().parents[4] / "deploy" / "config" / "crawler" / "matcher.yml"
_TARGETS = (
    TargetSegment(
        season=2,
        seasonal_number=11,
        absolute_number=62,
        segment="A",
        title="Les demoiselles cambrioleuses",
    ),
)


@pytest.fixture(scope="module")
def amuled() -> Iterator[tuple[str, int]]:
    ready = LogMessageWaitStrategy(r"listening on 0\.0\.0\.0:4712").with_startup_timeout(180)
    container = (
        DockerContainer(_IMAGE)
        .with_env("GUI_PWD", _EC_PASSWORD)
        .with_exposed_ports(4712)
        .waiting_for(ready)
    )
    try:
        container.start()
        yield container.get_container_host_ip(), int(container.get_exposed_port(4712))
    finally:
        container.stop()


class _ShutdownAfterFirstCycleClient:
    """Enveloppe un vrai client et déclenche l'arrêt au relevé de statut du 2e cycle.

    On NE déclenche PAS au 1er relevé (début du 1er cycle) : l'arrêt posé pendant le coverage
    annule le cycle en vol AVANT son ``write_cycle_state`` final, l'index n'avancerait jamais
    (vérifié empiriquement). On laisse donc le 1er cycle COMPLÉTER (il écrit ``cycle_index=1``),
    puis on déclenche l'arrêt au 1er relevé du 2e cycle — l'index reste à 1, preuve qu'un cycle
    complet a bien tourné. Le ``cycle_interval`` est minuscule → le 2e cycle démarre tout de
    suite après le 1er (le run reste borné, bien sous le ``wait_for`` de 120 s)."""

    def __init__(self, inner: object, app_holder: dict[str, CrawlerApp]) -> None:
        self._inner = inner
        self._app_holder = app_holder
        self._status_calls = 0

    async def connect(self) -> None:
        await self._inner.connect()  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self._inner.close()  # type: ignore[attr-defined]

    async def start_search(self, keyword: str, channel: object) -> None:
        await self._inner.start_search(keyword, channel)  # type: ignore[attr-defined]

    async def fetch_results(self) -> tuple:  # type: ignore[type-arg]
        return await self._inner.fetch_results()  # type: ignore[attr-defined,no-any-return]

    async def stop_search(self) -> None:
        await self._inner.stop_search()  # type: ignore[attr-defined]

    async def search_progress(self) -> int | None:
        return await self._inner.search_progress()  # type: ignore[attr-defined,no-any-return]

    async def network_status(self) -> NetworkStatus:
        status = await self._inner.network_status()  # type: ignore[attr-defined]
        self._status_calls += 1
        if self._status_calls == 2:  # 1er relevé du 2e cycle (le 1er cycle a écrit son index)
            self._app_holder["app"]._on_signal()
        return status  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_real_loop_runs_one_cycle_and_stops(amuled: tuple[str, int], tmp_path: Path) -> None:
    import asyncio

    from emule_indexer.adapters.mule_ec.client import AmuleEcClient

    host, port = amuled
    matcher_config = parse_matcher_config(load_yaml(_MATCHER))
    crawler_config = CrawlerConfig(
        # Intervalle minuscule : le 2e cycle démarre juste après le 1er (qui a écrit son index)
        # → l'arrêt déclenché au coverage du 2e cycle borne le run, bien sous le wait_for 120 s.
        cycle_interval_seconds=0.05,
        # Budget/intervalle de polling MINUSCULES : contre un vrai amuled, search_progress des
        # recherches Kad n'atteint pas 100 % → sans cela, CHAQUE tâche Kad brûlerait tout le
        # budget (10 s) et le 1er cycle dépasserait le shutdown_deadline. Ici le 1er cycle
        # complète en ~1 s → il écrit son index AVANT que l'arrêt (2e coverage) ne soit posé.
        search_poll_budget_seconds=0.2,
        search_poll_interval_seconds=0.05,
        keyword_pause_min_seconds=0.01,  # pauses minuscules (le test ne mesure pas le spacing)
        keyword_pause_max_seconds=0.05,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=60.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=30.0,
        amules=(AmuleEndpoint(name="amule-1", host=host, port=port, password=_EC_PASSWORD),),
        catalog_db_path=str(tmp_path / "catalog.db"),
        local_db_path=str(tmp_path / "local.db"),
        node_id=None,
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownAfterFirstCycleClient:
        inner = AmuleEcClient(endpoint.host, endpoint.port, endpoint.password, timeout=30.0)
        return _ShutdownAfterFirstCycleClient(inner, app_holder)

    app = CrawlerApp(
        crawler_config=crawler_config,
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=AsyncioClock(),
        rng=SeededRng(),
        signal_hub=AsyncioDecisionSignal(),
        client_factory=factory,
    )
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=120.0)
    # catalog.db ET local.db existent (open_catalog/open_local les créent), MAIS surtout le
    # cycle a COMPLÉTÉ : l'index de cycle a avancé (write_cycle_state(cycle_index+1, …) ne tourne
    # qu'en FIN de cycle). Sans cette assertion, le test passait avant même qu'un cycle tourne.
    assert (tmp_path / "catalog.db").exists()
    assert (tmp_path / "local.db").exists()
    local_conn = open_local(Path(crawler_config.local_db_path))
    try:
        scheduler_state = SqliteSchedulerStateRepository(local_conn)
        assert scheduler_state.read_cycle_index() >= 1  # un cycle complet a avancé l'index
    finally:
        local_conn.close()
