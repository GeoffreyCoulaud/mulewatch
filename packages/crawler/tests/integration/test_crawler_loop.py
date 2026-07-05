"""Lightweight end-to-end: the REAL crawl loop against a testcontainers amuled (spec §8).

Dedicated run: uv run pytest -m orchestration_integration --no-cov
Validates that a real ``CrawlerApp`` — real ``AmuleEcClient`` + real SQLite DBs — runs
ONE full cycle against a Docker ``amuled`` then stops CLEANLY. The results may be
empty (no guaranteed eD2k network access): it is the LOOP (startup, search, cataloging,
bounded shutdown) that is validated, not the richness of the results.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from catalog_matching.models import TargetSegment
from catalog_matching.validation import parse_matcher_config
from mulewatch.adapters.clock_asyncio import AsyncioClock, SeededRng
from mulewatch.adapters.config.crawler_config import (
    AmuleEndpoint,
    BackoffConfig,
    CrawlerConfig,
)
from mulewatch.adapters.config.yaml_loader import load_yaml
from mulewatch.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from mulewatch.adapters.persistence_sqlite.connection import open_local
from mulewatch.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from mulewatch.composition.app import CrawlerApp
from mulewatch.ports.mule_client import NetworkStatus

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
    """Wraps a real client and triggers shutdown on the 2nd cycle's status poll.

    We do NOT trigger on the 1st poll (start of the 1st cycle): a shutdown set during the status
    poll cancels the in-flight cycle BEFORE its final ``write_cycle_state``, and the index would
    never advance (verified empirically). So we let the 1st cycle COMPLETE (it writes
    ``cycle_index=1``), then we trigger shutdown on the 2nd cycle's 1st poll — the index stays at
    1, proof that a full cycle actually ran. The ``cycle_interval`` is tiny → the 2nd cycle starts
    right after the 1st (the run stays bounded, well under the 120 s ``wait_for``)."""

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
        if self._status_calls == 2:  # 1st poll of the 2nd cycle (the 1st cycle wrote its index)
            self._app_holder["app"]._on_signal()
        return status  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_real_loop_runs_one_cycle_and_stops(amuled: tuple[str, int], tmp_path: Path) -> None:
    import asyncio

    from mulewatch.adapters.mule_ec.client import AmuleEcClient

    host, port = amuled
    matcher_config = parse_matcher_config(load_yaml(_MATCHER))
    crawler_config = CrawlerConfig(
        # Tiny interval: the 2nd cycle starts right after the 1st (which wrote its index)
        # → the shutdown at the 2nd cycle's poll bounds the run, well under wait_for 120 s.
        cycle_interval_seconds=0.05,
        # TINY polling budget/interval: against a real amuled, search_progress of Kad
        # searches does not reach 100% → without this, EVERY Kad task would burn the whole
        # budget (10 s) and the 1st cycle would exceed the shutdown_deadline. Here the 1st cycle
        # completes in ~1 s → it writes its index BEFORE the shutdown (2nd poll) is set.
        search_poll_budget_seconds=0.2,
        search_poll_interval_seconds=0.05,
        keyword_pause_min_seconds=0.01,  # tiny pauses (the test does not measure spacing)
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
        policy_fingerprint="test-policy-fingerprint",
        client_factory=factory,
    )
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=120.0)
    # catalog.db AND local.db exist (open_catalog/open_local create them), BUT above all the
    # cycle COMPLETED: the cycle index advanced (write_cycle_state(cycle_index+1, …) only runs
    # at the END of a cycle). Without this assertion, the test passed before any cycle even ran.
    assert (tmp_path / "catalog.db").exists()
    assert (tmp_path / "local.db").exists()
    local_conn = open_local(Path(crawler_config.local_db_path))
    try:
        scheduler_state = SqliteSchedulerStateRepository(local_conn)
        assert scheduler_state.read_cycle_index() >= 1  # a full cycle advanced the index
    finally:
        local_conn.close()
