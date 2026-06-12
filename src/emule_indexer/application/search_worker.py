"""Travailleur de recherche : possède 1 ``MuleClient``, draine la queue (spec §4).

Couche APPLICATION. Un travailleur par instance ``amuled`` (spec §3 : N travailleurs = N
connexions EC = parallélisme réel ; dégénère en boucle séquentielle à N=1). Par item
``(keyword, channel)`` tiré de la queue partagée :

  consulte le backoff (SAUTE l'item si l'instance OU le canal est en backoff jusqu'à son
  ``retry_after``) → assure la connexion (reconnexion par instance si down) →
  ``start_search`` → polling borné (budget config) → ``fetch_results`` →
  ``record_observation`` pour CHAQUE obs.

Gestion d'erreurs (spec §7, « le client signale, le plan C décide ») — l'application ne
catch QUE des exceptions de PORT (jamais d'un adapter, règle de dépendance §4) :
- ``MuleUnreachableError`` (flux mort : connexion/timeout/trame illisible côté EC) →
  instance DOWN : on jette le client, BACKOFF de reconnexion PAR INSTANCE (``retry_after``
  posé) ; les autres travailleurs continuent ; l'item est ABANDONNÉ.
- ``MuleSearchFailedError`` (échec applicatif d'un canal) → BACKOFF PAR (instance, canal).
- ``RepositoryError`` sur une obs → loggée et comptée par ``record_observations``.

Le backoff est exponentiel + jitter (spec §3), MÉMORISÉ dans un ``BackoffRegistry`` PARTAGÉ
(une seule instance pour TOUS les travailleurs + le cycle) et PERSISTÉ dans
``scheduler_state`` en fin de cycle (spec §3/§7 : il survit à un redémarrage). « Skip jusqu'à
``retry_after`` » remplace l'ancien « sleep du délai » : un canal en backoff est sauté, pas
attendu — l'event loop reste disponible. Les mutations du registre partagé se font entre
deux ``await`` (event loop mono-thread, writer unique) → aucun verrou nécessaire (spec §3).
Le travailleur ne FERME jamais le client (ownership = composition root, §6).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from emule_indexer.application.record_observations import record_observation
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.search.backoff import backoff_delay
from emule_indexer.ports.catalog_repository import CatalogRepository
from emule_indexer.ports.clock import Clock, Rng
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.mule_client import (
    MuleClient,
    MuleSearchFailedError,
    MuleUnreachableError,
    SearchChannel,
)
from emule_indexer.ports.scheduler_state_repository import ChannelBackoff

_logger = logging.getLogger("emule_indexer.application.search_worker")

_PROGRESS_DONE = 100  # search_progress() à 100 % → on cesse de poller (handoff EC)


def _iso(moment: datetime) -> str:
    """ISO-8601 UTC à largeur fixe (microsecondes TOUJOURS écrites) — mêmes règles que
    ``utc_iso`` de l'adapter (qu'on ne peut pas importer : règle de dépendance §4) pour que
    la comparaison ``now < retry_after`` soit lexicographique == chronologique, et que le
    format PERSISTÉ soit identique à celui des autres timestamps."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SearchTask:
    """Une unité de travail : un mot-clé sur un canal (spec §4)."""

    keyword: str
    channel: SearchChannel


@dataclass(frozen=True)
class WorkerPolicy:
    """Paramètres de politique d'un travailleur, en PRIMITIFS (spec §5 ; injectés par compo).

    ``backoff_jitter_ratio`` : fraction du délai nominal tirée en jitter additionnel
    (anti-thundering-herd, spec §3) — p.ex. 0.3 ⇒ jitter dans ``[0, 0.3 * délai)``.
    ``keyword_pause_min_seconds``/``keyword_pause_max_seconds`` : bornes (min ≤ max) de la
    PAUSE JITTERÉE inter-mots-clés (spec §5/§7, anti-rate-limit eD2k) — un délai
    ``min + rng.jitter(max - min)`` est dormi ENTRE deux items d'un même travailleur.
    """

    backoff_base_seconds: float
    backoff_cap_seconds: float
    backoff_factor: float
    backoff_jitter_ratio: float
    poll_budget_seconds: float
    poll_interval_seconds: float
    keyword_pause_min_seconds: float
    keyword_pause_max_seconds: float


class BackoffRegistry:
    """Registre de backoff PARTAGÉ par clé (instance, ou « instance:canal »), PERSISTABLE.

    Tient une map ``clé → ChannelBackoff(attempts, retry_after)`` (spec §3/§7). ``retry_after``
    est calculé à l'échec : ``clock.now() + backoff_delay(attempts) + jitter`` (jitter tiré du
    port ``Rng``, déterministe en test) → ISO-8601 UTC à largeur fixe (comparaison
    lexicographique == chronologique). ``is_in_backoff`` saute une clé tant que
    ``now < retry_after``. ``snapshot``/``load_from`` font le pont avec ``scheduler_state``
    (la persistance survit à un redémarrage). Logique déterministe (clock/rng injectés).
    """

    def __init__(self, policy: WorkerPolicy, clock: Clock, rng: Rng) -> None:
        self._policy = policy
        self._clock = clock
        self._rng = rng
        self._states: dict[str, ChannelBackoff] = {}

    def load_from(self, states: dict[str, ChannelBackoff]) -> None:
        """Recharge le registre depuis un snapshot persisté (reprise après crash, spec §7)."""
        self._states = dict(states)

    def snapshot(self) -> dict[str, ChannelBackoff]:
        """Copie de la map courante (à persister en fin de cycle, spec §7)."""
        return dict(self._states)

    def is_in_backoff(self, key: str) -> bool:
        """``True`` si ``key`` a un ``retry_after`` encore dans le FUTUR (à sauter)."""
        state = self._states.get(key)
        if state is None:
            return False
        return _iso(self._clock.now()) < state.retry_after

    def record_failure(self, key: str) -> float:
        """Incrémente ``attempts``, calcule délai+jitter, pose ``retry_after``. Rend le délai.

        Le délai sert au LOG ; la décision opérationnelle est le ``retry_after`` (skip).
        """
        attempts = self._states[key].attempts + 1 if key in self._states else 1
        delay = backoff_delay(
            attempts,
            base=self._policy.backoff_base_seconds,
            cap=self._policy.backoff_cap_seconds,
            factor=self._policy.backoff_factor,
        )
        delay += self._rng.jitter(self._policy.backoff_jitter_ratio * delay)
        retry_after = _iso(self._clock.now() + timedelta(seconds=delay))
        self._states[key] = ChannelBackoff(attempts=attempts, retry_after=retry_after)
        return delay

    def reset(self, key: str) -> None:
        """Efface le backoff d'une clé (succès)."""
        self._states.pop(key, None)


@dataclass
class WorkerDeps:
    """Dépendances partagées d'un travailleur (la composition les assemble une fois).

    ``backoff`` est le registre PARTAGÉ (même instance pour tous les travailleurs + le cycle,
    qui le persiste). ``rng`` sert au jitter de la pause inter-mots-clés (le backoff a son
    propre accès au RNG via le registre ; les deux pointent la même instance partagée).
    Writer unique sur l'event loop → aucune course (spec §3).
    """

    catalog: CatalogRepository
    engine: MatchingEngine
    signal: DecisionSignal
    clock: Clock
    rng: Rng
    policy: WorkerPolicy
    backoff: "BackoffRegistry"


class SearchWorker:
    """Pilote UN ``amuled`` pour drainer des ``SearchTask`` (spec §3/§4)."""

    def __init__(self, instance_name: str, client: MuleClient, deps: WorkerDeps) -> None:
        self._instance = instance_name
        self._client = client
        self._deps = deps
        self._connected = False

    async def _ensure_connected(self) -> bool:
        """Connecte le client si nécessaire. Rend ``False`` si l'instance reste down."""
        if self._connected:
            return True
        try:
            await self._client.connect()
        except MuleUnreachableError as error:
            delay = self._deps.backoff.record_failure(self._instance)
            _logger.warning(
                "instance %s injoignable (%s) — backoff reconnexion %.1fs",
                self._instance,
                error,
                delay,
            )
            return False
        self._connected = True
        self._deps.backoff.reset(self._instance)
        _logger.info("instance %s connectée", self._instance)
        return True

    async def _poll_then_fetch(self) -> int:
        """Polling borné (budget config) puis ``fetch_results`` → pipeline par obs.

        Rend le nombre de verdicts CHANGÉS (logging). Le polling s'arrête dès 100 % ou au
        budget épuisé ; ``fetch_results`` rend le snapshot cumulatif (handoff EC). Une
        ``RepositoryError`` par obs est ABSORBÉE (loggée + comptée) DANS
        ``record_observation`` → le cycle continue (spec §7), une seule obs corrompue ne
        fait pas tomber tout le balayage.
        """
        waited = 0.0
        while waited < self._deps.policy.poll_budget_seconds:
            progress = await self._client.search_progress()
            if progress is not None and progress >= _PROGRESS_DONE:
                break
            await self._deps.clock.sleep(self._deps.policy.poll_interval_seconds)
            waited += self._deps.policy.poll_interval_seconds
        results = await self._client.fetch_results()
        changed = 0
        for observation in results:
            if record_observation(
                observation,
                catalog=self._deps.catalog,
                engine=self._deps.engine,
                signal=self._deps.signal,
            ):
                changed += 1
        return changed

    async def run_task(self, task: SearchTask) -> None:
        """Exécute UN ``SearchTask`` (spec §4). Ne lève jamais : signale par backoff/log.

        SAUTE l'item si l'instance OU le canal est en backoff (``retry_after`` futur, spec §7).
        """
        channel_key = f"{self._instance}:{task.channel}"
        if self._deps.backoff.is_in_backoff(self._instance):
            _logger.info("instance %s en backoff — item '%s' sauté", self._instance, task.keyword)
            return
        if self._deps.backoff.is_in_backoff(channel_key):
            _logger.info(
                "instance %s canal %s en backoff — item '%s' sauté",
                self._instance,
                task.channel,
                task.keyword,
            )
            return
        if not await self._ensure_connected():
            return
        try:
            await self._client.start_search(task.keyword, task.channel)
            changed = await self._poll_then_fetch()
        except MuleSearchFailedError as error:
            delay = self._deps.backoff.record_failure(channel_key)
            _logger.warning(
                "instance %s canal %s en échec (%s) — backoff %.1fs",
                self._instance,
                task.channel,
                error,
                delay,
            )
            return
        except MuleUnreachableError as error:
            self._connected = False
            delay = self._deps.backoff.record_failure(self._instance)
            _logger.warning(
                "instance %s : flux EC mort (%s) — instance down, backoff %.1fs",
                self._instance,
                error,
                delay,
            )
            return
        self._deps.backoff.reset(channel_key)
        _logger.info(
            "instance %s : '%s'/%s → %d verdict(s) changé(s)",
            self._instance,
            task.keyword,
            task.channel,
            changed,
        )

    async def pause_between_items(self) -> None:
        """Dort une PAUSE JITTERÉE inter-mots-clés (spec §5/§7, anti-rate-limit eD2k).

        Délai = ``keyword_pause_min + rng.jitter(keyword_pause_max - keyword_pause_min)``
        (réutilise le contrat ``Rng.jitter`` : ``[0, span)`` ; ``span ≤ 0`` quand min == max
        → jitter nul → pause FIXE = min). Espace les recherches d'un même travailleur pour
        éviter qu'``amuled`` se fasse bannir d'un serveur eD2k (spec §7). Appelée par le
        drain ENTRE deux items, jamais après le dernier (l'appelant saute la file vidée).
        """
        policy = self._deps.policy
        span = policy.keyword_pause_max_seconds - policy.keyword_pause_min_seconds
        delay = policy.keyword_pause_min_seconds + self._deps.rng.jitter(span)
        await self._deps.clock.sleep(delay)
