"""The UNIFIED port-sync loop (boot + mid-life): read the forwarded port, align amuled, restart.

APPLICATION layer (High-ID port-sync, design §4). ONE algorithm covers both "the port is wrong
at startup" AND "the port became wrong along the way" (VPN renegotiation): we read the live
forwarded port (gluetun), compare it to amuled's listen port (EC), and if they differ we
``SetPort`` + restart the container (the port is NOT re-bindable at runtime). Guards: restart
rate-limit (≤ 1 / window); High-ID re-check after restart WITHOUT looping; edge-triggered
fallback alert (OPERATIONS) when the port stays wrong. The degraded mode (Low-ID) is tolerated:
any defensive parse (port 0 / control-server unreachable / EC dead) → "not ready", backoff.

``run_port_sync_cycle`` NEVER RAISES (top-level net like ``run_verification_cycle``); every
re-looping path sleeps ``poll_interval_seconds`` (no busy-spin). ``port_sync_loop`` repeats
until shutdown (``verification_loop`` pattern). Like ``VerificationTaskQueue``, we declare local
NARROW Protocols (the real ``AmuleEcClient`` AND a minimal fake satisfy them) — we do NOT widen
``ports/mule_client.py``.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from emule_indexer.application.edge_state import EdgeState
from emule_indexer.domain.observability.events import (
    HighIdRecovered,
    PortMismatchUnresolved,
    PortSyncTriggered,
)
from emule_indexer.ports.clock import Clock
from emule_indexer.ports.mule_client import MuleClientError, NetworkStatus
from emule_indexer.ports.mule_restarter import MuleRestarter, RestarterError
from emule_indexer.ports.port_forwarding import PortForwardingReader
from emule_indexer.ports.telemetry import Telemetry

_logger = logging.getLogger("emule_indexer.application.port_sync_loop")

_MISMATCH = "port_mismatch"


class PortPreferences(Protocol):
    """Subset of ``MuleClient`` consumed by the loop (local typing, design §4.2).

    The real ``AmuleEcClient`` (connect, new get/set_listen_port, existing network_status) AND a
    minimal fake satisfy it. Stubs on ONE line.
    """

    async def connect(self) -> None: ...

    async def get_listen_port(self) -> int: ...

    async def set_listen_port(self, port: int) -> None: ...

    async def network_status(self) -> NetworkStatus: ...


@dataclass
class PortSyncDeps:
    """Dependencies of a port-sync cycle (composition assembles them once, design §4.3)."""

    reader: PortForwardingReader  # reads the live forwarded port (gluetun)
    ports: PortPreferences  # EC get/set/connstate (AmuleEcClient, dedicated connection R6)
    restarter: MuleRestarter  # restart amuled via the proxy
    clock: Clock  # injected sleep/now (determinism)
    telemetry: Telemetry  # observability events
    edge: EdgeState  # edge-triggered alert (uncorrected port mismatch)
    poll_interval_seconds: float  # poll cadence
    restart_min_interval_seconds: float  # restart rate-limit (≤ 1 / window)


class _PortSyncState:
    """Inter-iteration state (mutable, single-threaded on the event loop, NOT persisted — like
    ``EdgeState``). Remembers the last restart's instant (rate-limit) and the target port."""

    def __init__(self) -> None:
        self._last_restart: datetime | None = None
        self._last_target: int | None = None

    def too_soon(self, now: datetime, window_seconds: float) -> bool:
        """``True`` if a restart happened less than ``window_seconds`` ago (rate-limit)."""
        if self._last_restart is None:
            return False
        return (now - self._last_restart).total_seconds() < window_seconds

    def record_restart(self, now: datetime, target: int) -> None:
        """Records the instant + target port of the restart (rate-limit + target)."""
        self._last_restart = now
        self._last_target = target


async def run_port_sync_cycle(deps: PortSyncDeps, state: _PortSyncState) -> None:
    """ONE cycle (design §4.4). NEVER RAISES; every re-looping path sleeps ``poll_interval``.

    Boot vs mid-life = SAME path: on the 1st cycle ``current`` is the image's hardcoded port;
    if ``live`` differs, we ``SetPort`` + restart once then re-check High-ID. On later cycles,
    same in case of VPN renegotiation. No "first time" branch.
    """
    try:
        live = await deps.reader.forwarded_port()
        if live is None:
            # control-server not ready / PF not negotiated → we stay Low-ID, NO alert.
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        # (Re)connect the dedicated EC client BEFORE any EC op. IDEMPOTENT (AmuleEcClient.connect
        # is a no-op when already connected), but ESSENTIAL after a restart: our own restart() — or
        # a VPN renegotiation — kills the connection, and the client self-heals by nulling its
        # transport on the next failed read. Without this call the loop would stay stuck "EC client
        # not connected" forever (the field deadlock). A failed reconnect (amuled still down) raises
        # under ``MuleClientError`` → absorbed + backoff below, like any other EC failure.
        await deps.ports.connect()
        current = await deps.ports.get_listen_port()
        if live == current:
            # The preference is aligned with the forwarded port — but this is NOT proof that
            # amuled LISTENS on that port: ``set_listen_port`` writes the preference without
            # rebinding (the rebind requires a restart). EC does not expose the actually-bound
            # port; the only reliable signal that the right port is bound AND reachable is the
            # High-ID. So we clear the alert ONLY if High-ID; otherwise we backoff without
            # touching it — a failed restart keeps its alert lit instead of being masked by the
            # written preference (test-gaps#0). Low-ID tolerated: no re-restart (the
            # rate-limit/alert handle recovery).
            status = await deps.ports.network_status()
            if status.ed2k_high:
                deps.edge.leave(_MISMATCH)
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        # --- divergence: live != current, and live > 0 guaranteed ---
        now = deps.clock.now()
        if state.too_soon(now, deps.restart_min_interval_seconds):
            # rate-limit: recent restart → we wait (don't loop restarts).
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        await deps.ports.set_listen_port(live)
        await deps.telemetry.emit(PortSyncTriggered(old=current, new=live))
        try:
            await deps.restarter.restart()
        except RestarterError as error:
            # restart impossible → edge-triggered alert + backoff.
            _logger.warning("amuled restart failed (%s) — alert + backoff", error)
            await deps.telemetry.emit(
                PortMismatchUnresolved(
                    first_occurrence=deps.edge.enter(_MISMATCH), live=live, configured=current
                )
            )
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        state.record_restart(now, live)
        # --- re-check High-ID after restart (DECISION 4): DO NOT LOOP if not High-ID ---
        # we allow a bounded delay (amuled rebind) then read the connstate; if ed2k_high is
        # False, we emit the alert and return — the rate-limit prevents an immediate re-restart.
        await deps.clock.sleep(deps.poll_interval_seconds)
        status = await deps.ports.network_status()
        if status.ed2k_high:
            deps.edge.leave(_MISMATCH)
            await deps.telemetry.emit(HighIdRecovered(port=live))
        else:
            await deps.telemetry.emit(
                PortMismatchUnresolved(
                    first_occurrence=deps.edge.enter(_MISMATCH), live=live, configured=live
                )
            )
    except MuleClientError as error:
        # get/set_listen_port / network_status failed (amuled down / EC dead / EC_OP_FAILED) →
        # tolerated (the spec catches the base ``EcError``; on the application side we catch its
        # port ANCESTOR ``MuleClientError`` — which covers unreachable AND application failure —
        # without importing the adapter, dependency rule §4). Backoff, no crash (top-level net
        # §4.4).
        _logger.warning("EC failed during port-sync (%s) — tolerated, backoff", error)
        await deps.clock.sleep(deps.poll_interval_seconds)


@dataclass
class PortSyncLoopDeps(PortSyncDeps):
    """``PortSyncDeps`` + shutdown (``verification_loop`` pattern). No nudge: the poll is enough."""

    shutdown: asyncio.Event


async def port_sync_loop(deps: PortSyncLoopDeps) -> None:
    """Repeats ``run_port_sync_cycle`` until shutdown (design §4.5, ``verification_loop`` pattern).

    Wired by ``CrawlerApp`` into the ``TaskGroup``; cancellation (shutdown) lands at the next
    ``await`` (poll/EC/sleep). ``run_port_sync_cycle`` NEVER RAISES → this loop cannot crash the
    ``TaskGroup``. The post-cycle ``if deps.shutdown.is_set(): break`` avoids one extra cycle when
    shutdown is requested DURING the cycle.
    """
    state = _PortSyncState()
    while not deps.shutdown.is_set():
        await run_port_sync_cycle(deps, state)
        if deps.shutdown.is_set():
            break
