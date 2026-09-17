# Observabilité — Plan E.1 (socle) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construire le socle d'observabilité du crawler — une chaîne pure `events → policy → dispatcher` + sinks Prometheus/apprise + config + état edge-trigger — **sans toucher aucun code de prod existant** (zéro régression ; le gate 100 % branch ne porte que sur du code neuf).

**Architecture:** Hexagonale. `domain/observability/` est PUR (dataclasses d'événements + politique `describe(event) → Report`, aucun import `logging`/Prometheus/apprise). Les sinks et le dispatcher vivent dans `adapters/observability/`, derrière les ports `ports/telemetry.py`. La config étend les dataclasses gelées existantes. **Ce plan ne câble rien dans les boucles** (c'est E.2) : il livre la machinerie, testée à 100 %.

**Tech Stack:** Python ≥3.12, `prometheus-client`, `apprise`, dataclasses gelées, `match`/`assert_never`, pytest (100 % branch), `mypy --strict`, `ruff`.

**Référence spec :** `docs/superpowers/specs/2026-06-15-observability-design.md` (E-D1→E-D13).

**Rappel gate (à faire passer VERT après chaque tâche, depuis `packages/crawler/`) :**
```bash
( cd packages/crawler && uv run pytest -q )      # 100 % branch
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
Pour un test focalisé (sinon `--cov-fail-under=100` fait échouer un run partiel) : ajouter `--no-cov`.

---

## File Structure

**Crawler — créés :**
- `packages/crawler/src/emule_indexer/domain/observability/__init__.py` — paquet (vide).
- `packages/crawler/src/emule_indexer/domain/observability/events.py` — 15 dataclasses gelées + `type Event`.
- `packages/crawler/src/emule_indexer/domain/observability/policy.py` — `Severity`/`Audience`/`MetricName`/`MetricInstruction`/`Report` + `describe`.
- `packages/crawler/src/emule_indexer/ports/telemetry.py` — `Telemetry`/`MetricsSink`/`Notifier` (Protocols).
- `packages/crawler/src/emule_indexer/adapters/observability/__init__.py` — paquet (vide).
- `packages/crawler/src/emule_indexer/adapters/observability/dispatcher.py` — `ObservabilityDispatcher`.
- `packages/crawler/src/emule_indexer/adapters/observability/prometheus_sink.py` — `PrometheusSink`.
- `packages/crawler/src/emule_indexer/adapters/observability/apprise_notifier.py` — `AppriseNotifier`.
- `packages/crawler/src/emule_indexer/application/edge_state.py` — `EdgeState` (anti-spam edge-trigger).

**Crawler — modifiés :**
- `packages/crawler/src/emule_indexer/adapters/config/crawler_config.py` — `ObservabilityConfig`/`MetricsConfig` + parse + champ `CrawlerConfig.observability`.
- `packages/crawler/src/emule_indexer/adapters/config/local_config.py` — `NotificationTarget` + parse + champ `LocalConfig.notifications`.
- `packages/crawler/pyproject.toml` — deps `prometheus-client`, `apprise`.
- `pyproject.toml` (racine) — overrides `mypy` pour `apprise` (pas de stubs).

**Tests — créés** (miroir sous `packages/crawler/tests/`) : un fichier par module ci-dessus.

---

## Task 1 : `domain/observability/events.py` (taxonomie pure)

**Files:**
- Create: `packages/crawler/src/emule_indexer/domain/observability/__init__.py`
- Create: `packages/crawler/src/emule_indexer/domain/observability/events.py`
- Test: `packages/crawler/tests/domain/observability/test_events.py`

- [ ] **Step 1 : créer le paquet de test**

Create `packages/crawler/tests/domain/observability/__init__.py` (vide) et `packages/crawler/tests/domain/observability/test_events.py` :

```python
"""Les événements sont des dataclasses gelées à champs métier — test de construction/gel."""

import dataclasses

import pytest

from emule_indexer.domain.observability.events import (
    ObservationRecorded,
    VerificationCompleted,
)


def test_observation_recorded_carries_network() -> None:
    event = ObservationRecorded(network="ed2k")
    assert event.network == "ed2k"


def test_event_is_frozen() -> None:
    event = VerificationCompleted(target_id="S2E062A", verdict="clean")
    with pytest.raises(dataclasses.FrozenInstanceError):
        # setattr (pas l'affectation directe) → pas d'erreur mypy à supprimer, mais le frozen
        # lève bien FrozenInstanceError au runtime.
        setattr(event, "verdict", "malicious")
```

- [ ] **Step 2 : lancer le test → échoue**

Run: `( cd packages/crawler && uv run pytest tests/domain/observability/test_events.py --no-cov -q )`
Expected: FAIL (`ModuleNotFoundError: emule_indexer.domain.observability`).

- [ ] **Step 3 : implémenter `events.py`**

Create `packages/crawler/src/emule_indexer/domain/observability/__init__.py` (vide) puis `events.py` :

```python
"""Événements d'observabilité : faits métier PURS (spec Plan E §3-4).

Couche DOMAINE (pure). Une dataclass GELÉE par fait observable saillant ; union taguée
``Event``. Champs métier UNIQUEMENT — aucune notion de log/metric/notif (c'est le rôle de
``policy.describe``). Les faits de panne récurrents portent ``first_occurrence`` (calculé par
l'application via ``EdgeState``) pour l'anti-spam des notifications (E-D8).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchCycleCompleted:
    cycle_index: int
    duration_seconds: float


@dataclass(frozen=True)
class SearchExecuted:
    network: str
    n_results: int


@dataclass(frozen=True)
class InstanceUnreachable:
    instance: str


@dataclass(frozen=True)
class SearchFailed:
    instance: str
    network: str


@dataclass(frozen=True)
class AllInstancesBlind:
    first_occurrence: bool


@dataclass(frozen=True)
class ObservationRecorded:
    network: str


@dataclass(frozen=True)
class DecisionRecorded:
    target_id: str
    tier: str


@dataclass(frozen=True)
class DownloadQueued:
    target_id: str


@dataclass(frozen=True)
class DownloadCompleted:
    target_id: str
    ed2k_hash: str


@dataclass(frozen=True)
class PromotionFailed:
    ed2k_hash: str


@dataclass(frozen=True)
class VerificationCompleted:
    target_id: str
    verdict: str


@dataclass(frozen=True)
class VerifierUnavailable:
    first_occurrence: bool


@dataclass(frozen=True)
class ConnectedInstancesSampled:
    network: str
    count: int


@dataclass(frozen=True)
class VerificationQueueDepthSampled:
    count: int


@dataclass(frozen=True)
class CrawlerStarted:
    mode: str


type Event = (
    SearchCycleCompleted
    | SearchExecuted
    | InstanceUnreachable
    | SearchFailed
    | AllInstancesBlind
    | ObservationRecorded
    | DecisionRecorded
    | DownloadQueued
    | DownloadCompleted
    | PromotionFailed
    | VerificationCompleted
    | VerifierUnavailable
    | ConnectedInstancesSampled
    | VerificationQueueDepthSampled
    | CrawlerStarted
)
```

- [ ] **Step 4 : lancer le test → passe**

Run: `( cd packages/crawler && uv run pytest tests/domain/observability/test_events.py --no-cov -q )`
Expected: PASS (2 passed).

- [ ] **Step 5 : gate complet + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/domain/observability packages/crawler/tests/domain/observability
git commit -m "feat(domain): observability events taxonomy (Plan E.1)"
```

---

## Task 2 : `domain/observability/policy.py` (politique pure `describe`)

**Files:**
- Create: `packages/crawler/src/emule_indexer/domain/observability/policy.py`
- Test: `packages/crawler/tests/domain/observability/test_policy.py`

- [ ] **Step 1 : écrire le test paramétré (exhaustif, un cas par variante + branches)**

Create `packages/crawler/tests/domain/observability/test_policy.py` :

```python
"""``describe`` est un match exhaustif : un cas par événement + chaque branche conditionnelle
(verdict connu/inconnu, tier download/autre, first_occurrence vrai/faux)."""

from emule_indexer.domain.observability import events as ev
from emule_indexer.domain.observability.policy import (
    Audience,
    MetricInstruction,
    MetricName,
    Report,
    Severity,
    describe,
)

_COMMUNITY = frozenset({Audience.COMMUNITY})
_OPERATIONS = frozenset({Audience.OPERATIONS})
_BOTH = frozenset({Audience.COMMUNITY, Audience.OPERATIONS})


CASES: list[tuple[ev.Event, Report]] = [
    (
        ev.SearchCycleCompleted(cycle_index=3, duration_seconds=4.5),
        Report(
            Severity.INFO,
            "cycle 3 terminé (4.5s)",
            (
                MetricInstruction(MetricName.SEARCH_CYCLES, "inc"),
                MetricInstruction(MetricName.SEARCH_CYCLE_DURATION, "observe", value=4.5),
            ),
        ),
    ),
    (
        ev.SearchExecuted(network="ed2k", n_results=7),
        Report(
            Severity.DEBUG,
            "recherche ed2k : 7 résultat(s)",
            (MetricInstruction(MetricName.SEARCHES, "inc", (("network", "ed2k"),)),),
        ),
    ),
    (
        ev.InstanceUnreachable(instance="amule-1"),
        Report(
            Severity.WARNING,
            "instance amule-1 injoignable",
            (MetricInstruction(MetricName.MULE_UNREACHABLE, "inc", (("instance", "amule-1"),)),),
        ),
    ),
    (
        ev.SearchFailed(instance="amule-1", network="kad"),
        Report(
            Severity.WARNING,
            "recherche en échec sur kad (instance amule-1)",
            (MetricInstruction(MetricName.SEARCH_FAILURES, "inc", (("network", "kad"),)),),
        ),
    ),
    (
        ev.AllInstancesBlind(first_occurrence=True),
        Report(
            Severity.WARNING,
            "couverture aveugle : aucune instance search-capable",
            (MetricInstruction(MetricName.SEARCH_BLIND_CYCLES, "inc"),),
            _OPERATIONS,
        ),
    ),
    (
        ev.AllInstancesBlind(first_occurrence=False),
        Report(
            Severity.WARNING,
            "couverture aveugle : aucune instance search-capable",
            (MetricInstruction(MetricName.SEARCH_BLIND_CYCLES, "inc"),),
        ),
    ),
    (
        ev.ObservationRecorded(network="kad"),
        Report(
            Severity.DEBUG,
            "observation enregistrée (kad)",
            (MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", "kad"),)),),
        ),
    ),
    (
        ev.DecisionRecorded(target_id="S2E062A", tier="download"),
        Report(
            Severity.INFO,
            "décision download pour S2E062A",
            (MetricInstruction(MetricName.DECISIONS, "inc", (("tier", "download"),)),),
            _COMMUNITY,
        ),
    ),
    (
        ev.DecisionRecorded(target_id="S2E062A", tier="candidate"),
        Report(
            Severity.INFO,
            "décision candidate pour S2E062A",
            (MetricInstruction(MetricName.DECISIONS, "inc", (("tier", "candidate"),)),),
        ),
    ),
    (
        ev.DownloadQueued(target_id="S2E062A"),
        Report(
            Severity.INFO,
            "download mis en file : S2E062A",
            (MetricInstruction(MetricName.DOWNLOADS_QUEUED, "inc"),),
        ),
    ),
    (
        ev.DownloadCompleted(target_id="S2E062A", ed2k_hash="a" * 32),
        Report(
            Severity.INFO,
            "✅ téléchargement terminé : S2E062A",
            (MetricInstruction(MetricName.DOWNLOADS_COMPLETED, "inc"),),
            _COMMUNITY,
        ),
    ),
    (
        ev.PromotionFailed(ed2k_hash="a" * 32),
        Report(
            Severity.WARNING,
            f"mise en quarantaine échouée : {'a' * 32}",
            (MetricInstruction(MetricName.PROMOTION_FAILURES, "inc"),),
        ),
    ),
    (
        ev.VerificationCompleted(target_id="S2E062A", verdict="clean"),
        Report(
            Severity.INFO,
            "vérification S2E062A : verdict=clean",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "clean"),)),),
            _COMMUNITY,
        ),
    ),
    (
        ev.VerificationCompleted(target_id="S2E062A", verdict="suspicious"),
        Report(
            Severity.INFO,
            "vérification S2E062A : verdict=suspicious",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "suspicious"),)),),
            _OPERATIONS,
        ),
    ),
    (
        ev.VerificationCompleted(target_id="S2E062A", verdict="malicious"),
        Report(
            Severity.WARNING,
            "vérification S2E062A : verdict=malicious",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "malicious"),)),),
            _OPERATIONS,
        ),
    ),
    (
        ev.VerificationCompleted(target_id="S2E062A", verdict="error"),
        Report(
            Severity.WARNING,
            "vérification S2E062A : verdict=error",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "error"),)),),
        ),
    ),
    (
        ev.VerificationCompleted(target_id="S2E062A", verdict="bogus"),  # verdict INCONNU → défensif
        Report(
            Severity.WARNING,
            "vérification S2E062A : verdict=bogus",
            (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", "bogus"),)),),
        ),
    ),
    (
        ev.VerifierUnavailable(first_occurrence=True),
        Report(
            Severity.WARNING,
            "verifier injoignable",
            (MetricInstruction(MetricName.VERIFIER_UNAVAILABLE, "inc"),),
            _OPERATIONS,
        ),
    ),
    (
        ev.VerifierUnavailable(first_occurrence=False),
        Report(
            Severity.WARNING,
            "verifier injoignable",
            (MetricInstruction(MetricName.VERIFIER_UNAVAILABLE, "inc"),),
        ),
    ),
    (
        ev.ConnectedInstancesSampled(network="ed2k", count=2),
        Report(
            Severity.DEBUG,
            "instances connectées (ed2k) : 2",
            (MetricInstruction(MetricName.CONNECTED_INSTANCES, "set", (("network", "ed2k"),), 2.0),),
        ),
    ),
    (
        ev.VerificationQueueDepthSampled(count=5),
        Report(
            Severity.DEBUG,
            "file de vérification : 5 en attente",
            (MetricInstruction(MetricName.VERIFICATION_QUEUE_DEPTH, "set", (), 5.0),),
        ),
    ),
    (
        ev.CrawlerStarted(mode="full"),
        Report(
            Severity.INFO,
            "🟢 instance en ligne (mode full)",
            (MetricInstruction(MetricName.CRAWLER_UP, "set", (), 1.0),),
            _BOTH,
        ),
    ),
]


def test_describe_maps_every_event() -> None:
    for event, expected in CASES:
        assert describe(event) == expected, f"mauvais Report pour {event!r}"
```

- [ ] **Step 2 : lancer → échoue**

Run: `( cd packages/crawler && uv run pytest tests/domain/observability/test_policy.py --no-cov -q )`
Expected: FAIL (`ImportError`).

- [ ] **Step 3 : implémenter `policy.py`**

```python
"""Politique d'observabilité : ``describe(event) → Report`` (spec Plan E §3, E-D3).

Couche DOMAINE (pure). SEUL endroit qui décide — pour chaque événement — sévérité, message,
métrique(s), audiences. ``describe`` est un match EXHAUSTIF (``assert_never`` → 100 % branch).
Le domaine ne connaît ni ``logging`` ni Prometheus ni apprise : ``Severity``/``Audience``/
``MetricName`` sont des enums DOMAINE, traduits par les adapters (E-D3).

GOTCHA Prometheus : les noms de COUNTERS n'incluent PAS ``_total`` ici — ``prometheus_client``
l'ajoute à l'exposition (l'inclure produirait ``…_total_total``). Gauges/histogramme : nom tel
quel.
"""

from dataclasses import dataclass
from enum import Enum, StrEnum, auto
from typing import Literal, assert_never

from emule_indexer.domain.observability.events import (
    AllInstancesBlind,
    ConnectedInstancesSampled,
    CrawlerStarted,
    DecisionRecorded,
    DownloadCompleted,
    DownloadQueued,
    Event,
    InstanceUnreachable,
    ObservationRecorded,
    PromotionFailed,
    SearchCycleCompleted,
    SearchExecuted,
    SearchFailed,
    VerificationCompleted,
    VerificationQueueDepthSampled,
    VerifierUnavailable,
)


class Severity(Enum):
    """Sévérité DOMAINE d'un fait (traduite en niveau ``logging`` par l'adapter)."""

    DEBUG = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()


class Audience(Enum):
    """Consommateur d'une notification (E-D7) — la VALEUR est le tag apprise."""

    COMMUNITY = "community"
    OPERATIONS = "operations"


class MetricName(StrEnum):
    """Noms de métriques. Counters SANS ``_total`` (ajouté par prometheus_client à l'expo)."""

    SEARCH_CYCLES = "emule_search_cycles"
    SEARCH_CYCLE_DURATION = "emule_search_cycle_duration_seconds"
    SEARCHES = "emule_searches"
    OBSERVATIONS = "emule_observations"
    SEARCH_FAILURES = "emule_search_failures"
    MULE_UNREACHABLE = "emule_mule_unreachable"
    SEARCH_BLIND_CYCLES = "emule_search_blind_cycles"
    DECISIONS = "emule_decisions"
    DOWNLOADS_QUEUED = "emule_downloads_queued"
    DOWNLOADS_COMPLETED = "emule_downloads_completed"
    PROMOTION_FAILURES = "emule_promotion_failures"
    VERIFICATIONS = "emule_verifications"
    VERIFIER_UNAVAILABLE = "emule_verifier_unavailable"
    CONNECTED_INSTANCES = "emule_connected_instances"
    VERIFICATION_QUEUE_DEPTH = "emule_verification_queue_depth"
    CRAWLER_UP = "emule_crawler_up"


MetricKind = Literal["inc", "set", "observe"]


@dataclass(frozen=True)
class MetricInstruction:
    """Une opération de métrique : compteur ``inc`` / jauge ``set`` / histogramme ``observe``.

    ``labels`` = tuple de paires (clé, valeur) ordonnées (hashable → utilisable dans un test
    d'égalité de ``Report``). ``value`` = quantité (défaut 1.0 pour les ``inc``).
    """

    name: MetricName
    kind: MetricKind
    labels: tuple[tuple[str, str], ...] = ()
    value: float = 1.0


@dataclass(frozen=True)
class Report:
    """Comment raconter un événement : sévérité + message + métrique(s) + audiences de notif.

    ``metrics`` est un TUPLE (un événement peut alimenter plusieurs métriques —
    ``SearchCycleCompleted`` = compteur + histogramme). ``audiences`` vide = aucune notif.
    """

    severity: Severity
    message: str
    metrics: tuple[MetricInstruction, ...] = ()
    audiences: frozenset[Audience] = frozenset()


_VERDICT_SEVERITY: dict[str, Severity] = {
    "clean": Severity.INFO,
    "suspicious": Severity.INFO,
    "malicious": Severity.WARNING,
    "error": Severity.WARNING,
}
_VERDICT_AUDIENCES: dict[str, frozenset[Audience]] = {
    "clean": frozenset({Audience.COMMUNITY}),
    "suspicious": frozenset({Audience.OPERATIONS}),
    "malicious": frozenset({Audience.OPERATIONS}),
    "error": frozenset(),
}


def _verification(event: VerificationCompleted) -> Report:
    # verdict inconnu (contrat verifier non respecté) → traité comme ``error`` (défensif, E-D13).
    severity = _VERDICT_SEVERITY.get(event.verdict, Severity.WARNING)
    audiences = _VERDICT_AUDIENCES.get(event.verdict, frozenset())
    return Report(
        severity,
        f"vérification {event.target_id} : verdict={event.verdict}",
        (MetricInstruction(MetricName.VERIFICATIONS, "inc", (("verdict", event.verdict),)),),
        audiences,
    )


def describe(event: Event) -> Report:
    """Mappe un événement vers son ``Report`` (match EXHAUSTIF → 100 % branch)."""
    match event:
        case SearchCycleCompleted():
            return Report(
                Severity.INFO,
                f"cycle {event.cycle_index} terminé ({event.duration_seconds:.1f}s)",
                (
                    MetricInstruction(MetricName.SEARCH_CYCLES, "inc"),
                    MetricInstruction(
                        MetricName.SEARCH_CYCLE_DURATION, "observe", value=event.duration_seconds
                    ),
                ),
            )
        case SearchExecuted():
            return Report(
                Severity.DEBUG,
                f"recherche {event.network} : {event.n_results} résultat(s)",
                (MetricInstruction(MetricName.SEARCHES, "inc", (("network", event.network),)),),
            )
        case InstanceUnreachable():
            return Report(
                Severity.WARNING,
                f"instance {event.instance} injoignable",
                (
                    MetricInstruction(
                        MetricName.MULE_UNREACHABLE, "inc", (("instance", event.instance),)
                    ),
                ),
            )
        case SearchFailed():
            return Report(
                Severity.WARNING,
                f"recherche en échec sur {event.network} (instance {event.instance})",
                (
                    MetricInstruction(
                        MetricName.SEARCH_FAILURES, "inc", (("network", event.network),)
                    ),
                ),
            )
        case AllInstancesBlind():
            return Report(
                Severity.WARNING,
                "couverture aveugle : aucune instance search-capable",
                (MetricInstruction(MetricName.SEARCH_BLIND_CYCLES, "inc"),),
                frozenset({Audience.OPERATIONS}) if event.first_occurrence else frozenset(),
            )
        case ObservationRecorded():
            return Report(
                Severity.DEBUG,
                f"observation enregistrée ({event.network})",
                (MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", event.network),)),),
            )
        case DecisionRecorded():
            return Report(
                Severity.INFO,
                f"décision {event.tier} pour {event.target_id}",
                (MetricInstruction(MetricName.DECISIONS, "inc", (("tier", event.tier),)),),
                frozenset({Audience.COMMUNITY}) if event.tier == "download" else frozenset(),
            )
        case DownloadQueued():
            return Report(
                Severity.INFO,
                f"download mis en file : {event.target_id}",
                (MetricInstruction(MetricName.DOWNLOADS_QUEUED, "inc"),),
            )
        case DownloadCompleted():
            return Report(
                Severity.INFO,
                f"✅ téléchargement terminé : {event.target_id}",
                (MetricInstruction(MetricName.DOWNLOADS_COMPLETED, "inc"),),
                frozenset({Audience.COMMUNITY}),
            )
        case PromotionFailed():
            return Report(
                Severity.WARNING,
                f"mise en quarantaine échouée : {event.ed2k_hash}",
                (MetricInstruction(MetricName.PROMOTION_FAILURES, "inc"),),
            )
        case VerificationCompleted():
            return _verification(event)
        case VerifierUnavailable():
            return Report(
                Severity.WARNING,
                "verifier injoignable",
                (MetricInstruction(MetricName.VERIFIER_UNAVAILABLE, "inc"),),
                frozenset({Audience.OPERATIONS}) if event.first_occurrence else frozenset(),
            )
        case ConnectedInstancesSampled():
            return Report(
                Severity.DEBUG,
                f"instances connectées ({event.network}) : {event.count}",
                (
                    MetricInstruction(
                        MetricName.CONNECTED_INSTANCES,
                        "set",
                        (("network", event.network),),
                        float(event.count),
                    ),
                ),
            )
        case VerificationQueueDepthSampled():
            return Report(
                Severity.DEBUG,
                f"file de vérification : {event.count} en attente",
                (
                    MetricInstruction(
                        MetricName.VERIFICATION_QUEUE_DEPTH, "set", (), float(event.count)
                    ),
                ),
            )
        case CrawlerStarted():
            return Report(
                Severity.INFO,
                f"🟢 instance en ligne (mode {event.mode})",
                (MetricInstruction(MetricName.CRAWLER_UP, "set", (), 1.0),),
                frozenset({Audience.COMMUNITY, Audience.OPERATIONS}),
            )
        case _:  # pragma: no cover
            assert_never(event)
```

- [ ] **Step 4 : lancer → passe**

Run: `( cd packages/crawler && uv run pytest tests/domain/observability/test_policy.py --no-cov -q )`
Expected: PASS (1 passed).

- [ ] **Step 5 : gate complet + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/domain/observability/policy.py packages/crawler/tests/domain/observability/test_policy.py
git commit -m "feat(domain): observability policy describe() (Plan E.1)"
```

---

## Task 3 : `ports/telemetry.py` (ports Telemetry/MetricsSink/Notifier)

**Files:**
- Create: `packages/crawler/src/emule_indexer/ports/telemetry.py`
- Test: `packages/crawler/tests/ports/test_telemetry.py`

- [ ] **Step 1 : écrire le test (un faux satisfait structurellement chaque Protocol)**

Create `packages/crawler/tests/ports/test_telemetry.py` :

```python
"""Les ports sont des Protocols structurels : un faux minimal les satisfait (et couvre les stubs)."""

from emule_indexer.domain.observability.events import CrawlerStarted, Event
from emule_indexer.domain.observability.policy import (
    Audience,
    MetricInstruction,
    MetricName,
    Severity,
)
from emule_indexer.ports.telemetry import MetricsSink, Notifier, Telemetry


class _Sink:
    def apply(self, instruction: MetricInstruction) -> None:
        self.last = instruction


class _Notifier:
    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        self.last = (audience, body, severity)


class _Telemetry:
    async def emit(self, event: Event) -> None:
        self.last = event


def test_fakes_satisfy_ports() -> None:
    sink: MetricsSink = _Sink()
    notifier: Notifier = _Notifier()
    telemetry: Telemetry = _Telemetry()
    sink.apply(MetricInstruction(MetricName.OBSERVATIONS, "inc"))
    assert isinstance(notifier, Notifier)
    assert isinstance(telemetry, Telemetry)
    assert isinstance(sink, MetricsSink)
```

(`Telemetry`/`Notifier`/`MetricsSink` doivent être `@runtime_checkable` pour `isinstance`.)

- [ ] **Step 2 : lancer → échoue** — Run: `( cd packages/crawler && uv run pytest tests/ports/test_telemetry.py --no-cov -q )` ; Expected: FAIL (`ImportError`).

- [ ] **Step 3 : implémenter `ports/telemetry.py`**

```python
"""Ports d'observabilité (spec Plan E §3). ``Telemetry`` (façade émise par l'application) +
sinks ``MetricsSink``/``Notifier`` (branchés dans le dispatcher). Protocols structurels —
les adapters réels ET les fakes de test les satisfont sans héritage. Stubs sur UNE ligne."""

from typing import Protocol, runtime_checkable

from emule_indexer.domain.observability.events import Event
from emule_indexer.domain.observability.policy import Audience, MetricInstruction, Severity


@runtime_checkable
class MetricsSink(Protocol):
    def apply(self, instruction: MetricInstruction) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    async def notify(self, audience: Audience, body: str, severity: Severity) -> None: ...


@runtime_checkable
class Telemetry(Protocol):
    async def emit(self, event: Event) -> None: ...
```

- [ ] **Step 4 : lancer → passe** — Run: `( cd packages/crawler && uv run pytest tests/ports/test_telemetry.py --no-cov -q )` ; Expected: PASS.

- [ ] **Step 5 : gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/ports/telemetry.py packages/crawler/tests/ports/test_telemetry.py
git commit -m "feat(ports): telemetry/metrics-sink/notifier protocols (Plan E.1)"
```

---

## Task 4 : `adapters/observability/dispatcher.py` (le routeur)

**Files:**
- Create: `packages/crawler/src/emule_indexer/adapters/observability/__init__.py`
- Create: `packages/crawler/src/emule_indexer/adapters/observability/dispatcher.py`
- Test: `packages/crawler/tests/adapters/observability/test_dispatcher.py`

- [ ] **Step 1 : écrire le test** — couvre : log au bon niveau, métriques appliquées, 0/1/2 audiences, échec ET timeout de notif absorbés.

Create `packages/crawler/tests/adapters/observability/__init__.py` (vide) et `test_dispatcher.py` :

```python
"""Le dispatcher : log + métriques toujours ; notif par audience, échec/timeout absorbés."""

import asyncio
import logging

import pytest

from emule_indexer.adapters.observability.dispatcher import ObservabilityDispatcher
from emule_indexer.domain.observability import events as ev
from emule_indexer.domain.observability.policy import Audience, MetricInstruction, Severity
from emule_indexer.ports.telemetry import MetricsSink, Notifier


class _RecordingSink:
    def __init__(self) -> None:
        self.applied: list[MetricInstruction] = []

    def apply(self, instruction: MetricInstruction) -> None:
        self.applied.append(instruction)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[Audience, str, Severity]] = []

    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        self.calls.append((audience, body, severity))


class _RaisingNotifier:
    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        raise RuntimeError("canal mort")


class _HangingNotifier:
    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        await asyncio.sleep(10)  # dépasse le timeout court du test


def _dispatcher(
    sink: MetricsSink, notifier: Notifier, timeout: float = 5.0
) -> ObservabilityDispatcher:
    # _RecordingSink/_RecordingNotifier/… satisfont structurellement MetricsSink/Notifier → pas d'ignore.
    return ObservabilityDispatcher(metrics=sink, notifier=notifier, notify_timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_logs_and_applies_metrics_no_audience() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(ev.ObservationRecorded(network="ed2k"))
    assert [m.name.value for m in sink.applied] == ["emule_observations"]
    assert notifier.calls == []  # ObservationRecorded n'a aucune audience


@pytest.mark.asyncio
async def test_two_metrics_one_event() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(
        ev.SearchCycleCompleted(cycle_index=1, duration_seconds=2.0)
    )
    assert [m.name.value for m in sink.applied] == [
        "emule_search_cycles",
        "emule_search_cycle_duration_seconds",
    ]


@pytest.mark.asyncio
async def test_notifies_both_audiences() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(ev.CrawlerStarted(mode="full"))
    assert {a for a, _, _ in notifier.calls} == {Audience.COMMUNITY, Audience.OPERATIONS}


@pytest.mark.asyncio
async def test_log_level_matches_severity(caplog: pytest.LogCaptureFixture) -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    with caplog.at_level(logging.DEBUG, logger="emule_indexer.observability"):
        await _dispatcher(sink, notifier).emit(ev.InstanceUnreachable(instance="amule-1"))
    assert caplog.records[-1].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_notification_failure_is_absorbed(caplog: pytest.LogCaptureFixture) -> None:
    sink = _RecordingSink()
    with caplog.at_level(logging.WARNING, logger="emule_indexer.observability"):
        await _dispatcher(sink, _RaisingNotifier()).emit(ev.DownloadCompleted("S2E062A", "a" * 32))
    assert sink.applied  # la métrique est passée malgré l'échec de notif
    assert any("échouée" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_notification_timeout_is_absorbed() -> None:
    sink = _RecordingSink()
    # timeout court + notifier qui pend → wait_for lève TimeoutError, absorbé.
    await _dispatcher(sink, _HangingNotifier(), timeout=0.01).emit(
        ev.DownloadCompleted("S2E062A", "a" * 32)
    )
    assert sink.applied
```

- [ ] **Step 2 : lancer → échoue** — Run: `( cd packages/crawler && uv run pytest tests/adapters/observability/test_dispatcher.py --no-cov -q )` ; Expected: FAIL (`ImportError`).

- [ ] **Step 3 : implémenter `dispatcher.py`**

Create `packages/crawler/src/emule_indexer/adapters/observability/__init__.py` (vide) puis :

```python
"""Dispatcher d'observabilité : route un ``Event`` vers log + métriques + notifications (E-D3/E-D13).

Couche ADAPTER. Implémente ``Telemetry``. ``emit`` : ``describe`` (pur) → log au niveau mappé +
``MetricsSink.apply`` pour chaque métrique + ``Notifier.notify`` par audience, chaque notif sous
``asyncio.wait_for(timeout)`` avec échec/timeout ABSORBÉ + loggé (un canal en panne ne casse
JAMAIS le crawl, E-D13). Aucun état (l'edge-trigger vit dans l'application — E-D8)."""

import asyncio
import logging

from emule_indexer.domain.observability.events import Event
from emule_indexer.domain.observability.policy import Severity, describe
from emule_indexer.ports.telemetry import MetricsSink, Notifier

_logger = logging.getLogger("emule_indexer.observability")

_LEVELS: dict[Severity, int] = {
    Severity.DEBUG: logging.DEBUG,
    Severity.INFO: logging.INFO,
    Severity.WARNING: logging.WARNING,
    Severity.ERROR: logging.ERROR,
}


class ObservabilityDispatcher:
    """Adapter ``Telemetry`` : un point d'émission, trois sorties (log/métrique/notif)."""

    def __init__(
        self, *, metrics: MetricsSink, notifier: Notifier, notify_timeout_seconds: float
    ) -> None:
        self._metrics = metrics
        self._notifier = notifier
        self._timeout = notify_timeout_seconds

    async def emit(self, event: Event) -> None:
        report = describe(event)
        _logger.log(_LEVELS[report.severity], report.message)
        for instruction in report.metrics:
            self._metrics.apply(instruction)
        for audience in report.audiences:
            try:
                await asyncio.wait_for(
                    self._notifier.notify(audience, report.message, report.severity),
                    timeout=self._timeout,
                )
            except Exception as error:  # noqa: BLE001 — une notif ne casse JAMAIS le crawl (E-D13)
                _logger.warning("notification %s échouée (%s)", audience.value, error)
```

- [ ] **Step 4 : lancer → passe** — Run: `( cd packages/crawler && uv run pytest tests/adapters/observability/test_dispatcher.py --no-cov -q )` ; Expected: PASS (6 passed).

- [ ] **Step 5 : gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/adapters/observability packages/crawler/tests/adapters/observability/__init__.py packages/crawler/tests/adapters/observability/test_dispatcher.py
git commit -m "feat(adapters): observability dispatcher (Plan E.1)"
```

---

## Task 5 : `pyproject` deps + `adapters/observability/prometheus_sink.py`

**Files:**
- Modify: `packages/crawler/pyproject.toml` (deps `prometheus-client`)
- Create: `packages/crawler/src/emule_indexer/adapters/observability/prometheus_sink.py`
- Test: `packages/crawler/tests/adapters/observability/test_prometheus_sink.py`

- [ ] **Step 1 : ajouter la dépendance** — dans `packages/crawler/pyproject.toml`, section `[project] dependencies`, ajouter `"prometheus-client>=0.21"`. Puis :

```bash
uv sync --dev
```

- [ ] **Step 2 : écrire le test** (registre jetable + `get_sample_value` ; rappel : counters exposés avec `_total`)

Create `packages/crawler/tests/adapters/observability/test_prometheus_sink.py` :

```python
"""Le sink applique inc/set/observe sur un CollectorRegistry jetable (relecture get_sample_value)."""

from prometheus_client import CollectorRegistry

from emule_indexer.adapters.observability.prometheus_sink import PrometheusSink
from emule_indexer.domain.observability.policy import MetricInstruction, MetricName


def test_counter_inc_with_label() -> None:
    registry = CollectorRegistry()
    sink = PrometheusSink(registry)
    sink.apply(MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", "ed2k"),)))
    sink.apply(MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", "ed2k"),)))
    # counter exposé AVEC le suffixe _total ajouté par prometheus_client
    assert registry.get_sample_value("emule_observations_total", {"network": "ed2k"}) == 2.0


def test_counter_inc_no_label() -> None:
    registry = CollectorRegistry()
    PrometheusSink(registry).apply(MetricInstruction(MetricName.DOWNLOADS_COMPLETED, "inc"))
    assert registry.get_sample_value("emule_downloads_completed_total") == 1.0


def test_gauge_set_with_label() -> None:
    registry = CollectorRegistry()
    PrometheusSink(registry).apply(
        MetricInstruction(MetricName.CONNECTED_INSTANCES, "set", (("network", "kad"),), 3.0)
    )
    assert registry.get_sample_value("emule_connected_instances", {"network": "kad"}) == 3.0


def test_gauge_set_no_label() -> None:
    registry = CollectorRegistry()
    PrometheusSink(registry).apply(MetricInstruction(MetricName.CRAWLER_UP, "set", (), 1.0))
    assert registry.get_sample_value("emule_crawler_up") == 1.0


def test_histogram_observe() -> None:
    registry = CollectorRegistry()
    PrometheusSink(registry).apply(
        MetricInstruction(MetricName.SEARCH_CYCLE_DURATION, "observe", (), 2.5)
    )
    assert registry.get_sample_value("emule_search_cycle_duration_seconds_count") == 1.0
    assert registry.get_sample_value("emule_search_cycle_duration_seconds_sum") == 2.5
```

- [ ] **Step 3 : lancer → échoue** — Run: `( cd packages/crawler && uv run pytest tests/adapters/observability/test_prometheus_sink.py --no-cov -q )` ; Expected: FAIL (`ImportError`).

- [ ] **Step 4 : implémenter `prometheus_sink.py`** — trois dicts HOMOGÈNES (counters/gauges/histogrammes) → mypy-clean (pas d'union `Counter|Gauge|Histogram`), 3 branches `kind`.

```python
"""Sink Prometheus : applique une ``MetricInstruction`` sur un ``CollectorRegistry`` DÉDIÉ (E-D9).

Couche ADAPTER (implémente ``MetricsSink``). Catalogue déclaré sur le registre INJECTÉ (jamais
le registre global) → testable sur un registre jetable, sans état partagé. Trois maps HOMOGÈNES
(counters/gauges/histogrammes) indexées par ``MetricName`` → ``apply`` route sur ``kind`` en 3
branches. GOTCHA : les counters sont nommés SANS ``_total`` (ajouté par la lib à l'exposition)."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from emule_indexer.domain.observability.policy import MetricInstruction, MetricName

# (nom, doc, labels) des counters.
_COUNTERS: tuple[tuple[MetricName, str, tuple[str, ...]], ...] = (
    (MetricName.SEARCH_CYCLES, "Cycles de recherche terminés", ()),
    (MetricName.SEARCHES, "Recherches exécutées", ("network",)),
    (MetricName.OBSERVATIONS, "Observations enregistrées", ("network",)),
    (MetricName.SEARCH_FAILURES, "Recherches en échec", ("network",)),
    (MetricName.MULE_UNREACHABLE, "Instances injoignables", ("instance",)),
    (MetricName.SEARCH_BLIND_CYCLES, "Cycles à couverture aveugle", ()),
    (MetricName.DECISIONS, "Décisions de match enregistrées", ("tier",)),
    (MetricName.DOWNLOADS_QUEUED, "Téléchargements mis en file", ()),
    (MetricName.DOWNLOADS_COMPLETED, "Téléchargements terminés", ()),
    (MetricName.PROMOTION_FAILURES, "Mises en quarantaine échouées", ()),
    (MetricName.VERIFICATIONS, "Vérifications terminées", ("verdict",)),
    (MetricName.VERIFIER_UNAVAILABLE, "Verifier injoignable (occurrences)", ()),
)
_GAUGES: tuple[tuple[MetricName, str, tuple[str, ...]], ...] = (
    (MetricName.CONNECTED_INSTANCES, "Instances search-capable", ("network",)),
    (MetricName.VERIFICATION_QUEUE_DEPTH, "Tâches de vérification en attente", ()),
    (MetricName.CRAWLER_UP, "Crawler en marche (1)", ()),
)
_HISTOGRAMS: tuple[tuple[MetricName, str], ...] = (
    (MetricName.SEARCH_CYCLE_DURATION, "Durée d'un cycle de recherche (s)"),
)


class PrometheusSink:
    """Adapter ``MetricsSink`` sur un registre dédié injecté."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self._counters = {
            name: Counter(name.value, doc, labels, registry=registry)
            for name, doc, labels in _COUNTERS
        }
        self._gauges = {
            name: Gauge(name.value, doc, labels, registry=registry)
            for name, doc, labels in _GAUGES
        }
        self._histograms = {
            name: Histogram(name.value, doc, registry=registry) for name, doc in _HISTOGRAMS
        }

    def apply(self, instruction: MetricInstruction) -> None:
        labels = dict(instruction.labels)
        if instruction.kind == "inc":
            counter = self._counters[instruction.name]
            (counter.labels(**labels) if labels else counter).inc(instruction.value)
        elif instruction.kind == "set":
            gauge = self._gauges[instruction.name]
            (gauge.labels(**labels) if labels else gauge).set(instruction.value)
        else:
            self._histograms[instruction.name].observe(instruction.value)
```

- [ ] **Step 5 : lancer → passe** — Run: `( cd packages/crawler && uv run pytest tests/adapters/observability/test_prometheus_sink.py --no-cov -q )` ; Expected: PASS (5 passed).

- [ ] **Step 6 : gate + commit** — si `mypy` se plaint de `prometheus_client` (selon présence de `py.typed`), ajouter un override (voir Task 9). Puis :

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/pyproject.toml uv.lock packages/crawler/src/emule_indexer/adapters/observability/prometheus_sink.py packages/crawler/tests/adapters/observability/test_prometheus_sink.py
git commit -m "feat(adapters): prometheus metrics sink (Plan E.1)"
```

---

## Task 6 : `pyproject` apprise + `adapters/observability/apprise_notifier.py`

**Files:**
- Modify: `packages/crawler/pyproject.toml` (dep `apprise`)
- Create: `packages/crawler/src/emule_indexer/adapters/observability/apprise_notifier.py`
- Test: `packages/crawler/tests/adapters/observability/test_apprise_notifier.py`

- [ ] **Step 1 : ajouter la dépendance** — `"apprise>=1.9"` dans `packages/crawler/pyproject.toml`. Puis `uv sync --dev`.

- [ ] **Step 2 : écrire le test** — préfixe `node_id`, routage par tag, mapping sévérité→NotifyType, no-op si aucune URL. On capture l'appel via un faux objet apprise injecté.

Create `packages/crawler/tests/adapters/observability/test_apprise_notifier.py` :

```python
"""Le notifier apprise : add(url, tag) au montage, préfixe node_id, route par tag, mappe NotifyType."""

import apprise
import pytest

from emule_indexer.adapters.observability.apprise_notifier import AppriseNotifier
from emule_indexer.domain.observability.policy import Audience, Severity


class _FakeApprise:
    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []
        self.sent: list[dict[str, object]] = []

    def add(self, url: str, tag: str) -> bool:
        self.added.append((url, tag))
        return True

    async def async_notify(self, **kwargs: object) -> bool:
        self.sent.append(kwargs)
        return True


def _notifier(fake: _FakeApprise, node_id: str = "titar-node-1") -> AppriseNotifier:
    targets = (("discord://x", Audience.COMMUNITY), ("discord://y", Audience.OPERATIONS))
    return AppriseNotifier(targets, node_id=node_id, apprise_obj=fake)


def test_targets_added_with_tags() -> None:
    fake = _FakeApprise()
    _notifier(fake)
    assert fake.added == [("discord://x", "community"), ("discord://y", "operations")]


@pytest.mark.asyncio
async def test_notify_prefixes_node_id_and_routes_tag() -> None:
    fake = _FakeApprise()
    await _notifier(fake).notify(Audience.COMMUNITY, "épisode trouvé", Severity.INFO)
    call = fake.sent[-1]
    assert call["tag"] == "community"
    assert call["body"] == "[titar-node-1] épisode trouvé"
    assert call["notify_type"] == apprise.NotifyType.INFO


@pytest.mark.asyncio
async def test_severity_maps_to_failure() -> None:
    fake = _FakeApprise()
    await _notifier(fake).notify(Audience.OPERATIONS, "panne", Severity.ERROR)
    assert fake.sent[-1]["notify_type"] == apprise.NotifyType.FAILURE


@pytest.mark.asyncio
async def test_default_apprise_obj_is_built_from_targets() -> None:
    # Sans apprise_obj injecté, le notifier construit un vrai Apprise (aucune URL → no-op safe).
    notifier = AppriseNotifier((), node_id="n")
    await notifier.notify(Audience.COMMUNITY, "x", Severity.INFO)  # ne lève pas
```

- [ ] **Step 3 : lancer → échoue** — Run: `( cd packages/crawler && uv run pytest tests/adapters/observability/test_apprise_notifier.py --no-cov -q )` ; Expected: FAIL (`ImportError`).

- [ ] **Step 4 : implémenter `apprise_notifier.py`**

```python
"""Notifier apprise : route une notification par AUDIENCE via les tags apprise (E-D7).

Couche ADAPTER (implémente ``Notifier``). Au montage : ``add(url, tag=audience)`` pour chaque
cible. ``notify`` PRÉFIXE le corps du ``node_id`` (ID d'instance — indispensable côté COMMUNITY,
réseau distribué) et appelle ``async_notify(body, notify_type, tag)``. Aucune URL → no-op naturel
(apprise sans service rend ``None``). ``apprise_obj`` injectable pour le test (défaut : vrai
``apprise.Apprise``). Le timeout/l'absorption d'erreur sont dans le dispatcher (E-D13).

Pas de stubs apprise → ``# type: ignore`` ciblés (override mypy, Task 9)."""

from collections.abc import Sequence

import apprise

from emule_indexer.domain.observability.policy import Audience, Severity

# tuple (url, audience) — la config (Task 7) produit ces paires depuis ``local.yaml``.
NotificationTargets = Sequence[tuple[str, Audience]]

_NOTIFY_TYPES: dict[Severity, object] = {
    Severity.DEBUG: apprise.NotifyType.INFO,
    Severity.INFO: apprise.NotifyType.INFO,
    Severity.WARNING: apprise.NotifyType.WARNING,
    Severity.ERROR: apprise.NotifyType.FAILURE,
}


class AppriseNotifier:
    """Adapter ``Notifier`` : un canal apprise par audience (tag), corps préfixé du node_id."""

    def __init__(
        self,
        targets: NotificationTargets,
        *,
        node_id: str,
        apprise_obj: object | None = None,
    ) -> None:
        # Typé ``object`` à dessein : l'adapter ne dépend pas de la surface (non typée) d'apprise ;
        # les appels ``.add``/``.async_notify`` portent donc un ``# type: ignore[attr-defined]`` (utile).
        self._apprise: object = apprise.Apprise() if apprise_obj is None else apprise_obj
        for url, audience in targets:
            self._apprise.add(url, tag=audience.value)  # type: ignore[attr-defined]
        self._node_id = node_id

    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        await self._apprise.async_notify(  # type: ignore[attr-defined]
            body=f"[{self._node_id}] {body}",
            notify_type=_NOTIFY_TYPES[severity],
            tag=audience.value,
        )
```

- [ ] **Step 5 : lancer → passe** — Run: `( cd packages/crawler && uv run pytest tests/adapters/observability/test_apprise_notifier.py --no-cov -q )` ; Expected: PASS (4 passed).

- [ ] **Step 6 : gate + commit** (ajouter l'override mypy apprise si besoin — Task 9)

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/pyproject.toml uv.lock packages/crawler/src/emule_indexer/adapters/observability/apprise_notifier.py packages/crawler/tests/adapters/observability/test_apprise_notifier.py
git commit -m "feat(adapters): apprise notifier routed by audience (Plan E.1)"
```

---

## Task 7 : config — `ObservabilityConfig` (crawler) + `NotificationTarget` (local)

**Files:**
- Modify: `packages/crawler/src/emule_indexer/adapters/config/crawler_config.py`
- Modify: `packages/crawler/src/emule_indexer/adapters/config/local_config.py`
- Test: `packages/crawler/tests/adapters/config/test_crawler_config.py` (existant — ajouter des cas)
- Test: `packages/crawler/tests/adapters/config/test_local_config.py` (existant — ajouter des cas)

> Rappel split secrets (E-D2) : `crawler.yaml` (versionné) porte `observability.{log_level, metrics, notification_timeout_seconds}` ; `local.yaml` (gitignored) porte `observability.notifications` (URLs secrètes).

- [ ] **Step 1 : écrire les tests crawler** — ajouter à `tests/adapters/config/test_crawler_config.py` :

```python
from emule_indexer.adapters.config.crawler_config import (
    MetricsConfig,
    ObservabilityConfig,
)


def test_observability_absent_defaults_to_none(valid_crawler_raw: dict[str, object]) -> None:
    config = parse_crawler_config(valid_crawler_raw)
    assert config.observability is None


def test_observability_parsed(valid_crawler_raw: dict[str, object]) -> None:
    valid_crawler_raw["observability"] = {
        "log_level": "DEBUG",
        "metrics": {"enabled": True, "port": 9100},
        "notification_timeout_seconds": 3.0,
    }
    config = parse_crawler_config(valid_crawler_raw)
    assert config.observability == ObservabilityConfig(
        log_level="DEBUG",
        metrics=MetricsConfig(enabled=True, port=9100),
        notification_timeout_seconds=3.0,
    )


def test_observability_metrics_optional(valid_crawler_raw: dict[str, object]) -> None:
    valid_crawler_raw["observability"] = {"log_level": "INFO"}
    config = parse_crawler_config(valid_crawler_raw)
    assert config.observability == ObservabilityConfig(
        log_level="INFO", metrics=None, notification_timeout_seconds=5.0
    )


def test_observability_bad_log_level_rejected(valid_crawler_raw: dict[str, object]) -> None:
    valid_crawler_raw["observability"] = {"log_level": "LOUD"}
    with pytest.raises(ConfigError, match="log_level"):
        parse_crawler_config(valid_crawler_raw)
```

(Si `valid_crawler_raw` n'existe pas comme fixture, réutiliser le dict de config valide déjà employé dans ce fichier de test — adapter le nom.)

- [ ] **Step 2 : lancer → échoue** — Run: `( cd packages/crawler && uv run pytest tests/adapters/config/test_crawler_config.py --no-cov -q )` ; Expected: FAIL (`ImportError: MetricsConfig`).

- [ ] **Step 3 : implémenter dans `crawler_config.py`** — ajouter les dataclasses, le parseur, le champ, et un helper `_bool`/`_log_level`. Insérer après `VerifyConfig` :

```python
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class MetricsConfig:
    """Serveur de métriques Prometheus du crawler (E-D9). ``port`` = serveur HTTP dédié."""

    enabled: bool
    port: int


@dataclass(frozen=True)
class ObservabilityConfig:
    """Réglages d'observabilité NON secrets (``crawler.yaml``). Les URLs apprise sont dans
    ``local.yaml`` (E-D2). ``log_level`` pilote le logging global (bootstrap → setLevel)."""

    log_level: str
    metrics: MetricsConfig | None
    notification_timeout_seconds: float
```

Ajouter le champ à `CrawlerConfig` (après `verify`) :

```python
    observability: "ObservabilityConfig | None" = None
```

Ajouter un helper de bool (avant `parse_crawler_config`) :

```python
def _bool(mapping: dict[str, Any], key: str, what: str) -> bool:
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key} : booléen attendu, obtenu {value!r}")
    return value
```

Ajouter le parseur de section (avant `parse_crawler_config`) :

```python
def _parse_observability(raw: dict[str, Any]) -> ObservabilityConfig:
    log_level = raw.get("log_level", "INFO")
    if not isinstance(log_level, str) or log_level not in _LOG_LEVELS:
        raise ConfigError(
            f"observability.log_level : un de {sorted(_LOG_LEVELS)} attendu, obtenu {log_level!r}"
        )
    metrics: MetricsConfig | None = None
    if "metrics" in raw:
        metrics_raw = _require_mapping(raw["metrics"], "observability.metrics")
        metrics = MetricsConfig(
            enabled=_bool(metrics_raw, "enabled", "observability.metrics"),
            port=_positive_int(metrics_raw, "port", "observability.metrics"),
        )
    timeout = (
        _positive(raw, "notification_timeout_seconds", "observability")
        if "notification_timeout_seconds" in raw
        else 5.0
    )
    return ObservabilityConfig(
        log_level=log_level, metrics=metrics, notification_timeout_seconds=timeout
    )
```

Dans `parse_crawler_config`, avant le `return`, ajouter :

```python
    observability: ObservabilityConfig | None = None
    if "observability" in raw:
        observability = _parse_observability(
            _require_mapping(raw["observability"], "section 'observability'")
        )
```

Et passer `observability=observability` au `CrawlerConfig(...)` retourné.

- [ ] **Step 4 : lancer crawler config → passe** — Run: `( cd packages/crawler && uv run pytest tests/adapters/config/test_crawler_config.py --no-cov -q )` ; Expected: PASS.

- [ ] **Step 5 : écrire les tests local** — ajouter à `tests/adapters/config/test_local_config.py` :

```python
from emule_indexer.adapters.config.local_config import NotificationTarget
from emule_indexer.domain.observability.policy import Audience


def test_notifications_absent_is_empty(valid_local_raw: dict[str, object]) -> None:
    assert parse_local_config(valid_local_raw).notifications == ()


def test_notifications_parsed(valid_local_raw: dict[str, object]) -> None:
    valid_local_raw["observability"] = {
        "notifications": [
            {"url": "discord://a", "tag": "community"},
            {"url": "discord://b", "tag": "operations"},
        ]
    }
    assert parse_local_config(valid_local_raw).notifications == (
        NotificationTarget(url="discord://a", tag=Audience.COMMUNITY),
        NotificationTarget(url="discord://b", tag=Audience.OPERATIONS),
    )


def test_notifications_bad_tag_rejected(valid_local_raw: dict[str, object]) -> None:
    valid_local_raw["observability"] = {"notifications": [{"url": "x", "tag": "nope"}]}
    with pytest.raises(ConfigError, match="tag"):
        parse_local_config(valid_local_raw)
```

(Adapter `valid_local_raw` au dict de config locale valide déjà utilisé dans le fichier.)

- [ ] **Step 6 : implémenter dans `local_config.py`** — importer `Audience`, ajouter `NotificationTarget`, le champ, et le parsing. En tête :

```python
from emule_indexer.domain.observability.policy import Audience
```

Après `AmuleEndpoint` :

```python
@dataclass(frozen=True)
class NotificationTarget:
    """Une cible apprise (``local.yaml`` — secret). ``tag`` = l'audience consommatrice (E-D7)."""

    url: str
    tag: Audience
```

Champ sur `LocalConfig` (après `verifier_url`) :

```python
    notifications: tuple[NotificationTarget, ...] = ()
```

Parsing (avant le `return LocalConfig(...)`) :

```python
    notifications: list[NotificationTarget] = []
    if "observability" in raw:
        obs_raw = _require_mapping(raw["observability"], "section 'observability'")
        for index, entry in enumerate(obs_raw.get("notifications", [])):
            what = f"observability.notifications[{index}]"
            mapping = _require_mapping(entry, what)
            tag_raw = _require_str(mapping, "tag", what)
            try:
                tag = Audience(tag_raw)
            except ValueError as error:
                raise ConfigError(
                    f"{what}.tag : 'community' ou 'operations' attendu, obtenu {tag_raw!r}"
                ) from error
            notifications.append(
                NotificationTarget(url=_require_str(mapping, "url", what), tag=tag)
            )
```

Passer `notifications=tuple(notifications)` au `LocalConfig(...)` retourné.

- [ ] **Step 7 : lancer local config → passe** — Run: `( cd packages/crawler && uv run pytest tests/adapters/config/test_local_config.py --no-cov -q )` ; Expected: PASS.

- [ ] **Step 8 : gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/adapters/config packages/crawler/tests/adapters/config
git commit -m "feat(config): observability config (crawler) + notification targets (local) (Plan E.1)"
```

---

## Task 8 : `application/edge_state.py` (anti-spam edge-trigger)

**Files:**
- Create: `packages/crawler/src/emule_indexer/application/edge_state.py`
- Test: `packages/crawler/tests/application/test_edge_state.py`

> `EdgeState` est l'état mutable d'application (comme `BackoffRegistry`) qui calcule `first_occurrence` : `enter(condition)` rend `True` SEULEMENT à la transition vers actif ; `leave(condition)` réarme. Détenu par `CrawlerApp`, consommé par les boucles en E.2.

- [ ] **Step 1 : écrire le test**

Create `packages/crawler/tests/application/test_edge_state.py` :

```python
"""EdgeState : first_occurrence = transition vers actif ; leave réarme."""

from emule_indexer.application.edge_state import EdgeState


def test_enter_is_true_only_on_transition() -> None:
    state = EdgeState()
    assert state.enter("verifier_unavailable") is True   # 1re fois → transition
    assert state.enter("verifier_unavailable") is False  # déjà actif → pas de re-notif


def test_leave_rearms() -> None:
    state = EdgeState()
    state.enter("blind")
    assert state.leave("blind") is True    # était actif → transition de sortie
    assert state.leave("blind") is False   # déjà inactif
    assert state.enter("blind") is True    # réarmé → re-transition


def test_conditions_are_independent() -> None:
    state = EdgeState()
    assert state.enter("a") is True
    assert state.enter("b") is True
    assert state.enter("a") is False
```

- [ ] **Step 2 : lancer → échoue** — Run: `( cd packages/crawler && uv run pytest tests/application/test_edge_state.py --no-cov -q )` ; Expected: FAIL (`ImportError`).

- [ ] **Step 3 : implémenter `edge_state.py`**

```python
"""État edge-trigger : calcule ``first_occurrence`` pour l'anti-spam des notifications (E-D8).

Couche APPLICATION (état mutable inter-itérations, comme ``BackoffRegistry``). Détenu par
``CrawlerApp``, consulté par les boucles (E.2) : ``enter(condition)`` rend ``True`` SEULEMENT à
la transition vers actif (première occurrence d'une panne) ; ``leave(condition)`` réarme au
rétablissement. La métrique, elle, s'incrémente à CHAQUE occurrence (Prometheus veut l'état
brut) — l'edge-trigger ne gouverne que la notification. Mono-thread sur l'event loop → aucun
verrou. L'état est in-process (non persisté) : après un redémarrage, une panne en cours
re-notifie une fois (acceptable, E-D8)."""


class EdgeState:
    """Ensemble des conditions actuellement actives (en alerte)."""

    def __init__(self) -> None:
        self._active: set[str] = set()

    def enter(self, condition: str) -> bool:
        """Marque ``condition`` active. Rend ``True`` ssi c'est une TRANSITION (1re occurrence)."""
        if condition in self._active:
            return False
        self._active.add(condition)
        return True

    def leave(self, condition: str) -> bool:
        """Marque ``condition`` inactive. Rend ``True`` ssi elle était active (réarmement)."""
        if condition not in self._active:
            return False
        self._active.discard(condition)
        return True
```

- [ ] **Step 4 : lancer → passe** — Run: `( cd packages/crawler && uv run pytest tests/application/test_edge_state.py --no-cov -q )` ; Expected: PASS (3 passed).

- [ ] **Step 5 : gate + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/crawler/src/emule_indexer/application/edge_state.py packages/crawler/tests/application/test_edge_state.py
git commit -m "feat(application): edge-trigger state for notification anti-spam (Plan E.1)"
```

---

## Task 9 : overrides mypy (apprise / prometheus_client si besoin)

**Files:**
- Modify: `pyproject.toml` (racine) — section `[[tool.mypy.overrides]]`

> À ne faire que si `uv run mypy` se plaint de `import apprise` / `import prometheus_client` (pas de `py.typed`). `prometheus_client` est généralement typé ; `apprise` non.

- [ ] **Step 1 : lancer mypy pour constater** — Run: `uv run mypy` ; noter les modules manquant de stubs (`error: Skipping analyzing "apprise"...` ou `import-untyped`).

- [ ] **Step 2 : ajouter l'override** — dans `pyproject.toml` racine, à côté de l'override `re2` existant :

```toml
[[tool.mypy.overrides]]
module = ["apprise"]
ignore_missing_imports = true
```

(Ajouter `"prometheus_client"` à la liste `module` uniquement si mypy le réclame.)

- [ ] **Step 3 : gate complet + commit**

```bash
( cd packages/crawler && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add pyproject.toml
git commit -m "chore(mypy): ignore missing imports for apprise (Plan E.1)"
```

---

## Vérification finale du plan E.1 (à exécuter avant de clore)

- [ ] Gate COMPLET vert : `( cd packages/crawler && uv run pytest -q )` = **100 % branch**, `( cd packages/verifier && uv run pytest -q )` inchangé, `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint packages/crawler/src` verts.
- [ ] Vérifier qu'AUCUN module de prod existant n'a changé de comportement (seuls des fichiers NEUFS + des ajouts de config purement additifs).
- [ ] Le socle est prêt à être câblé par **E.2** (instrumentation des boucles + `CrawlerApp`).

---

## Hors périmètre de E.1 (→ E.2 / E.3)

- **Émission depuis les boucles** (`record_observation`/`search_worker`/`run_search_cycle`/`run_download_cycle`/`run_verification_cycle`), propagation du `network` (mapping `SearchChannel`→`ed2k`/`kad`), `count_pending` sur le repo de queue, câblage `CrawlerApp` (registre + `start_http_server` + injection `telemetry` + `CrawlerStarted`), bootstrap logging deux-temps → **Plan E.2**.
- **Verifier** (mini-loader YAML, `/metrics`, instrumentation `/verify`, logging) → **Plan E.3**.
