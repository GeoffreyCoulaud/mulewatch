"""Faux objets déterministes pour les tests de la couche application (spec §8).

``FakeMuleClient`` : résultats SCRIPTÉS par appel de ``fetch_results``, pannes injectables
(``MuleUnreachableError``/``MuleSearchFailedError``) à ``connect``/``start_search``.
``FakeClock`` : horloge avançable (``advance`` sans I/O) + ``sleep`` qui avance SANS attente
réelle (déterminisme). ``FakeRng`` : shuffle identité + jitter FIXE (déterminisme).
``RecordingSignal`` : capture les sujets nudgés. Les repos sont les VRAIS repos SQLite
(spec §8 : « vrais repos sur tmp_path ») — pas de faux ici.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from emule_indexer.domain.observability.events import Event
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import (
    KadStatus,
    MuleSearchFailedError,
    MuleUnreachableError,
    NetworkStatus,
    SearchChannel,
)


class FakeClock:
    """Horloge fausse avançable + sleep instantané (avance le now, déterministe)."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 6, 12, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        """Avance l'horloge SANS dormir (pour faire passer un ``retry_after`` en test)."""
        self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)  # cède la main sans attente réelle


class FakeRng:
    """Rng faux DÉTERMINISTE : shuffle identité + jitter constant (``jitter_value``).

    Le shuffle conserve l'ordre (pas de dépendance au seed dans les tests). ``jitter`` rend
    ``jitter_value`` (0.0 par défaut → backoff/pause = valeur NOMINALE exacte), mais respecte
    le CONTRAT du port comme le vrai ``SeededRng`` : ``span <= 0`` → ``0.0`` (sinon le test
    de pause min==max mentirait sur le comportement réel)."""

    def __init__(self, *, jitter_value: float = 0.0) -> None:
        self._jitter_value = jitter_value
        self.jitter_spans: list[float] = []

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        self.jitter_spans.append(span)
        if span <= 0:
            return 0.0
        return self._jitter_value


class RecordingTelemetry:
    """Telemetry faux : capture les événements émis (le test asserte la séquence)."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


class RecordingSignal:
    """Hub de nudge qui ENREGISTRE les sujets signalés (le test inspecte/await)."""

    def __init__(self) -> None:
        self.signalled: list[str] = []
        self._events: dict[str, asyncio.Event] = {}

    def signal(self, subject: str) -> None:
        self.signalled.append(subject)
        self._events.setdefault(subject, asyncio.Event()).set()

    async def wait(self, subject: str) -> None:
        event = self._events.setdefault(subject, asyncio.Event())
        await event.wait()
        event.clear()


class FakeMuleClient:
    """Client EC scripté (satisfait MuleClient structurellement, spec §8).

    ``results`` : liste de tuples d'observations, un par appel de ``fetch_results``
    (épuisée → tuple vide). ``connect_failures`` : exceptions à lever aux N premiers
    ``connect`` (puis succès). ``search_failures`` : exceptions à lever aux N premiers
    ``start_search`` (puis succès). ``status`` : le ``NetworkStatus`` renvoyé.
    """

    def __init__(
        self,
        *,
        results: list[tuple[FileObservation, ...]] | None = None,
        connect_failures: list[Exception] | None = None,
        search_failures: list[Exception] | None = None,
        status: NetworkStatus | None = None,
    ) -> None:
        self._results = list(results or [])
        self._connect_failures = list(connect_failures or [])
        self._search_failures = list(search_failures or [])
        self._status = status or NetworkStatus(
            ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED
        )
        self.connect_calls = 0
        self.close_calls = 0
        self.searches: list[tuple[str, SearchChannel]] = []
        self.fetch_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_failures:
            raise self._connect_failures.pop(0)

    async def close(self) -> None:
        self.close_calls += 1

    async def start_search(self, keyword: str, channel: SearchChannel) -> None:
        self.searches.append((keyword, channel))
        if self._search_failures:
            raise self._search_failures.pop(0)

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        self.fetch_calls += 1
        if not self._results:
            return ()
        return self._results.pop(0)

    async def stop_search(self) -> None:
        return None

    async def search_progress(self) -> int | None:
        return 100  # « terminé » : le polling s'arrête tout de suite (déterminisme)

    async def network_status(self) -> NetworkStatus:
        return self._status


class UnreachableStatusClient(FakeMuleClient):
    """Variante dont ``network_status`` lève ``MuleUnreachableError`` (instance injoignable).

    Modélise le vrai adapter EC : un client non connecté lève ``EcConnectError`` (qui EST un
    ``MuleUnreachableError``) au relevé de statut. Sert à couvrir la branche tolérante de
    ``_aggregate_coverage`` (instance injoignable → non search-capable, pas de crash)."""

    async def network_status(self) -> NetworkStatus:
        raise MuleUnreachableError("client EC non connecté (instance injoignable)")


def make_unreachable(message: str = "down") -> MuleUnreachableError:
    return MuleUnreachableError(message)


def make_search_failed(message: str = "EC_OP_FAILED") -> MuleSearchFailedError:
    return MuleSearchFailedError(message)
