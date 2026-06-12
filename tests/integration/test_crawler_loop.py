"""Bout-en-bout léger : la boucle de crawl RÉELLE contre un amuled testcontainers (spec §8).

Run dédié : uv run pytest -m orchestration_integration --no-cov
Valide qu'un ``CrawlerApp`` réel — vrais ``AmuleEcClient`` + vraies bases SQLite — tourne
UN cycle complet contre un ``amuled`` Docker puis s'arrête PROPREMENT. Les résultats
peuvent être vides (pas d'accès réseau eD2k garanti) : c'est la BOUCLE (démarrage,
recherche, catalogage, arrêt borné) qui est validée, pas la richesse des résultats.
"""

import datetime
from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from emule_indexer.adapters.clock_asyncio import AsyncioClock, SeededRng
from emule_indexer.adapters.config.crawler_config import BackoffConfig, CrawlerConfig
from emule_indexer.adapters.config.local_config import AmuleEndpoint, LocalConfig
from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from emule_indexer.composition.app import CrawlerApp
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.matching.validation import parse_matcher_config
from emule_indexer.ports.mule_client import NetworkStatus

pytestmark = pytest.mark.orchestration_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_TARGETS = (
    TargetSegment(
        season=2,
        number=62,
        segment="A",
        title="Les demoiselles cambrioleuses",
        broadcast_date=datetime.date(2008, 9, 21),
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


class _ShutdownAfterFirstStatusClient:
    """Enveloppe un vrai client et déclenche l'arrêt après le 1er relevé de statut (1 cycle)."""

    def __init__(self, inner: object, app_holder: dict[str, CrawlerApp]) -> None:
        self._inner = inner
        self._app_holder = app_holder
        self._fired = False

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
        if not self._fired:
            self._fired = True
            self._app_holder["app"]._on_signal()
        return status  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_real_loop_runs_one_cycle_and_stops(amuled: tuple[str, int], tmp_path: Path) -> None:
    import asyncio

    from emule_indexer.adapters.mule_ec.client import AmuleEcClient

    host, port = amuled
    matcher_config = parse_matcher_config(load_yaml(_FIXTURES / "canonical_config.yaml"))
    crawler_config = CrawlerConfig(
        cycle_interval_seconds=300.0,
        search_poll_budget_seconds=10.0,
        search_poll_interval_seconds=5.0,
        keyword_pause_min_seconds=0.01,  # pauses minuscules (le test ne mesure pas le spacing)
        keyword_pause_max_seconds=0.05,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=60.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=30.0,
    )
    local_config = LocalConfig(
        amules=(AmuleEndpoint(name="amule-1", host=host, port=port, password=_EC_PASSWORD),),
        catalog_db_path=str(tmp_path / "catalog.db"),
        local_db_path=str(tmp_path / "local.db"),
        node_id=None,
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownAfterFirstStatusClient:
        inner = AmuleEcClient(endpoint.host, endpoint.port, endpoint.password, timeout=30.0)
        return _ShutdownAfterFirstStatusClient(inner, app_holder)

    app = CrawlerApp(
        crawler_config=crawler_config,
        local_config=local_config,
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=AsyncioClock(),
        rng=SeededRng(),
        signal_hub=AsyncioDecisionSignal(),
        client_factory=factory,
    )
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=120.0)
    assert (tmp_path / "catalog.db").exists()
