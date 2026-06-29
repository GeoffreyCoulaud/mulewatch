# Simplification du déploiement — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réduire le déploiement à « remplir un seul `.env` (secrets) + une commande », en
interpolant `${NAME}` dans une config crawler unifiée et en restructurant `deploy/`.

**Architecture:** Interpolation `${NAME}` **paresseuse** (à la consommation) et **par sous-chaîne**,
cantonnée à l'adapter de config (domaine pur préservé). Les parseurs `crawler_config` + `local_config`
fusionnent en un seul, dont les sections `download`/`port_sync` sont **présentes ⟺ activées**
(`enabled: true`), ce qui supprime les checks « solidaires » de la composition. `deploy/` passe à un
fragment `base.compose.yml` inclus par deux points d'entrée `gluetun`/`direct.compose.yml`.

**Tech Stack:** Python ≥ 3.12, pytest (100 % branch), mypy --strict, ruff, Docker Compose (`include:`).

## Global Constraints

- **Python ≥ 3.12.** Domaine `domain/` pur (aucune I/O) ; l'interpolation lit l'environnement → vit
  dans `adapters/config/`.
- **TDD strict** : écrire le test, le voir échouer, puis l'implémentation minimale. Tests = spec.
- **100 % branch coverage par package** (`--cov-fail-under=100`, `branch=true`). Exercer les **deux**
  côtés de chaque conditionnelle.
- **`mypy --strict`** sur `src` **et** `tests`. Chaque fonction de test annotée `-> None`, params typés.
- **`ruff`** (`E,F,I,UP,B,SIM`), **line-length 100** (code Python).
- **YAML** : en-tête court par fichier, commentaire seulement si non-évident, **80 colonnes max**
  (commentaires compris), lignes vides pour aérer (entre sections, autour des blocs de commentaires).
- **Conventional commits** (`feat`/`fix`/`refactor`/`docs`/`test`/`chore` + scope).
- **Le gate complet** (4× pytest, ruff check, ruff format --check, mypy, sqlfluff, check_templates)
  doit être vert en fin de chantier. Lancer pytest **par package** (`cd packages/crawler && uv run
  pytest`), jamais à la racine.
- Spec de référence : `docs/specs/2026-06-29-simplification-deploiement-design.md` (décisions D1–D9).

> **Niveau de détail.** Les modules **nouveaux** (interpolation) sont donnés en code complet. Les
> **refactors** de fichiers lourdement testés (`crawler_config`, `app`, `__main__`) sont donnés par
> signatures cibles + cas de test nommés + extraits clés : l'implémenteur a le code source sous les
> yeux et adapte les tests existants. Les **livrables de contenu** (compose, `crawler.yml`,
> `.env.example`) sont donnés intégralement (ils *sont* le livrable).

---

## File Structure

**Code (`packages/crawler/src/emule_indexer/`)**

- `adapters/config/errors.py` — **créé** : `ConfigError` (extrait de `crawler_config`, ré-exporté).
- `adapters/config/interpolation.py` — **créé** : `interpolate(value, env, what)`.
- `adapters/config/crawler_config.py` — **modifié** : parseur **unifié** (politique + câblage),
  `_require_str` interpole, sections `download`/`port_sync` présentes ⟺ activées.
- `adapters/config/local_config.py` — **supprimé** (fusionné).
- `composition/app.py` — **modifié** : un seul objet config ; trigger mode = `config.download is not
  None` ; suppression `_require_full_config`/`_require_port_sync_config`.
- `composition/__main__.py` — **modifié** : `--config` (fusionne `--crawler`/`--local`), passe
  `os.environ` à l'interpolation, `validate-config` adapté.

**Tests (`packages/crawler/tests/`)**

- `adapters/config/test_interpolation.py` — **créé**.
- `adapters/config/test_crawler_config.py` — **modifié** (absorbe les cas de `test_local_config.py`).
- `adapters/config/test_local_config.py` — **supprimé**.
- `composition/test_app.py`, `composition/test_main.py` — **modifiés**.

**Déploiement (`deploy/`)**

- `deploy/.env.example` — **créé** (rapatrié de la racine, épuré secrets).
- `deploy/base.compose.yml` — **créé** (depuis `compose.base.yaml`, service crawler unique).
- `deploy/gluetun.compose.yml`, `deploy/direct.compose.yml` — **créés** (via `include:`).
- `deploy/config/crawler/crawler.yml` — **créé** (fusion versionnée, `${…}`).
- `deploy/config/crawler/{matcher,targets}.yml` — **renommés** (`.yaml` → `.yml`).
- **Supprimés** : `/.env.example`, `deploy/compose.base.yaml`, `deploy/examples/`,
  `deploy/config/crawler/{crawler.yaml,observer.example.yaml,download.example.yaml}`,
  `deploy/config/verifier.example.yaml`.

**Smoke + docs** : `tests/smoke/`, `docs/runbooks/*`, specs, `CLAUDE.md`.

---

## Task 1 : Module d'interpolation `${NAME}`

**Files:**
- Create: `packages/crawler/src/emule_indexer/adapters/config/errors.py`
- Create: `packages/crawler/src/emule_indexer/adapters/config/interpolation.py`
- Modify: `packages/crawler/src/emule_indexer/adapters/config/crawler_config.py` (top : importer
  `ConfigError` depuis `errors`, le ré-exporter pour compat)
- Test: `packages/crawler/tests/adapters/config/test_interpolation.py`

**Interfaces:**
- Produces: `errors.ConfigError(Exception)` ; `interpolate(value: str, env:
  Mapping[str, str], what: str) -> str` — substitue chaque `${NAME}` (`NAME` =
  `[A-Za-z_][A-Za-z0-9_]*`) par `env[NAME]` ; lève `ConfigError` si une variable manque ; renvoie
  `value` inchangé si aucun motif.

- [ ] **Step 1 : Écrire les tests d'interpolation**

```python
# packages/crawler/tests/adapters/config/test_interpolation.py
import pytest

from emule_indexer.adapters.config.errors import ConfigError
from emule_indexer.adapters.config.interpolation import interpolate


def test_no_pattern_returns_value_unchanged() -> None:
    assert interpolate("plain value", {}, "champ") == "plain value"


def test_single_substitution() -> None:
    assert interpolate("${A}", {"A": "secret"}, "champ") == "secret"


def test_substring_substitution() -> None:
    env = {"ID": "111", "TOKEN": "xyz"}
    assert interpolate("discord://${ID}/${TOKEN}", env, "url") == "discord://111/xyz"


def test_repeated_variable() -> None:
    assert interpolate("${A}-${A}", {"A": "x"}, "champ") == "x-x"


def test_missing_variable_raises_naming_var_and_field() -> None:
    with pytest.raises(ConfigError) as err:
        interpolate("${MISSING}", {}, "amules[0].password")
    assert "MISSING" in str(err.value)
    assert "amules[0].password" in str(err.value)


def test_dollar_without_braces_is_literal() -> None:
    assert interpolate("price is $5", {}, "champ") == "price is $5"
```

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `( cd packages/crawler && uv run pytest tests/adapters/config/test_interpolation.py --no-cov -q )`
Expected: FAIL (`ModuleNotFoundError: ...config.errors` / `.interpolation`).

- [ ] **Step 3 : Créer `errors.py` et déplacer `ConfigError`**

```python
# packages/crawler/src/emule_indexer/adapters/config/errors.py
"""Erreur de config commune (fail-fast §5/§14), isolée pour casser un cycle d'import."""


class ConfigError(Exception):
    """Config invalide → refus de démarrer (fail-fast, spec §5/§14)."""
```

Dans `crawler_config.py`, **remplacer** la définition locale `class ConfigError(Exception): ...` par
un import ré-exporté (les consommateurs `from ...crawler_config import ConfigError` continuent de
marcher) :

```python
from emule_indexer.adapters.config.errors import ConfigError  # ré-exporté (compat)
```

- [ ] **Step 4 : Implémenter `interpolate`**

```python
# packages/crawler/src/emule_indexer/adapters/config/interpolation.py
"""Interpolation ${NAME} (sous-chaîne, fail-fast) — I/O env, donc dans l'adapter (spec D1/D3)."""

import re
from collections.abc import Mapping

from emule_indexer.adapters.config.errors import ConfigError

_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def interpolate(value: str, env: Mapping[str, str], what: str) -> str:
    """Substitue chaque ``${NAME}`` de ``value`` par ``env[NAME]``.

    ``what`` nomme le champ pour l'erreur. Variable absente ⇒ ``ConfigError`` (spec D1).
    Aucun motif ⇒ ``value`` renvoyé tel quel (les ``$`` isolés ne sont pas touchés).
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in env:
            raise ConfigError(
                f"{what} : variable d'environnement {name!r} référencée mais absente"
            )
        return env[name]

    return _PATTERN.sub(_replace, value)
```

- [ ] **Step 5 : Lancer les tests d'interpolation (sans cov)**

Run: `( cd packages/crawler && uv run pytest tests/adapters/config/test_interpolation.py --no-cov -q )`
Expected: PASS (6 tests).

- [ ] **Step 6 : Commit**

```bash
git add packages/crawler/src/emule_indexer/adapters/config/errors.py \
        packages/crawler/src/emule_indexer/adapters/config/interpolation.py \
        packages/crawler/src/emule_indexer/adapters/config/crawler_config.py \
        packages/crawler/tests/adapters/config/test_interpolation.py
git commit -m "feat(config): interpolation \${NAME} sous-chaîne fail-fast"
```

---

## Task 2 : Parseur de config crawler unifié

Fusionne `parse_local_config` dans `parse_crawler_config`, réorganise en sections, branche
l'interpolation, rend `download`/`port_sync` **présents ⟺ activés**.

**Files:**
- Modify: `packages/crawler/src/emule_indexer/adapters/config/crawler_config.py`
- Delete: `packages/crawler/src/emule_indexer/adapters/config/local_config.py`
- Modify: `packages/crawler/tests/adapters/config/test_crawler_config.py`
- Delete: `packages/crawler/tests/adapters/config/test_local_config.py`

**Interfaces:**
- Consumes: `interpolate(value, env, what)` (Task 1).
- Produces (dataclasses gelées + parseur) :

```python
@dataclass(frozen=True)
class AmuleEndpoint:        # déplacé depuis local_config
    name: str; host: str; port: int; password: str

@dataclass(frozen=True)
class NotificationTarget:   # déplacé depuis local_config
    url: str; tag: Audience

@dataclass(frozen=True)
class VerifyConfig:         # inchangé (poll_interval_seconds, client_timeout_seconds)
    ...

@dataclass(frozen=True)
class DownloadConfig:       # ÉLARGI : politique + câblage. Présent ⟺ enabled.
    poll_interval_seconds: float
    disk_cap_bytes: int
    endpoint: AmuleEndpoint
    staging_dir: str
    quarantine_dir: str
    verifier_url: str
    verify: VerifyConfig

@dataclass(frozen=True)
class PortSyncConfig:       # ÉLARGI : politique + câblage. Présent ⟺ enabled.
    poll_interval_seconds: float
    restart_min_interval_seconds: float
    gluetun_control_url: str
    restarter_url: str

@dataclass(frozen=True)
class ObservabilityConfig:  # gagne `notifications`
    log_level: str
    metrics: MetricsConfig | None
    notification_timeout_seconds: float
    notifications: tuple[NotificationTarget, ...]

@dataclass(frozen=True)
class CrawlerConfig:        # UNIFIÉ (politique + câblage)
    # politique (inchangée) : cycle_interval_seconds, search_*, keyword_pause_*, backoff,
    #   decision_poll_interval_seconds, shutdown_deadline_seconds
    # câblage (ex-local) :
    amules: tuple[AmuleEndpoint, ...]
    catalog_db_path: str
    local_db_path: str
    node_id: str | None
    observability: ObservabilityConfig | None
    download: DownloadConfig | None      # None ⟺ mode observer
    port_sync: PortSyncConfig | None     # None ⟺ port-sync off

def parse_crawler_config(raw: dict[str, Any], env: Mapping[str, str]) -> CrawlerConfig: ...
```

**Règle clé (D5/D9)** : la section `download` se lit ainsi — `enabled` (bool **non interpolé**,
défaut `false`) absent/`false` ⇒ `download = None` (on **ne descend pas** dans le reste : paresse,
aucune var exigée) ; `enabled: true` ⇒ tous les champs câblage **requis** (sinon `ConfigError`).
Idem `port_sync`. `_require_str` interpole via `interpolate(value, env, what)`.

- [ ] **Step 1 : Écrire/migrer les tests du parseur unifié**

Repartir de `test_crawler_config.py` + des cas de `test_local_config.py`. Cas neufs à **ajouter**
explicitement (chacun annoté `-> None`) :

```python
def test_password_interpolated_from_env() -> None:
    raw = _minimal_raw() | {
        "amules": [{"name": "a1", "host": "amuled", "port": 4712,
                    "password": "${AMULE_EC_PASSWORD}"}],
    }
    cfg = parse_crawler_config(raw, {"AMULE_EC_PASSWORD": "s3cr3t"})
    assert cfg.amules[0].password == "s3cr3t"


def test_missing_env_var_raises() -> None:
    raw = _minimal_raw() | {
        "amules": [{"name": "a1", "host": "amuled", "port": 4712,
                    "password": "${AMULE_EC_PASSWORD}"}],
    }
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})  # AMULE_EC_PASSWORD absent


def test_download_absent_is_observer() -> None:
    cfg = parse_crawler_config(_minimal_raw(), _env())
    assert cfg.download is None


def test_download_enabled_false_is_observer_without_requiring_wiring() -> None:
    # enabled:false ⇒ on NE lit PAS le reste : verifier_url manquant n'est PAS une erreur.
    raw = _minimal_raw() | {"download": {"enabled": False}}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.download is None


def test_download_enabled_true_requires_endpoint_and_dirs() -> None:
    raw = _minimal_raw() | {"download": {"enabled": True, "poll_interval_seconds": 30,
                                         "disk_cap_bytes": 1024}}  # câblage manquant
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, _env())


def test_download_enabled_true_full_is_download_mode() -> None:
    raw = _minimal_raw() | {"download": _full_download_section()}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.download is not None
    assert cfg.download.verifier_url == "http://verifier:8000"
    assert cfg.download.verify.client_timeout_seconds == 180.0


def test_port_sync_enabled_false_is_off() -> None:
    raw = _minimal_raw() | {"port_sync": {"enabled": False}}
    assert parse_crawler_config(raw, _env()).port_sync is None


def test_port_sync_enabled_true_full() -> None:
    raw = _minimal_raw() | {"port_sync": _full_port_sync_section()}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.port_sync is not None
    assert cfg.port_sync.gluetun_control_url == "http://gluetun:8000"


def test_notification_url_interpolated_substring() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "INFO", "notifications": [
        {"url": "discord://${WID}/${WTOK}", "tag": "operations"}]}}
    cfg = parse_crawler_config(raw, _env() | {"WID": "1", "WTOK": "t"})
    assert cfg.observability is not None
    assert cfg.observability.notifications[0].url == "discord://1/t"
```

Helpers locaux à définir dans le fichier de test : `_minimal_raw()` (politique valide + `amules`
sans `${}` + `catalog_db_path`/`local_db_path`), `_env()` (mapping avec `AMULE_EC_PASSWORD`),
`_full_download_section()`, `_full_port_sync_section()`.

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `( cd packages/crawler && uv run pytest tests/adapters/config/test_crawler_config.py --no-cov -q )`
Expected: FAIL (signature `parse_crawler_config` à 1 arg, sections non reconnues).

- [ ] **Step 3 : Implémenter le parseur unifié**

Dans `crawler_config.py` : (a) déplacer `AmuleEndpoint`/`NotificationTarget` depuis `local_config`
+ les helpers `_require_str`/`_require_port` ; (b) `_require_str(mapping, key, what, env)` applique
`interpolate(value, env, what)` **après** lecture, **avant** la validation non-vide ; (c) parser les
sections `amules`, `catalog_db_path`, `local_db_path`, `node_id`, `observability` (+ notifications),
`download`, `port_sync` selon la règle `enabled`. Esquisse du parsing conditionnel :

```python
def _parse_download(raw: dict[str, Any], env: Mapping[str, str]) -> DownloadConfig | None:
    if "download" not in raw:
        return None
    section = _require_mapping(raw["download"], "section 'download'")
    if not _bool_default(section, "enabled", False, "download"):
        return None  # paresse : on ne lit/interpole RIEN d'autre
    endpoint_raw = _require_mapping(section.get("endpoint"), "download.endpoint")
    verify_raw = _require_mapping(section.get("verify", {}), "download.verify")
    return DownloadConfig(
        poll_interval_seconds=_positive(section, "poll_interval_seconds", "download"),
        disk_cap_bytes=_positive_int(section, "disk_cap_bytes", "download"),
        endpoint=_parse_endpoint(endpoint_raw, "download.endpoint", env),
        staging_dir=_require_str(section, "staging_dir", "download", env),
        quarantine_dir=_require_str(section, "quarantine_dir", "download", env),
        verifier_url=_require_str(section, "verifier_url", "download", env),
        verify=VerifyConfig(
            poll_interval_seconds=_positive(verify_raw, "poll_interval_seconds", "download.verify"),
            client_timeout_seconds=(
                _positive(verify_raw, "client_timeout_seconds", "download.verify")
                if "client_timeout_seconds" in verify_raw else 180.0
            ),
        ),
    )
```

(`_bool_default` : lit un bool optionnel avec défaut, refuse un non-bool — fail-fast. `_parse_endpoint`
factorise `AmuleEndpoint`. `port_sync` suit le même schéma.) Supprimer `local_config.py`.

- [ ] **Step 4 : Lancer les tests du parseur (sans cov)**

Run: `( cd packages/crawler && uv run pytest tests/adapters/config/test_crawler_config.py --no-cov -q )`
Expected: PASS.

- [ ] **Step 5 : Couverture 100 % branch du module config**

Run: `( cd packages/crawler && uv run pytest tests/adapters/config/ -q )`
Expected: PASS, **100 % branch**. Ajouter les cas manquants (enabled true/false, var présente/absente,
`verify` avec/sans `client_timeout_seconds`, défauts) jusqu'au vert.

- [ ] **Step 6 : Commit**

```bash
git add packages/crawler/src/emule_indexer/adapters/config/crawler_config.py \
        packages/crawler/tests/adapters/config/test_crawler_config.py
git rm packages/crawler/src/emule_indexer/adapters/config/local_config.py \
       packages/crawler/tests/adapters/config/test_local_config.py
git commit -m "refactor(config): parseur crawler unifié, sections download/port_sync à flag enabled"
```

---

## Task 3 : Composition — mode via `config.download`

**Files:**
- Modify: `packages/crawler/src/emule_indexer/composition/app.py`
- Modify: `packages/crawler/tests/composition/test_app.py`

**Interfaces:**
- Consumes: `CrawlerConfig` unifié (Task 2) — `config.download: DownloadConfig | None`,
  `config.port_sync: PortSyncConfig | None`, `config.amules`, `config.catalog_db_path`, etc.
- `CrawlerApp.__init__` ne prend **plus** `local_config` ; tout vient de `crawler_config`.

Changements :
- `CrawlerApp.__init__(*, crawler_config, targets, matcher_config, clock, rng, signal_hub, …)` —
  retirer le paramètre `local_config` ; remplacer toutes les lectures `self._local_config.X` par
  `self._crawler_config.X` (amules, catalog_db_path, local_db_path, node_id, download, port_sync,
  observability.notifications).
- **Trigger mode full** : `if self._crawler_config.download is not None:` (au lieu de
  `verifier_url is not None`). `verifier_url` se lit `self._crawler_config.download.verifier_url`.
- **Supprimer `_require_full_config`** : le parseur garantit déjà la complétude quand `download`
  est présent (enabled ⇒ tous champs). Idem **supprimer `_require_port_sync_config`** et la règle
  « 3 réglages solidaires ».
- `_port_sync_enabled()` ⇒ `return self._crawler_config.port_sync is not None`.
- `_build_full_loops`/`_build_port_sync_loop` : lire l'endpoint/dirs/urls depuis
  `config.download.*` / `config.port_sync.*` (plus de `assert ... is not None` issus des checks
  supprimés — la présence de l'objet le garantit déjà au typage).

- [ ] **Step 1 : Adapter les tests `test_app.py`**

Mettre à jour les constructions `CrawlerApp(...)` (retrait `local_config`, config unifiée).
S'assurer de couvrir : (a) observer (`download is None`) → pas de boucle download/verify ;
(b) download (`download` présent) → health-check verifier appelé, boucles armées ; (c) verifier
health KO → `ConfigError` ; (d) port_sync présent/absent. Réutiliser les fakes existants.

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `( cd packages/crawler && uv run pytest tests/composition/test_app.py --no-cov -q )`
Expected: FAIL (signature `CrawlerApp`, attributs).

- [ ] **Step 3 : Implémenter les changements dans `app.py`** (cf. liste ci-dessus).

- [ ] **Step 4 : Tests + couverture**

Run: `( cd packages/crawler && uv run pytest tests/composition/test_app.py -q )`
Expected: PASS, 100 % branch sur `app.py`.

- [ ] **Step 5 : Commit**

```bash
git add packages/crawler/src/emule_indexer/composition/app.py \
        packages/crawler/tests/composition/test_app.py
git commit -m "refactor(composition): mode full via config.download, suppression checks solidaires"
```

---

## Task 4 : CLI — `--config` unique + interpolation env

**Files:**
- Modify: `packages/crawler/src/emule_indexer/composition/__main__.py`
- Modify: `packages/crawler/tests/composition/test_main.py`

Changements :
- `_add_config_options` : remplacer `--crawler` + `--local` par **`--config`** (défaut
  `deploy/config/crawler/crawler.yml`) ; garder `--targets`/`--matcher` (défauts en `.yml`).
- `build_app` : `crawler_config = parse_crawler_config(load_yaml(args.config), os.environ)` ; ne
  plus parser `local`. Passer `crawler_config` seul à `CrawlerApp` (plus de `local_config`).
- `validate_config` : `parse_crawler_config(load_yaml(args.config), os.environ)` +
  `parse_targets` + `parse_matcher_config`. (Effet de bord voulu : valide aussi la présence des
  variables d'env référencées par les sections **actives**.)
- Mettre à jour la docstring du module (retrait « aucune variable d'environnement », mention de
  l'interpolation `${NAME}`).

- [ ] **Step 1 : Adapter `test_main.py`**

Couvrir : (a) run nominal avec `--config` (monkeypatch `os.environ` pour fournir
`AMULE_EC_PASSWORD`) ; (b) `validate-config` OK (code 0, « Config valide ») ; (c) `validate-config`
avec var d'env manquante → code 1, message clair ; (d) config invalide → code 1.

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `( cd packages/crawler && uv run pytest tests/composition/test_main.py --no-cov -q )`
Expected: FAIL.

- [ ] **Step 3 : Implémenter** (cf. liste). `import os` en tête.

- [ ] **Step 4 : Tests + couverture**

Run: `( cd packages/crawler && uv run pytest tests/composition/ -q )`
Expected: PASS, 100 % branch.

- [ ] **Step 5 : Gate crawler complet**

Run: `( cd packages/crawler && uv run pytest -q )` puis `uv run ruff check . && uv run mypy`
Expected: PASS, 100 % branch global crawler.

- [ ] **Step 6 : Commit**

```bash
git add packages/crawler/src/emule_indexer/composition/__main__.py \
        packages/crawler/tests/composition/test_main.py
git commit -m "refactor(cli): --config unique, interpolation depuis os.environ"
```

---

## Task 5 : `deploy/config/crawler/crawler.yml` unifié + renommages

**Files:**
- Create: `deploy/config/crawler/crawler.yml`
- Rename: `deploy/config/crawler/matcher.yaml` → `matcher.yml`,
  `deploy/config/crawler/targets.yaml` → `targets.yml`
- Delete: `deploy/config/crawler/{crawler.yaml,observer.example.yaml,download.example.yaml}`,
  `deploy/config/verifier.example.yaml`
- Rename: `deploy/config/verifier.yaml` → `deploy/config/verifier.yml` (cohérence)

- [ ] **Step 1 : Écrire `crawler.yml`** (80 cols, aéré)

```yaml
# Config du crawler — versionnée. Secrets via ${VAR} (depuis .env), reste en clair.
# Mode download : basculer download.enabled à true ET lancer --profile download.

cycle_interval_seconds: 300.0
search_poll_budget_seconds: 30.0
search_poll_interval_seconds: 5.0
keyword_pause_min_seconds: 1.0
keyword_pause_max_seconds: 4.0
decision_poll_interval_seconds: 5.0
shutdown_deadline_seconds: 10.0

backoff:
  base_seconds: 2.0
  cap_seconds: 300.0
  factor: 2.0
  jitter_ratio: 0.3

amules:
  - name: amule-1
    host: amuled
    port: 4712
    password: ${AMULE_EC_PASSWORD}

catalog_db_path: /data/catalog/catalog.db
local_db_path: /data/local/local.db

observability:
  log_level: INFO
  metrics: { enabled: true, port: 9090 }
  notification_timeout_seconds: 5.0
  # notifications:
  #   - url: "discord://${DISCORD_WEBHOOK_ID}/${DISCORD_WEBHOOK_TOKEN}"
  #     tag: operations

download:
  enabled: false
  poll_interval_seconds: 30.0
  disk_cap_bytes: 53687091200
  endpoint:
    name: amule-dl
    host: amuled
    port: 4712
    password: ${AMULE_EC_PASSWORD}
  staging_dir: /data/quarantine
  quarantine_dir: /data/quarantine
  verifier_url: http://verifier:8000
  verify:
    poll_interval_seconds: 10.0
    client_timeout_seconds: 180.0

port_sync:
  enabled: false
  poll_interval_seconds: 60.0
  restart_min_interval_seconds: 300.0
  gluetun_control_url: http://gluetun:8000
  restarter_url: http://docker-proxy:2375
```

- [ ] **Step 2 : Renommer matcher/targets/verifier en `.yml`**

```bash
git mv deploy/config/crawler/matcher.yaml deploy/config/crawler/matcher.yml
git mv deploy/config/crawler/targets.yaml deploy/config/crawler/targets.yml
git mv deploy/config/verifier.yaml deploy/config/verifier.yml
git rm deploy/config/crawler/crawler.yaml \
       deploy/config/crawler/observer.example.yaml \
       deploy/config/crawler/download.example.yaml \
       deploy/config/verifier.example.yaml
```

- [ ] **Step 3 : Valider la config localement (interpolation comprise)**

Run (depuis le repo, avec une var bidon) :
`AMULE_EC_PASSWORD=x ( cd packages/crawler && uv run python -m emule_indexer validate-config \
  --config ../../deploy/config/crawler/crawler.yml \
  --targets ../../deploy/config/crawler/targets.yml \
  --matcher ../../deploy/config/crawler/matcher.yml )`
Expected: « Config valide » (code 0).

- [ ] **Step 4 : Commit**

```bash
git add deploy/config/
git commit -m "feat(deploy): config crawler unifiée crawler.yml + renommages .yml"
```

---

## Task 6 : `deploy/base.compose.yml` (service crawler unique)

**Files:**
- Create: `deploy/base.compose.yml` (depuis `deploy/compose.base.yaml`)
- Delete: `deploy/compose.base.yaml`

Transformations vs l'actuel `compose.base.yaml` :
- Fusionner `crawler-observer` + `crawler-download` en **un** service `crawler` **sans `profiles:`**
  (toujours actif), attaché aux réseaux `ec` + `egress` + `verify-internal`, montant
  `./config/crawler/{crawler,matcher,targets}.yml` + les volumes `catalog-db`/`local-db`/`quarantine`.
- `command:` → `["--config", "/app/config/crawler.yml", "--targets", "/app/config/targets.yml",
  "--matcher", "/app/config/matcher.yml"]`.
- `webui` : `profiles: [webui]` (au lieu de `[observer, download]`).
- `verifier` + `freshclam` : `profiles: [download]` (inchangé). Monter `verifier.yml`.
- `prometheus` + `grafana` : `profiles: [monitoring]` (inchangé).
- Conserver `x-crawler-common` (image/durcissement) ; `build.context: ..` inchangé.
- Épurer les commentaires (en-tête court, 80 cols).

- [ ] **Step 1 : Écrire `base.compose.yml`** (reprendre l'existant, appliquer les transformations ;
  un seul service `crawler` ; chemins `./config/...` ; réseaux/volumes inchangés).

- [ ] **Step 2 : Supprimer l'ancien**

```bash
git rm deploy/compose.base.yaml
```

- [ ] **Step 3 : Vérifier la syntaxe du fragment**

Run: `GRAFANA_PWD=x docker compose -f deploy/base.compose.yml config >/dev/null && echo OK`
Expected: `OK` (le fragment seul est valide ; amuled viendra des couches).

- [ ] **Step 4 : Commit**

```bash
git add deploy/base.compose.yml
git commit -m "feat(deploy): base.compose.yml avec service crawler unique + profil webui"
```

---

## Task 7 : Points d'entrée `gluetun` / `direct` via `include:`

**Files:**
- Create: `deploy/gluetun.compose.yml`
- Create: `deploy/direct.compose.yml`
- Delete: `deploy/examples/` (tout)

- [ ] **Step 1 : Écrire `direct.compose.yml`** (le défaut le plus simple)

```yaml
# Stack sans VPN. amuled en réseau direct. Low-ID par défaut ; High-ID = ouvrir LISTEN_PORT.
# Lancer : docker compose -f deploy/direct.compose.yml [--profile download|monitoring|webui] up -d

include:
  - path: base.compose.yml

services:
  amuled:
    image: ngosang/amule:3.0.0-1
    container_name: amuled
    environment:
      GUI_PWD: ${AMULE_EC_PASSWORD:?}
    ports:
      - "${LISTEN_PORT:-4662}:4662"
      - "${LISTEN_PORT:-4662}:4662/udp"
    volumes:
      - amule-state:/home/amule/.aMule
      - quarantine:/data/quarantine
    networks:
      ec:
        aliases: [amuled]
    restart: unless-stopped
```

- [ ] **Step 2 : Écrire `gluetun.compose.yml`** (VPN + port-sync)

```yaml
# Stack VPN (gluetun). amuled partage la netns de gluetun ; docker-proxy pour le port-sync.
# High-ID auto : VPN_PORT_FORWARDING=on (.env) + port_sync.enabled: true (crawler.yml).
# Lancer : docker compose -f deploy/gluetun.compose.yml [--profile download|monitoring|webui] up -d

include:
  - path: base.compose.yml

services:
  gluetun:
    image: qmcgaw/gluetun:latest
    cap_add: [NET_ADMIN]
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      VPN_SERVICE_PROVIDER: ${VPN_SERVICE_PROVIDER:-protonvpn}
      VPN_TYPE: wireguard
      WIREGUARD_PRIVATE_KEY: ${WIREGUARD_PRIVATE_KEY:?}
      SERVER_COUNTRIES: ${SERVER_COUNTRIES:-}
      VPN_PORT_FORWARDING: ${VPN_PORT_FORWARDING:-off}
      HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"none"}'
    networks:
      ec:
        aliases: [amuled]
    restart: unless-stopped

  amuled:
    image: ngosang/amule:3.0.0-1
    container_name: amuled
    network_mode: "service:gluetun"
    depends_on: [gluetun]
    environment:
      GUI_PWD: ${AMULE_EC_PASSWORD:?}
    volumes:
      - amule-state:/home/amule/.aMule
      - quarantine:/data/quarantine
    restart: unless-stopped

  docker-proxy:
    image: wollomatic/socket-proxy:1.12.2
    profiles: [download]
    command:
      - "-loglevel=info"
      - "-allowfrom=0.0.0.0/0"
      - "-listenip=0.0.0.0"
      - "-proxyport=2375"
      - "-socketpath=/var/run/docker.sock"
      - "-allowPOST=/v1\\..{1,2}/containers/amuled/restart"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks: [ec]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    restart: unless-stopped
```

- [ ] **Step 3 : Supprimer `examples/`**

```bash
git rm -r deploy/examples
```

- [ ] **Step 4 : Vérifier les deux stacks complètes**

Run:
```
AMULE_EC_PASSWORD=x GRAFANA_PWD=y docker compose -f deploy/direct.compose.yml config >/dev/null && echo DIRECT_OK
AMULE_EC_PASSWORD=x GRAFANA_PWD=y WIREGUARD_PRIVATE_KEY=z \
  docker compose -f deploy/gluetun.compose.yml config >/dev/null && echo GLUETUN_OK
```
Expected: `DIRECT_OK` et `GLUETUN_OK` (l'`include:` résout `base.compose.yml`, services fusionnés).

- [ ] **Step 5 : Commit**

```bash
git add deploy/gluetun.compose.yml deploy/direct.compose.yml
git commit -m "feat(deploy): points d'entrée gluetun/direct via include, suppression examples/"
```

---

## Task 8 : `deploy/.env.example` rapatrié et épuré

**Files:**
- Create: `deploy/.env.example`
- Delete: `/.env.example` (racine)

- [ ] **Step 1 : Écrire `deploy/.env.example`** (secrets + vars services tiers uniquement, 80 cols)

```bash
# Secrets et variables d'environnement du déploiement.
# Copier en `.env` (gitignoré) et renseigner. Les flags applicatifs (download.enabled,
# port_sync.enabled) vivent dans config/crawler/crawler.yml, PAS ici.

# --- Toutes stacks ---
AMULE_EC_PASSWORD=change-me            # mot de passe EC (crawler <-> amuled), >= 12 caractères
IMAGE_TAG=latest                       # tag des images GHCR

# --- Stack gluetun (VPN) ---
WIREGUARD_PRIVATE_KEY=change-me        # clé privée WireGuard (espace client du fournisseur)
VPN_SERVICE_PROVIDER=protonvpn
SERVER_COUNTRIES=                      # ex: Switzerland,France (noms anglais)
VPN_PORT_FORWARDING=off                # on => High-ID (avec port_sync.enabled: true)

# --- Stack direct (sans VPN), High-ID optionnel ---
LISTEN_PORT=4662                       # port redirigé sur la box (NAT) pour le High-ID

# --- Monitoring (--profile monitoring) ---
GRAFANA_PWD=change-me
GRAFANA_PORT=3000
```

- [ ] **Step 2 : Supprimer l'ancien**

```bash
git rm .env.example
```

- [ ] **Step 3 : Vérifier le rendu d'une stack avec ce `.env`**

Run: `cp deploy/.env.example deploy/.env && ( cd deploy && docker compose -f direct.compose.yml \
  config >/dev/null ) && echo OK && rm deploy/.env`
Expected: `OK`.

- [ ] **Step 4 : Commit**

```bash
git add deploy/.env.example
git commit -m "feat(deploy): .env.example rapatrié dans deploy/, épuré (secrets only)"
```

---

## Task 9 : Réaligner le smoke (`tests/smoke/`)

**Files:**
- Modify: `tests/smoke/compose.yaml` (+ toute config de crawler qu'il monte)

Le smoke valide le câblage. L'aligner sur : nouveau `crawler.yml` unifié (ou son équivalent de
test), `--config` au lieu de `--crawler`/`--local`, nommage des fichiers. Conserver son rôle (pas de
VPN, pas de vrai serveur).

- [ ] **Step 1 : Lire `tests/smoke/compose.yaml` et ses configs montées** ; lister ce qui référence
  `--crawler`/`--local`, `compose.base.yaml`, `observer.yaml`/`download.yaml`, ou un chemin `.yaml`.

- [ ] **Step 2 : Mettre à jour** vers `--config .../crawler.yml`, l'`include:` de `base.compose.yml`
  si pertinent, et les `${…}` requis (fournir les vars dans l'invocation smoke).

- [ ] **Step 3 : Lancer le smoke** (shell réel, hors sandbox — Docker requis)

Run: `( cd tests/smoke && docker compose config >/dev/null && echo CONFIG_OK )` puis la cible smoke
documentée dans `docs/testing-guide.md` (`-m compose_integration`).
Expected: `CONFIG_OK` puis smoke vert. *(À faire exécuter par Geoffrey si le sandbox n'a pas Docker.)*

- [ ] **Step 4 : Commit**

```bash
git add tests/smoke/
git commit -m "test(smoke): réaligner sur la config crawler unifiée et le nommage compose"
```

---

## Task 10 : Réécrire le runbook de déploiement (quickstart)

**Files:**
- Modify: `docs/runbooks/deployment.md`

Réécriture en **quickstart** : (1) prérequis Docker en 2 lignes ; (2) matrice stack courte (direct
défaut, gluetun pour masquer l'IP, High-ID optionnel via port/VPN) ; (3) `cp deploy/.env.example
deploy/.env` + renseigner les secrets ; (4) éventuellement `download.enabled: true` dans
`crawler.yml` ; (5) **une** commande (`docker compose -f deploy/direct.compose.yml [--profile …] up
-d`). Déplacer/raccourcir : tuning RAM/clamav, durcissement, dépannage → renvoyer vers
`administration.md` / `troubleshooting.md`. Mettre en avant `direct` + observer (Low-ID) comme
chemin par défaut (D9), avec la mention « IP visible des pairs ».

- [ ] **Step 1 : Réécrire `deployment.md`** (cible : ~1 page de quickstart + liens).
- [ ] **Step 2 : Vérifier les liens internes** (anchors administration/troubleshooting existants).
- [ ] **Step 3 : Commit** : `git commit -m "docs(runbook): déploiement en quickstart"`.

---

## Task 11 : Aligner le reste de la doc + `CLAUDE.md`

**Files:**
- Modify: `docs/runbooks/administration.md`, `docs/runbooks/troubleshooting.md`
- Modify: `docs/specs/2026-06-10-crawler-mvp-design.md` (§3), `docs/specs/2026-06-20-deploiement-exemples-design.md`
- Modify: `CLAUDE.md`
- Modify: `docs/testing-guide.md` (si réf. aux noms de compose/smoke)

- [ ] **Step 1 : `CLAUDE.md`** — mettre à jour :
  - invariant « **Two run modes** » : *observer* = `download.enabled: false` (ou absent) ;
    *download* = `download.enabled: true` + section complète (plus « no `verifier_url` »).
  - ligne **Packaging** : `deploy/base.compose.yml` + `deploy/{gluetun,direct}.compose.yml` (plus
    `compose.base.yaml`/`examples/*`).
  - révision de l'invariant **§3** « aucune variable d'environnement » → interpolation `${NAME}`
    dans l'adapter de config (domaine toujours pur).
- [ ] **Step 2 : Specs** — `2026-06-10` §3 (env), `2026-06-20` (structure `include:`) : noter la
  révision avec renvoi au présent spec.
- [ ] **Step 3 : Runbooks admin/troubleshooting** — chemins de fichiers, activation port-sync
  (`port_sync.enabled`), références `crawler.yml`.
- [ ] **Step 4 : `testing-guide.md`** — ajuster toute référence aux noms de fichiers compose.
- [ ] **Step 5 : Gate complet final**

Run (les huit) :
```
( cd packages/matching && uv run pytest -q )
( cd packages/crawler  && uv run pytest -q )
( cd packages/verifier && uv run pytest -q )
( cd packages/webui    && uv run pytest -q )
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run sqlfluff lint packages/crawler/src
uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates
```
Expected: tout vert.

- [ ] **Step 6 : Commit** : `git commit -m "docs: aligner CLAUDE.md + runbooks + specs sur le nouveau déploiement"`.

---

## Self-Review (couverture spec)

- D1 interpolation paresseuse/sous-chaîne/fail-fast → **T1** (module) + **T2** (branchement dans
  `_require_str`, paresse par non-descente dans section désactivée).
- D2 secrets/env vs config/yaml → **T5** (`crawler.yml` : `${…}` pour secrets, `enabled` en dur) +
  **T8** (`.env` secrets only).
- D3 I/O dans l'adapter → **T1/T2** (interpolation dans `adapters/config`).
- D4 config unifiée → **T2** + **T5**.
- D5 sections à flag `enabled` → **T2** (présent ⟺ activé).
- D6 paire mode/profil → **T6** (profil `download`) + **T3** (trigger via `config.download`).
- D7 compose par `include:` → **T7**.
- D8 service crawler unique + profils → **T6**.
- D9 défaut direct/observer + High-ID optionnel → **T7** (`direct` défaut) + **T10** (runbook).
- §6 impact doc → **T10/T11**. §7 style YAML → appliqué T5–T8.

Dépendances : T1→T2→{T3,T4} ; T5 indépendant (mais validé par T1–T4 via `validate-config`) ;
T6→T7→T8 ; T9 dépend de T5–T8 ; T10/T11 après le reste (T11 porte le gate final).
