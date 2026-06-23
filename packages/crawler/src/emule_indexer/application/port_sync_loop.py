"""La boucle port-sync UNIFIÉE (boot + mid-life) : lit le port forwardé, aligne amuled, restart.

Couche APPLICATION (port-sync High-ID, design §4). UN seul algorithme couvre « le port est faux
au démarrage » ET « le port est devenu faux en cours de route » (renégo VPN) : on lit le port
forwardé vivant (gluetun), on compare au port d'écoute d'amuled (EC), et si ça diffère on
``SetPort`` + restart le conteneur (le port n'est PAS re-bindable à chaud). Garde-fous : rate-limit
des restarts (≤ 1 / fenêtre) ; re-check High-ID après restart SANS boucler ; alerte de repli
edge-triggered (OPERATIONS) quand le port reste faux. Le mode dégradé (Low-ID) est toléré : tout
parsing défensif (port 0 / control-server injoignable / EC mort) → « pas prêt », backoff.

``run_port_sync_cycle`` NE LÈVE JAMAIS (filet top-level comme ``run_verification_cycle``) ; tout
chemin re-bouclant dort ``poll_interval_seconds`` (pas de busy-spin). ``port_sync_loop`` répète
jusqu'à l'arrêt (pattern ``verification_loop``). Comme ``VerificationTaskQueue``, on déclare des
Protocols NARROW locaux (le vrai ``AmuleEcClient`` ET un fake minimal les satisfont) — on n'élargit
PAS ``ports/mule_client.py``.
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
    """Sous-ensemble de ``MuleClient`` consommé par la boucle (typage local, design §4.2).

    Le vrai ``AmuleEcClient`` (get/set_listen_port nouvelles, network_status existante) ET un fake
    minimal le satisfont. Stubs sur UNE ligne.
    """

    async def get_listen_port(self) -> int: ...

    async def set_listen_port(self, port: int) -> None: ...

    async def network_status(self) -> NetworkStatus: ...


@dataclass
class PortSyncDeps:
    """Dépendances d'un cycle port-sync (la composition les assemble une fois, design §4.3)."""

    reader: PortForwardingReader  # lit le port forwardé vivant (gluetun)
    ports: PortPreferences  # EC get/set/connstate (AmuleEcClient, connexion dédiée R6)
    restarter: MuleRestarter  # restart amuled via le proxy
    clock: Clock  # sleep/now injectés (déterminisme)
    telemetry: Telemetry  # events d'observabilité
    edge: EdgeState  # alerte edge-triggered (port mismatch non corrigé)
    poll_interval_seconds: float  # cadence du poll
    restart_min_interval_seconds: float  # rate-limit des restarts (≤ 1 / fenêtre)


class _PortSyncState:
    """État inter-itérations (mutable, mono-thread sur l'event loop, NON persisté — comme
    ``EdgeState``). Mémorise l'instant du dernier restart (rate-limit) et le port visé."""

    def __init__(self) -> None:
        self._last_restart: datetime | None = None
        self._last_target: int | None = None

    def too_soon(self, now: datetime, window_seconds: float) -> bool:
        """``True`` si un restart a eu lieu il y a moins de ``window_seconds`` (rate-limit)."""
        if self._last_restart is None:
            return False
        return (now - self._last_restart).total_seconds() < window_seconds

    def record_restart(self, now: datetime, target: int) -> None:
        """Enregistre l'instant + le port visé du restart (rate-limit + cible)."""
        self._last_restart = now
        self._last_target = target


async def run_port_sync_cycle(deps: PortSyncDeps, state: _PortSyncState) -> None:
    """UN cycle (design §4.4). NE LÈVE JAMAIS ; tout chemin re-bouclant dort ``poll_interval``.

    Boot vs mid-life = MÊME chemin : au 1er cycle ``current`` est le port codé en dur de l'image ;
    si ``live`` diffère, on ``SetPort`` + restart une fois puis on re-vérifie High-ID. Aux cycles
    suivants, idem en cas de renégo VPN. Pas de branche « première fois ».
    """
    try:
        live = await deps.reader.forwarded_port()
        if live is None:
            # control-server pas prêt / PF non négocié → on reste Low-ID, PAS d'alerte.
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        current = await deps.ports.get_listen_port()
        if live == current:
            # La préférence est alignée sur le port forwardé — mais ce n'est PAS une preuve
            # qu'amuled ÉCOUTE ce port : ``set_listen_port`` écrit la préférence sans rebind (le
            # rebind exige un restart). L'EC n'expose pas le port réellement bound ; le seul signal
            # fiable que le bon port est bindé ET joignable est le High-ID. On n'efface donc
            # l'alerte QUE si High-ID ; sinon on backoff sans y toucher — un restart raté garde son
            # alerte allumée au lieu d'être masqué par la préférence écrite (test-gaps#0). Low-ID
            # toléré : pas de re-restart (le rate-limit/alerte gèrent la reprise).
            status = await deps.ports.network_status()
            if status.ed2k_high:
                deps.edge.leave(_MISMATCH)
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        # --- divergence : live != current, et live > 0 garanti ---
        now = deps.clock.now()
        if state.too_soon(now, deps.restart_min_interval_seconds):
            # rate-limit : restart récent → on attend (ne pas boucler les restarts).
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        await deps.ports.set_listen_port(live)
        await deps.telemetry.emit(PortSyncTriggered(old=current, new=live))
        try:
            await deps.restarter.restart()
        except RestarterError as error:
            # restart impossible → alerte edge-triggered + backoff.
            _logger.warning("restart d'amuled impossible (%s) — alerte + backoff", error)
            await deps.telemetry.emit(
                PortMismatchUnresolved(
                    first_occurrence=deps.edge.enter(_MISMATCH), live=live, configured=current
                )
            )
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        state.record_restart(now, live)
        # --- re-check High-ID après restart (DÉCISION 4) : NE PAS BOUCLER si pas High-ID ---
        # on laisse un délai borné (amuled rebind) puis on lit le connstate ; si ed2k_high est
        # False, on émet l'alerte et on rend — le rate-limit empêche un re-restart immédiat.
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
        # get/set_listen_port / network_status en échec (amuled down / EC mort / EC_OP_FAILED) →
        # toléré (le spec catche le base ``EcError`` ; côté application on catche son ANCÊTRE de
        # port ``MuleClientError`` — qui couvre injoignable ET échec applicatif — sans importer
        # l'adapter, règle de dépendance §4). Backoff, pas de crash (filet top-level §4.4).
        _logger.warning("EC en échec pendant le port-sync (%s) — toléré, backoff", error)
        await deps.clock.sleep(deps.poll_interval_seconds)


@dataclass
class PortSyncLoopDeps(PortSyncDeps):
    """``PortSyncDeps`` + l'arrêt (pattern ``verification_loop``). Pas de nudge : le poll suffit."""

    shutdown: asyncio.Event


async def port_sync_loop(deps: PortSyncLoopDeps) -> None:
    """Répète ``run_port_sync_cycle`` jusqu'à l'arrêt (design §4.5, pattern ``verification_loop``).

    Câblée par ``CrawlerApp`` dans le ``TaskGroup`` ; l'annulation (arrêt) atterrit au prochain
    ``await`` (poll/EC/sleep). ``run_port_sync_cycle`` NE LÈVE JAMAIS → cette boucle ne peut pas
    crasher le ``TaskGroup``. Le ``if deps.shutdown.is_set(): break`` post-cycle évite un cycle de
    plus quand l'arrêt est demandé PENDANT le cycle.
    """
    state = _PortSyncState()
    while not deps.shutdown.is_set():
        await run_port_sync_cycle(deps, state)
        if deps.shutdown.is_set():
            break
