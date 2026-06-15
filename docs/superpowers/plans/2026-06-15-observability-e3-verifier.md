# Observabilité — Plan E.3 (verifier) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommandé) ou superpowers:executing-plans. Steps en checkbox (`- [ ]`). **Indépendant de E.2** (ne dépend que des deps partagées). Le verifier **n'importe RIEN de `emule_indexer`** (frontière de paquet — vérifier que ça reste vrai).

**Goal:** Doter le **verifier** (`download_verifier`) d'observabilité minimale — `log_level` par YAML, route `/metrics`, instrumentation technique de `/verify` — sans la machinerie d'événements (crawler-only, E-D10).

**Architecture:** Le verifier reste un microservice Starlette/uvicorn. `build_app` crée un `CollectorRegistry` dédié + un objet `VerifierMetrics`, monte `GET /metrics` (`generate_latest`), et `POST /verify` incrémente `emule_verifier_requests_total{verdict}` + observe `emule_verifier_analysis_duration_seconds`. Un **mini-loader YAML** (propre au paquet) lit `observability.log_level` ; `__main__` applique le bootstrap deux-temps avant `uvicorn.run`. `AnalysisConfig` (env) reste **inchangé**.

**Tech Stack:** `prometheus-client`, `pyyaml`, Starlette, pytest (`httpx.ASGITransport`, 100 % branch verifier), `mypy --strict`.

**Réfs :** spec `docs/superpowers/specs/2026-06-15-observability-design.md` (§5 verifier, §7 logging, §9, E-D10). **Écart au spec assumé :** le verifier n'a **pas** de port métriques séparé — `/metrics` est servi sur le port uvicorn existant ; le YAML verifier porte donc seulement `log_level` (pas de `metrics.{enabled,port}`). Mettre à jour le spec §5/§8/E-D10 en conséquence (Task 5).

**Gate (vert après CHAQUE tâche, depuis `packages/verifier/`) :**
```bash
( cd packages/verifier && uv run pytest -q ) && cd ../.. && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

---

## File Structure

**Créés :**
- `packages/verifier/src/download_verifier/metrics.py` — `VerifierMetrics` (registre + counter + histogram).
- `packages/verifier/src/download_verifier/obs_config.py` — mini-loader YAML (`log_level`).
- Tests miroirs.

**Modifiés :**
- `packages/verifier/pyproject.toml` — deps `prometheus-client`, `pyyaml`.
- `packages/verifier/src/download_verifier/app.py` — `/metrics` + instrumentation `/verify` + logger.
- `packages/verifier/src/download_verifier/__main__.py` — bootstrap logging deux-temps + lecture YAML.
- `config/` + compose — exemple `verifier.yaml` + montage (documenté).
- spec — écart « pas de port métriques verifier ».

---

## Task 1 : deps verifier + `metrics.py`

**Files:**
- Modify: `packages/verifier/pyproject.toml`
- Create: `packages/verifier/src/download_verifier/metrics.py`
- Test: `packages/verifier/tests/test_metrics.py`

- [ ] **Step 1 : ajouter les deps** — dans `packages/verifier/pyproject.toml`, `[project] dependencies`, ajouter `"prometheus-client>=0.21"` et `"pyyaml>=6.0.3"`. Puis `uv sync --dev`.

- [ ] **Step 2 : test** — Create `packages/verifier/tests/test_metrics.py` :

```python
"""VerifierMetrics : compteur par verdict + histogramme de durée, sur un registre dédié."""

from download_verifier.metrics import VerifierMetrics


def test_observe_increments_counter_and_histogram() -> None:
    metrics = VerifierMetrics()
    metrics.observe("clean", 0.5)
    metrics.observe("clean", 0.7)
    metrics.observe("malicious", 0.1)
    registry = metrics.registry
    assert registry.get_sample_value("emule_verifier_requests_total", {"verdict": "clean"}) == 2.0
    assert (
        registry.get_sample_value("emule_verifier_requests_total", {"verdict": "malicious"}) == 1.0
    )
    assert registry.get_sample_value("emule_verifier_analysis_duration_seconds_count") == 3.0
```

- [ ] **Step 3 : lancer → échoue** — `( cd packages/verifier && uv run pytest tests/test_metrics.py --no-cov -q )` ; Expected: FAIL (`ImportError`).

- [ ] **Step 4 : impl `metrics.py`**

```python
"""Métriques techniques du verifier (E-D10). Pas d'événements/notifications (crawler-only) :
un simple compteur de requêtes ``/verify`` par verdict + un histogramme de durée d'analyse, sur
un ``CollectorRegistry`` DÉDIÉ (exposé tel quel par ``/metrics``). Counter SANS ``_total`` (ajouté
par prometheus_client à l'exposition)."""

from prometheus_client import CollectorRegistry, Counter, Histogram


class VerifierMetrics:
    """Registre + compteur ``/verify`` par verdict + histogramme de durée."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._requests = Counter(
            "emule_verifier_requests",
            "Requêtes /verify traitées",
            ["verdict"],
            registry=self.registry,
        )
        self._duration = Histogram(
            "emule_verifier_analysis_duration_seconds",
            "Durée d'analyse d'un fichier (s)",
            registry=self.registry,
        )

    def observe(self, verdict: str, seconds: float) -> None:
        """Compte une requête (par verdict) et observe sa durée d'analyse."""
        self._requests.labels(verdict=verdict).inc()
        self._duration.observe(seconds)
```

- [ ] **Step 5 : lancer → passe** ; **gate + commit**

```bash
( cd packages/verifier && uv run pytest -q ) && cd ../.. && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/verifier/pyproject.toml uv.lock packages/verifier/src/download_verifier/metrics.py packages/verifier/tests/test_metrics.py
git commit -m "feat(verifier): VerifierMetrics (requests + analysis duration) (Plan E.3)"
```

---

## Task 2 : `/metrics` + instrumentation `/verify`

**Files:**
- Modify: `packages/verifier/src/download_verifier/app.py`
- Test: `packages/verifier/tests/test_app.py` (existant)

- [ ] **Step 1 : test** — dans `tests/test_app.py` (qui utilise déjà `build_app` + `httpx.ASGITransport`), ajouter. On monkeypatche `verify_file` dans le module `app` pour un verdict fixe (pas de spawn réel) :

```python
import httpx
import pytest

from download_verifier import app as app_module
from download_verifier.app import build_app


def _client(quarantine: Path) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app(quarantine)),
        base_url="http://testserver",
    )


@pytest.mark.asyncio
async def test_metrics_endpoint_responds(tmp_path: Path) -> None:
    async with _client(tmp_path) as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "emule_verifier_requests" in response.text


@pytest.mark.asyncio
async def test_verify_increments_request_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app_module, "verify_file", lambda path, expected: ("clean", {}, ())
    )
    (tmp_path / ("a" * 32)).write_bytes(b"x")
    async with _client(tmp_path) as client:
        verify = await client.post("/verify", json={"hash": "a" * 32, "expected": {}})
        metrics = await client.get("/metrics")
    assert verify.status_code == 200
    assert 'emule_verifier_requests_total{verdict="clean"} 1.0' in metrics.text
```

- [ ] **Step 2 : lancer → échoue** — `( cd packages/verifier && uv run pytest tests/test_app.py -k "metrics or counter" --no-cov -q )` ; Expected: FAIL (404 sur `/metrics`).

- [ ] **Step 3 : impl `app.py`** :

(a) Imports (en tête) :

```python
import logging
import time

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from download_verifier.metrics import VerifierMetrics

_logger = logging.getLogger("download_verifier.app")
```

(b) Instrumenter `verify_endpoint` — récupérer les métriques de l'app, mesurer, observer. Remplacer la fin de la fonction :

```python
    metrics: VerifierMetrics = request.app.state.metrics
    start = time.monotonic()
    verdict, real_meta, checks = verify_file(_quarantine_dir(request) / ed2k_hash, expected)
    metrics.observe(verdict, time.monotonic() - start)
    _logger.info("verify hash=%s → verdict=%s", ed2k_hash, verdict)
    return JSONResponse({"verdict": verdict, "real_meta": real_meta, "checks": checks})
```

(c) Ajouter l'endpoint `/metrics` (fonction module-level) :

```python
async def metrics_endpoint(request: Request) -> Response:
    """``GET /metrics`` : exposition Prometheus du registre dédié de l'app."""
    metrics: VerifierMetrics = request.app.state.metrics
    return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)
```

(d) `build_app` — créer les métriques et ajouter la route :

```python
def build_app(quarantine_dir: Path) -> Starlette:
    """Fabrique l'app Starlette liée à un dossier de quarantaine (testable in-process)."""
    application = Starlette(
        routes=[
            Route("/verify", verify_endpoint, methods=["POST"]),
            Route("/health", health_endpoint, methods=["GET"]),
            Route("/metrics", metrics_endpoint, methods=["GET"]),
        ]
    )
    application.state.quarantine_dir = quarantine_dir
    application.state.metrics = VerifierMetrics()
    return application
```

- [ ] **Step 4 : lancer → passe** ; **gate + commit**

```bash
( cd packages/verifier && uv run pytest -q ) && cd ../.. && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/verifier/src/download_verifier/app.py packages/verifier/tests/test_app.py
git commit -m "feat(verifier): /metrics endpoint + /verify instrumentation (Plan E.3)"
```

---

## Task 3 : mini-loader YAML d'observabilité (`log_level`)

**Files:**
- Create: `packages/verifier/src/download_verifier/obs_config.py`
- Test: `packages/verifier/tests/test_obs_config.py`

- [ ] **Step 1 : test** — Create `packages/verifier/tests/test_obs_config.py` :

```python
"""Mini-loader YAML d'observabilité du verifier : log_level validé, défaut INFO, fail-fast."""

from pathlib import Path

import pytest

from download_verifier.obs_config import ObsConfigError, load_observability


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "verifier.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_log_level(tmp_path: Path) -> None:
    path = _write(tmp_path, "observability:\n  log_level: DEBUG\n")
    assert load_observability(path).log_level == "DEBUG"


def test_defaults_to_info_when_absent(tmp_path: Path) -> None:
    path = _write(tmp_path, "other: 1\n")
    assert load_observability(path).log_level == "INFO"


def test_rejects_unknown_level(tmp_path: Path) -> None:
    path = _write(tmp_path, "observability:\n  log_level: LOUD\n")
    with pytest.raises(ObsConfigError, match="log_level"):
        load_observability(path)
```

- [ ] **Step 2 : lancer → échoue** — `( cd packages/verifier && uv run pytest tests/test_obs_config.py --no-cov -q )` ; Expected: FAIL (`ImportError`).

- [ ] **Step 3 : impl `obs_config.py`**

```python
"""Mini-loader YAML d'observabilité du verifier (E-D2/E-D10). N'importe RIEN de ``emule_indexer``
(frontière de paquet). Lit ``observability.log_level`` (défaut ``INFO``) ; niveau inconnu →
``ObsConfigError`` (fail-fast). ``AnalysisConfig`` (env) reste séparé et inchangé."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ObsConfigError(Exception):
    """Config d'observabilité invalide → refus de démarrer."""


@dataclass(frozen=True)
class ObservabilityConfig:
    """Réglages d'observabilité du verifier (seul ``log_level`` ; ``/metrics`` toujours exposé)."""

    log_level: str


def load_observability(path: Path) -> ObservabilityConfig:
    """Lit ``path`` (YAML), extrait ``observability.log_level`` (défaut INFO), valide."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = raw.get("observability", {}) if isinstance(raw, dict) else {}
    section = section if isinstance(section, dict) else {}
    log_level = section.get("log_level", "INFO")
    if log_level not in _LEVELS:
        raise ObsConfigError(
            f"observability.log_level : un de {sorted(_LEVELS)} attendu, obtenu {log_level!r}"
        )
    return ObservabilityConfig(log_level=log_level)
```

- [ ] **Step 4 : lancer → passe** ; **gate + commit**

```bash
( cd packages/verifier && uv run pytest -q ) && cd ../.. && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/verifier/src/download_verifier/obs_config.py packages/verifier/tests/test_obs_config.py
git commit -m "feat(verifier): YAML observability mini-loader (log_level) (Plan E.3)"
```

---

## Task 4 : bootstrap logging dans `__main__`

**Files:**
- Modify: `packages/verifier/src/download_verifier/__main__.py`
- Test: `packages/verifier/tests/test_main.py` (existant ou nouveau)

> Bootstrap deux-temps (E-D2) : `basicConfig(INFO)` au démarrage, puis `setLevel(log_level)` si un YAML de config est fourni via l'env `VERIFIER_CONFIG`. Extraire la logique testable hors de `uvicorn.run` (qui ne tourne pas en test → `# pragma: no cover`).

- [ ] **Step 1 : test** — Create/concaténer `packages/verifier/tests/test_main.py` :

```python
"""Le bootstrap logging applique le log_level du YAML si VERIFIER_CONFIG est fourni."""

import logging
from pathlib import Path

import pytest

from download_verifier.__main__ import configure_logging


def test_configure_logging_default_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERIFIER_CONFIG", raising=False)
    configure_logging({})
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "verifier.yaml"
    path.write_text("observability:\n  log_level: WARNING\n", encoding="utf-8")
    configure_logging({"VERIFIER_CONFIG": str(path)})
    assert logging.getLogger().level == logging.WARNING
```

- [ ] **Step 2 : lancer → échoue** — `( cd packages/verifier && uv run pytest tests/test_main.py --no-cov -q )` ; Expected: FAIL (`ImportError: configure_logging`).

- [ ] **Step 3 : impl `__main__.py`**

```python
"""Entrée du verifier : ``python -m download_verifier`` (spec verify §4 ; logging E-D2).

Bootstrap deux-temps : ``basicConfig(INFO)`` puis ``setLevel`` depuis le YAML d'observabilité
(``VERIFIER_CONFIG``) avant ``uvicorn.run``. Le dossier de quarantaine vient de ``QUARANTINE_DIR``
(lu par ``app.py`` à l'import)."""

import logging
import os
from collections.abc import Mapping
from pathlib import Path

import uvicorn

from download_verifier.obs_config import load_observability


def configure_logging(env: Mapping[str, str]) -> None:
    """Arme le logging (INFO), puis applique ``log_level`` du YAML ``VERIFIER_CONFIG`` s'il existe."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    config_path = env.get("VERIFIER_CONFIG")
    if config_path:
        log_level = load_observability(Path(config_path)).log_level
        logging.getLogger().setLevel(log_level)


def main() -> None:  # pragma: no cover
    """Configure le logging puis sert l'app verifier (host/port depuis l'environnement)."""
    configure_logging(os.environ)
    uvicorn.run(
        "download_verifier.app:app",
        host=os.environ.get("VERIFIER_HOST", "127.0.0.1"),
        port=int(os.environ.get("VERIFIER_PORT", "8000")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
```

> Si le `test_main.py` existant teste déjà `main()` en mockant `uvicorn.run`, conserver ce test ; sinon `main()` est `# pragma: no cover` (I/O réseau non testable) et seul `configure_logging` est couvert.

- [ ] **Step 4 : lancer → passe** ; **gate + commit**

```bash
( cd packages/verifier && uv run pytest -q ) && cd ../.. && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add packages/verifier/src/download_verifier/__main__.py packages/verifier/tests/test_main.py
git commit -m "feat(verifier): two-step logging bootstrap from YAML (Plan E.3)"
```

---

## Task 5 : config exemple + montage compose + alignement spec

**Files:**
- Create: `config/verifier.example.yaml`
- Modify: `compose.yaml` (montage + `VERIFIER_CONFIG`)
- Modify: `docs/runbook-deployment.md` (scrape `/metrics`)
- Modify: `docs/superpowers/specs/2026-06-15-observability-design.md` (écart port verifier)

- [ ] **Step 1 : `config/verifier.example.yaml`** (modèle ; le vrai n'est pas secret mais reste optionnel) :

```yaml
# Observabilité du verifier (E-D10) — log_level uniquement (/metrics toujours exposé sur le port
# du service). Monté dans le conteneur et pointé par VERIFIER_CONFIG.
observability:
  log_level: INFO
```

- [ ] **Step 2 : `compose.yaml`** — au service `verifier` : monter le fichier (lecture seule) et pointer `VERIFIER_CONFIG`. Ajouter sous `verifier` :

```yaml
    environment:
      VERIFIER_CONFIG: /config/verifier.yaml
    volumes:
      - ./config/verifier.yaml:/config/verifier.yaml:ro
```

(Adapter à la structure exacte du service `verifier` déjà présente ; ne PAS toucher le réseau `verify-internal: internal: true` — `/metrics` est scrapé via ce réseau, pas via egress.)

- [ ] **Step 3 : runbook** — ajouter une note « scrape Prometheus » : le crawler expose `:<port>/metrics` (config `observability.metrics.port`) ; le verifier expose `/metrics` sur son port de service (réseau `verify-internal`). Un Prometheus externe doit **rejoindre `ec`/`verify-internal`** ou les ports doivent être exposés ; le serveur Prometheus reste hors repo.

- [ ] **Step 4 : aligner le spec** — dans `docs/superpowers/specs/2026-06-15-observability-design.md`, préciser (§5 et E-D10) que le **verifier n'a pas de port métriques séparé** : `/metrics` est servi sur le port uvicorn du service, et son YAML porte **seulement `log_level`** (pas de `metrics.{enabled,port}`). Le crawler garde son serveur HTTP dédié + `metrics.{enabled,port}`.

- [ ] **Step 5 : valider compose + commit**

```bash
docker compose config >/dev/null     # la fusion reste valide
git add config/verifier.example.yaml compose.yaml docs/runbook-deployment.md docs/superpowers/specs/2026-06-15-observability-design.md
git commit -m "docs(verifier): config example + compose mount + scrape notes + spec alignment (Plan E.3)"
```

---

## Vérification finale E.3

- [ ] Gate complet vert : `( cd packages/verifier && uv run pytest -q )` = **100 % branch**, `( cd packages/crawler && uv run pytest -q )` inchangé, `uv run ruff check . && uv run ruff format --check . && uv run mypy`.
- [ ] Le verifier **n'importe toujours RIEN** de `emule_indexer` (`grep -r "emule_indexer" packages/verifier/src` = vide).
- [ ] `GET /metrics` répond (format Prometheus) ; `POST /verify` incrémente `emule_verifier_requests_total{verdict}`.
- [ ] `( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )` (si ffmpeg dispo) toujours vert — l'instrumentation n'a pas cassé l'analyse réelle.
- [ ] Optionnel (Docker) : `( cd packages/crawler && uv run pytest -m compose_integration --no-cov )` — la stack assemblée démarre toujours avec le montage `verifier.yaml`.

## Observabilité — Plan E COMPLET après E.3

E.1 (socle) + E.2 (crawler) + E.3 (verifier) ferment le Plan E. Envisager un jalon `v0.11.0-observability` (annoté, non poussé) après revue holistique. Suivi restant (hors Plan E) : clamav, port-sync/High-ID, ring noyau bwrap (cf. handoff packaging §5).
