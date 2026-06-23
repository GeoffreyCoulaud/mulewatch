"""Tests de ``run_port_sync_cycle`` (port-sync High-ID, design §4.4/§10.5) — le cœur des branches.

Fakes injectés : ``FakePortForwardingReader`` (port vivant scripté), ``FakePortPreferences``
(get/set/network_status programmables + pannes EC injectables), ``FakeMuleRestarter`` (succès /
``RestarterError``), ``FakeClock`` (now/sleep enregistrés), ``RecordingTelemetry``, vrai
``EdgeState``. On couvre les DEUX côtés de chaque conditionnel (tableau §10.5).
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from emule_indexer.adapters.mule_ec.errors import EcConnectError, EcFailureError
from emule_indexer.application.edge_state import EdgeState
from emule_indexer.application.port_sync_loop import (
    _MISMATCH,
    PortSyncDeps,
    _PortSyncState,
    run_port_sync_cycle,
)
from emule_indexer.domain.observability.events import (
    HighIdRecovered,
    PortMismatchUnresolved,
    PortSyncTriggered,
)
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus
from emule_indexer.ports.mule_restarter import RestarterError
from tests.application.fakes import RecordingTelemetry


class FakePortForwardingReader:
    """Lecteur du port forwardé : rend un port scripté (ou ``None`` = pas prêt)."""

    def __init__(self, *, port: int | None) -> None:
        self._port = port
        self.calls = 0

    async def forwarded_port(self) -> int | None:
        self.calls += 1
        return self._port


class FakePortPreferences:
    """EC get/set/connstate programmable (sous-ensemble de AmuleEcClient).

    ``get_error``/``set_error`` injectent une panne EC sur la méthode correspondante.
    ``ed2k_high`` pilote le re-check post-restart. ``set_ports``/``status_calls`` tracent.
    """

    def __init__(
        self,
        *,
        current_port: int = 4662,
        ed2k_high: bool = True,
        get_error: Exception | None = None,
        set_error: Exception | None = None,
    ) -> None:
        self._current_port = current_port
        self._ed2k_high = ed2k_high
        self._get_error = get_error
        self._set_error = set_error
        self.set_ports: list[int] = []
        self.status_calls = 0

    async def get_listen_port(self) -> int:
        if self._get_error is not None:
            raise self._get_error
        return self._current_port

    async def set_listen_port(self, port: int) -> None:
        if self._set_error is not None:
            raise self._set_error
        self.set_ports.append(port)
        # set→get FIDÈLE : écrire la préférence change ce que get_listen_port renverra (comme le
        # vrai amuled), MÊME sans rebind. Sans ça, le fake masquerait test-gaps#0.
        self._current_port = port

    async def network_status(self) -> NetworkStatus:
        self.status_calls += 1
        return NetworkStatus(
            ed2k_id=0x02000001 if self._ed2k_high else 100,
            ed2k_high=self._ed2k_high,
            kad_status=KadStatus.CONNECTED,
        )


class FakeMuleRestarter:
    """Restarter : succès, ou lève ``RestarterError`` si ``fails=True``."""

    def __init__(self, *, fails: bool = False) -> None:
        self._fails = fails
        self.calls = 0

    async def restart(self) -> None:
        self.calls += 1
        if self._fails:
            raise RestarterError("proxy KO")


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 15, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


_POLL = 60.0
_WINDOW = 300.0


def _deps(
    *,
    reader: FakePortForwardingReader,
    ports: FakePortPreferences,
    restarter: FakeMuleRestarter | None = None,
    clock: FakeClock | None = None,
    telemetry: RecordingTelemetry | None = None,
    edge: EdgeState | None = None,
) -> PortSyncDeps:
    return PortSyncDeps(
        reader=reader,
        ports=ports,
        restarter=restarter or FakeMuleRestarter(),
        clock=clock or FakeClock(),
        telemetry=telemetry or RecordingTelemetry(),
        edge=edge or EdgeState(),
        poll_interval_seconds=_POLL,
        restart_min_interval_seconds=_WINDOW,
    )


# ---------------------------------------------------------------- port pas prêt


@pytest.mark.asyncio
async def test_port_not_ready_sleeps_without_set_or_restart() -> None:
    reader = FakePortForwardingReader(port=None)
    ports = FakePortPreferences()
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock, telemetry=telemetry)
    await run_port_sync_cycle(deps, _PortSyncState())
    assert clock.sleeps == [_POLL]
    assert ports.set_ports == []
    assert restarter.calls == 0
    assert telemetry.events == []  # aucun event, aucune alerte (Low-ID toléré)


# ---------------------------------------------------------------- port inchangé


@pytest.mark.asyncio
async def test_port_unchanged_sleeps_and_leaves_mismatch() -> None:
    reader = FakePortForwardingReader(port=4662)
    ports = FakePortPreferences(current_port=4662)
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    edge = EdgeState()
    edge.enter("port_mismatch")  # on était en alerte → le réarmement doit la lever
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock, edge=edge)
    await run_port_sync_cycle(deps, _PortSyncState())
    assert clock.sleeps == [_POLL]
    assert ports.set_ports == []
    assert restarter.calls == 0
    # réarmé : la condition n'est plus active (leave a été appelé).
    assert edge.enter("port_mismatch") is True  # re-enter rend True → elle était bien sortie


# ---------------------------------------------------------------- port changé, restart OK


@pytest.mark.asyncio
async def test_port_changed_restart_ok_high_id() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, ed2k_high=True)
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    edge = EdgeState()
    deps = _deps(
        reader=reader,
        ports=ports,
        restarter=restarter,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps, _PortSyncState())
    assert ports.set_ports == [51820]  # SetPort(N)
    assert restarter.calls == 1
    assert ports.status_calls == 1  # re-check High-ID après restart
    triggered = [e for e in telemetry.events if isinstance(e, PortSyncTriggered)]
    assert triggered == [PortSyncTriggered(old=4662, new=51820)]
    recovered = [e for e in telemetry.events if isinstance(e, HighIdRecovered)]
    assert recovered == [HighIdRecovered(port=51820)]
    # alerte réarmée (High-ID retrouvé) → re-enter rend True.
    assert edge.enter("port_mismatch") is True


@pytest.mark.asyncio
async def test_port_changed_restart_ok_but_not_high_id_alerts_without_second_restart() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, ed2k_high=False)
    restarter = FakeMuleRestarter()
    telemetry = RecordingTelemetry()
    edge = EdgeState()
    deps = _deps(reader=reader, ports=ports, restarter=restarter, telemetry=telemetry, edge=edge)
    await run_port_sync_cycle(deps, _PortSyncState())
    assert ports.set_ports == [51820]
    assert restarter.calls == 1  # PAS de 2e restart immédiat (DÉCISION 4)
    unresolved = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert len(unresolved) == 1
    assert unresolved[0].first_occurrence is True
    assert unresolved[0].live == 51820


# ---------------------------------------------------------------- restart ÉCHOUE


@pytest.mark.asyncio
async def test_port_changed_restart_fails_alerts_and_backs_off() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662)
    restarter = FakeMuleRestarter(fails=True)
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    edge = EdgeState()
    deps = _deps(
        reader=reader,
        ports=ports,
        restarter=restarter,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps, _PortSyncState())  # ne lève pas
    assert ports.set_ports == [51820]  # set appelé AVANT le restart
    assert restarter.calls == 1
    assert ports.status_calls == 0  # pas de re-check (le restart a échoué)
    unresolved = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert len(unresolved) == 1
    assert unresolved[0].first_occurrence is True
    assert unresolved[0].configured == 4662  # configured = current (le restart n'a pas pris)
    assert clock.sleeps == [_POLL]  # backoff


# ---------------------------------------------------------------- rate-limit


@pytest.mark.asyncio
async def test_rate_limit_active_skips_set_and_restart() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662)
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    state = _PortSyncState()
    state.record_restart(clock.now(), 51820)  # restart « tout juste » → too_soon True
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock)
    await run_port_sync_cycle(deps, state)
    assert ports.set_ports == []  # NI set NI restart (too_soon)
    assert restarter.calls == 0
    assert clock.sleeps == [_POLL]


@pytest.mark.asyncio
async def test_rate_limit_expired_runs_set_and_restart() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662)
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    state = _PortSyncState()
    state.record_restart(clock.now(), 51820)
    clock.advance(_WINDOW + 1)  # dernier restart > fenêtre → too_soon False
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock)
    await run_port_sync_cycle(deps, state)
    assert ports.set_ports == [51820]  # set+restart exécutés
    assert restarter.calls == 1


# ---------------------------------------------------------------- EC injoignable


@pytest.mark.asyncio
async def test_ec_error_on_get_is_absorbed_and_sleeps() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(get_error=EcConnectError("amuled down"))
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock)
    await run_port_sync_cycle(deps, _PortSyncState())  # ne lève pas
    assert ports.set_ports == []
    assert restarter.calls == 0
    assert clock.sleeps == [_POLL]


@pytest.mark.asyncio
async def test_ec_error_on_set_is_absorbed_and_sleeps() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, set_error=EcConnectError("amuled down"))
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock)
    await run_port_sync_cycle(deps, _PortSyncState())  # ne lève pas
    assert ports.set_ports == []  # set a levé avant d'enregistrer
    assert restarter.calls == 0  # restart jamais tenté (set a échoué)
    assert clock.sleeps == [_POLL]


@pytest.mark.asyncio
async def test_application_level_ec_failure_is_also_absorbed() -> None:
    # EcFailureError (EC_OP_FAILED, un MuleSearchFailedError — PAS un MuleUnreachableError) doit
    # AUSSI être absorbé : le filet catche l'ancêtre de port MuleClientError. Sans ce filet large,
    # un set_listen_port répondant EC_OP_FAILED crasherait la boucle.
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, set_error=EcFailureError("pref refusée"))
    clock = FakeClock()
    deps = _deps(reader=reader, ports=ports, clock=clock)
    await run_port_sync_cycle(deps, _PortSyncState())  # ne lève pas
    assert ports.set_ports == []
    assert clock.sleeps == [_POLL]


# ---------------------------------------------------------------- edge-trigger


@pytest.mark.asyncio
async def test_mismatch_then_recovered_rearms_the_alert() -> None:
    edge = EdgeState()
    telemetry = RecordingTelemetry()
    # Cycle 1 : restart OK mais pas High-ID → unresolved (enter → True).
    deps1 = _deps(
        reader=FakePortForwardingReader(port=51820),
        ports=FakePortPreferences(current_port=4662, ed2k_high=False),
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps1, _PortSyncState())
    first = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert first and first[0].first_occurrence is True

    telemetry.events.clear()
    # Cycle 2 : port aligné → leave réarme (la prochaine occurrence re-notifiera).
    deps2 = _deps(
        reader=FakePortForwardingReader(port=4662),
        ports=FakePortPreferences(current_port=4662),
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps2, _PortSyncState())
    assert edge.enter("port_mismatch") is True  # réarmé : la condition était bien sortie


@pytest.mark.asyncio
async def test_second_unresolved_in_a_row_is_not_first_occurrence() -> None:
    edge = EdgeState()
    telemetry = RecordingTelemetry()
    state = _PortSyncState()  # état partagé : rate-limit aux 2e+ cycles
    # Cycle 1 : restart fail → unresolved (first_occurrence True).
    deps1 = _deps(
        reader=FakePortForwardingReader(port=51820),
        ports=FakePortPreferences(current_port=4662),
        restarter=FakeMuleRestarter(fails=True),
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps1, state)
    first = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert first and first[0].first_occurrence is True

    telemetry.events.clear()
    # Cycle 2 : encore restart fail → unresolved, mais first_occurrence False (déjà actif).
    deps2 = _deps(
        reader=FakePortForwardingReader(port=51820),
        ports=FakePortPreferences(current_port=4662),
        restarter=FakeMuleRestarter(fails=True),
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps2, state)
    second = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert second and second[0].first_occurrence is False


# ---------------------------------------------------------------- test-gaps#0 : préférence ≠ bound


@pytest.mark.asyncio
async def test_failed_restart_is_not_masked_by_written_preference_next_cycle() -> None:
    # test-gaps#0 : cycle 1, le restart ÉCHOUE APRÈS set_listen_port (préférence écrite = live) ;
    # cycle 2, get_listen_port renvoie cette préférence (== live) MAIS amuled n'a jamais rebindé
    # (toujours Low-ID). L'alerte posée au cycle 1 ne doit PAS être effacée par l'égalité de
    # préférence — seul le High-ID l'efface. Sinon une panne transitoire masque le mismatch POUR
    # TOUJOURS et éteint le signal OPERATIONS.
    edge = EdgeState()
    state = _PortSyncState()
    ports = FakePortPreferences(current_port=4662, ed2k_high=False)  # set→get modélisé, Low-ID
    # Cycle 1 : 51820 != 4662 → set(51820) écrit la préférence, restart() ÉCHOUE → alerte posée.
    await run_port_sync_cycle(
        _deps(
            reader=FakePortForwardingReader(port=51820),
            ports=ports,
            restarter=FakeMuleRestarter(fails=True),
            edge=edge,
        ),
        state,
    )
    assert ports.set_ports == [51820]
    assert edge.enter(_MISMATCH) is False  # alerte bien active après le restart raté (reste active)

    # Cycle 2 : live=51820, get_listen_port()==51820 (la préférence du cycle 1), mais Low-ID.
    await run_port_sync_cycle(
        _deps(
            reader=FakePortForwardingReader(port=51820),
            ports=ports,  # MÊME fake → current_port == 51820 (set→get)
            restarter=FakeMuleRestarter(),
            edge=edge,
        ),
        state,
    )
    # l'alerte est MAINTENUE (port toujours faux, pas de High-ID) : re-enter rend encore False.
    assert edge.enter(_MISMATCH) is False
