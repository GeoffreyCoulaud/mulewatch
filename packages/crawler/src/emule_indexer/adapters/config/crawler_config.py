"""Config UNIFIÉE du crawler (``crawler.yml``, versionné — design simplification-déploiement).

Fusionne l'ancienne config de POLITIQUE (cadences, budgets de polling, jitter, backoff, délai
d'arrêt) et l'ancienne config LOCALE (endpoints EC + secrets + chemins des bases + câblage
download/port-sync). Parsée depuis le dict YAML déjà chargé par ``load_yaml`` (l'I/O fichier est
dans ``yaml_loader``) en dataclasses GELÉES, avec validation FAIL-FAST (bornes cohérentes,
champs présents → ``ConfigError`` sinon, refus de démarrer, spec §5/§14).

Les valeurs sensibles au déploiement (secrets, URLs) sont interpolées depuis l'environnement par
``${NAME}`` (sous-chaîne, PARESSEUX : une section désactivée n'exige aucune variable, D1). Les
sections ``download`` et ``port_sync`` sont présentes ⟺ activées (``enabled: true``, D5) :
``enabled`` absent/``false`` ⇒ section ``None`` (on ne descend pas dans le reste) ; ``enabled:
true`` ⇒ tous les champs de câblage requis.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from emule_indexer.adapters.config.errors import ConfigError as ConfigError  # ré-export explicite
from emule_indexer.adapters.config.interpolation import interpolate
from emule_indexer.domain.observability.policy import Audience


@dataclass(frozen=True)
class BackoffConfig:
    """Backoff exponentiel + jitter par (instance, canal) (spec §3/§5).

    ``jitter_ratio`` : fraction du délai nominal tirée en jitter additionnel
    (anti-thundering-herd) — 0 = aucun jitter, 0.3 = jusqu'à +30 %.
    """

    base_seconds: float
    cap_seconds: float
    factor: float
    jitter_ratio: float


@dataclass(frozen=True)
class AmuleEndpoint:
    """Un daemon ``amuled`` joignable par EC (spec §5). ``name`` est l'étiquette d'instance
    (logging, clé de backoff/scheduler_state) ; UNIQUE par config."""

    name: str
    host: str
    port: int
    password: str


@dataclass(frozen=True)
class NotificationTarget:
    """Une cible apprise (secret via ``${...}``). ``tag`` = l'audience consommatrice (E-D7)."""

    url: str
    tag: Audience


@dataclass(frozen=True)
class VerifyConfig:
    """Politique de vérification (spec verify §6). Imbriquée dans ``download``.

    ``poll_interval_seconds`` : cadence à laquelle la boucle de vérif ``claim`` la file quand
    elle est vide (la file durable est le couplage — pas de nudge dédié, DÉCISION DV5).
    ``client_timeout_seconds`` : timeout de lecture du client HTTP vers le verifier ; DOIT couvrir
    le pire cas d'analyse (clamav ~120-150 s) sinon un fichier sain mais lent part en dead-letter
    sur ReadTimeout (concurrency-async#1). Défaut généreux ; le connect reste court (adapter).
    """

    poll_interval_seconds: float
    client_timeout_seconds: float


@dataclass(frozen=True)
class DownloadConfig:
    """Politique + câblage du téléchargement (spec download §3/§7). Présent ⟺ ``enabled``.

    ``poll_interval_seconds`` : cadence de relevé de la file de download (le nudge réveille plus
    tôt). ``disk_cap_bytes`` : plafond disque APPLICATIF (back-pressure gracieux). ``endpoint`` :
    2e connexion EC dédiée au download (DÉCISION D3). ``staging_dir`` = l'Incoming d'amuled ;
    ``quarantine_dir`` = la zone tampon avant promotion. ``verifier_url`` = service de vérif.
    ``verify`` = politique de la boucle de vérification.
    """

    poll_interval_seconds: float
    disk_cap_bytes: int
    endpoint: AmuleEndpoint
    staging_dir: str
    quarantine_dir: str
    verifier_url: str
    verify: VerifyConfig


@dataclass(frozen=True)
class PortSyncConfig:
    """Politique + câblage du port-sync High-ID (design port-sync §8.1). Présent ⟺ ``enabled``.

    ``poll_interval_seconds`` : cadence du poll gluetun + comparaison du port. ``restart_min_
    interval_seconds`` : fenêtre de rate-limit des restarts. ``gluetun_control_url`` = control-
    server gluetun (port forwardé) ; ``restarter_url`` = docker-socket-proxy (restart d'amuled).
    """

    poll_interval_seconds: float
    restart_min_interval_seconds: float
    gluetun_control_url: str
    restarter_url: str


_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class MetricsConfig:
    """Serveur de métriques Prometheus du crawler (E-D9). ``port`` = serveur HTTP dédié."""

    enabled: bool
    port: int


@dataclass(frozen=True)
class ObservabilityConfig:
    """Réglages d'observabilité (``crawler.yml``). ``log_level`` pilote le logging global
    (bootstrap → setLevel) ; ``notifications`` = cibles apprise (URLs interpolées, E-D2/E-D7)."""

    log_level: str
    metrics: MetricsConfig | None
    notification_timeout_seconds: float
    notifications: tuple[NotificationTarget, ...]


@dataclass(frozen=True)
class CrawlerConfig:
    """Config unifiée du crawler (politique + câblage). Toutes les durées en SECONDES.

    Politique (inchangée) : ``cycle_interval_seconds`` (cadence visée d'un cycle),
    ``search_poll_budget_seconds`` (temps max d'attente des résultats), ``search_poll_interval_
    seconds`` (pas de polling), ``keyword_pause_{min,max}_seconds`` (jitter inter-mots-clés),
    ``backoff``, ``decision_poll_interval_seconds`` (filet du nudge), ``shutdown_deadline_seconds``
    (borne dure de l'arrêt propre).

    Câblage (ex-local) : ``amules`` (pool EC), chemins des bases, ``node_id`` (``None`` = celui de
    ``local.db``), ``observability``, ``download`` (``None`` ⟺ mode observer), ``port_sync``
    (``None`` ⟺ port-sync off).
    """

    cycle_interval_seconds: float
    search_poll_budget_seconds: float
    search_poll_interval_seconds: float
    keyword_pause_min_seconds: float
    keyword_pause_max_seconds: float
    backoff: BackoffConfig
    decision_poll_interval_seconds: float
    shutdown_deadline_seconds: float
    amules: tuple[AmuleEndpoint, ...]
    catalog_db_path: str
    local_db_path: str
    node_id: str | None
    observability: ObservabilityConfig | None = None
    download: DownloadConfig | None = None
    port_sync: PortSyncConfig | None = None


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} : mapping attendu, obtenu {type(value).__name__}")
    return value


def _number(mapping: dict[str, Any], key: str, what: str) -> float:
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ConfigError(f"{what}.{key} : nombre attendu, obtenu {value!r}")
    return float(value)


def _positive(mapping: dict[str, Any], key: str, what: str) -> float:
    number = _number(mapping, key, what)
    if number <= 0:
        raise ConfigError(f"{what}.{key} : strictement positif attendu, obtenu {number}")
    return number


def _non_negative(mapping: dict[str, Any], key: str, what: str) -> float:
    number = _number(mapping, key, what)
    if number < 0:
        raise ConfigError(f"{what}.{key} : ≥ 0 attendu, obtenu {number}")
    return number


def _positive_int(mapping: dict[str, Any], key: str, what: str) -> int:
    """Entier strictement positif (bool refusé), sinon ``ConfigError`` (fail-fast §5/§14)."""
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{what}.{key} : entier strictement positif attendu, obtenu {value!r}")
    return value


def _bool(mapping: dict[str, Any], key: str, what: str) -> bool:
    """Booléen REQUIS, sinon ``ConfigError`` (fail-fast §5/§14)."""
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key} : booléen attendu, obtenu {value!r}")
    return value


def _bool_default(mapping: dict[str, Any], key: str, default: bool, what: str) -> bool:
    """Booléen OPTIONNEL (défaut ``default``) ; refuse un non-bool — fail-fast (§5/§14)."""
    if key not in mapping:
        return default
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key} : booléen attendu, obtenu {value!r}")
    return value


def _require_str(mapping: dict[str, Any], key: str, what: str, env: Mapping[str, str]) -> str:
    """Chaîne non vide, interpolée ``${NAME}`` depuis ``env`` APRÈS lecture, AVANT le contrôle
    non-vide (fail-fast §5/§14). Variable d'env absente ⇒ ``ConfigError`` (via ``interpolate``)."""
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, str):
        raise ConfigError(f"{what}.{key} : chaîne non vide attendue, obtenu {value!r}")
    interpolated = interpolate(value, env, f"{what}.{key}")
    if not interpolated:
        raise ConfigError(f"{what}.{key} : chaîne non vide attendue, obtenu {interpolated!r}")
    return interpolated


def _require_port(mapping: dict[str, Any], what: str) -> int:
    if "port" not in mapping:
        raise ConfigError(f"{what} : clé 'port' manquante")
    value = mapping["port"]
    if not isinstance(value, int) or isinstance(value, bool) or not (0 < value < 65536):
        raise ConfigError(f"{what}.port : entier 1..65535 attendu, obtenu {value!r}")
    return value


def _parse_endpoint(mapping: dict[str, Any], what: str, env: Mapping[str, str]) -> AmuleEndpoint:
    """Construit un ``AmuleEndpoint`` (name/host/password interpolés, port validé)."""
    return AmuleEndpoint(
        name=_require_str(mapping, "name", what, env),
        host=_require_str(mapping, "host", what, env),
        port=_require_port(mapping, what),
        password=_require_str(mapping, "password", what, env),
    )


def _parse_observability(raw: dict[str, Any], env: Mapping[str, str]) -> ObservabilityConfig:
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
    notifications: list[NotificationTarget] = []
    for index, entry in enumerate(raw.get("notifications", [])):
        what = f"observability.notifications[{index}]"
        mapping = _require_mapping(entry, what)
        tag_raw = _require_str(mapping, "tag", what, env)
        try:
            tag = Audience(tag_raw)
        except ValueError as error:
            raise ConfigError(
                f"{what}.tag : 'community' ou 'operations' attendu, obtenu {tag_raw!r}"
            ) from error
        notifications.append(
            NotificationTarget(url=_require_str(mapping, "url", what, env), tag=tag)
        )
    return ObservabilityConfig(
        log_level=log_level,
        metrics=metrics,
        notification_timeout_seconds=timeout,
        notifications=tuple(notifications),
    )


def _parse_download(raw: dict[str, Any], env: Mapping[str, str]) -> DownloadConfig | None:
    if "download" not in raw:
        return None
    section = _require_mapping(raw["download"], "section 'download'")
    if not _bool_default(section, "enabled", False, "download"):
        return None  # paresse : on ne lit/interpole RIEN d'autre (aucune var exigée)
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
                if "client_timeout_seconds" in verify_raw
                else 180.0
            ),
        ),
    )


def _parse_port_sync(raw: dict[str, Any], env: Mapping[str, str]) -> PortSyncConfig | None:
    if "port_sync" not in raw:
        return None
    section = _require_mapping(raw["port_sync"], "section 'port_sync'")
    if not _bool_default(section, "enabled", False, "port_sync"):
        return None  # paresse : on ne lit/interpole RIEN d'autre
    return PortSyncConfig(
        poll_interval_seconds=_positive(section, "poll_interval_seconds", "port_sync"),
        restart_min_interval_seconds=_positive(
            section, "restart_min_interval_seconds", "port_sync"
        ),
        gluetun_control_url=_require_str(section, "gluetun_control_url", "port_sync", env),
        restarter_url=_require_str(section, "restarter_url", "port_sync", env),
    )


def parse_crawler_config(raw: dict[str, Any], env: Mapping[str, str]) -> CrawlerConfig:
    """Construit un ``CrawlerConfig`` validé depuis le dict YAML parsé + l'environnement ``env``
    (interpolation des ``${NAME}``). Fail-fast §5/§14 : la moindre incohérence → ``ConfigError``."""
    backoff_raw = _require_mapping(raw.get("backoff", {}), "section 'backoff'")
    factor = _positive(backoff_raw, "factor", "backoff")
    if factor < 1:
        raise ConfigError(f"backoff.factor doit être ≥ 1 (croissance), obtenu {factor}")
    backoff = BackoffConfig(
        base_seconds=_positive(backoff_raw, "base_seconds", "backoff"),
        cap_seconds=_positive(backoff_raw, "cap_seconds", "backoff"),
        factor=factor,
        jitter_ratio=_non_negative(backoff_raw, "jitter_ratio", "backoff"),
    )
    if backoff.cap_seconds < backoff.base_seconds:
        raise ConfigError(
            f"backoff.cap_seconds ({backoff.cap_seconds}) < base_seconds "
            f"({backoff.base_seconds}) : plafond sous le plancher"
        )
    pause_min = _positive(raw, "keyword_pause_min_seconds", "crawler")
    pause_max = _positive(raw, "keyword_pause_max_seconds", "crawler")
    if pause_max < pause_min:
        raise ConfigError(
            f"keyword_pause_max_seconds ({pause_max}) < min ({pause_min}) : intervalle vide"
        )
    amules_raw = raw.get("amules")
    if not isinstance(amules_raw, list) or not amules_raw:
        raise ConfigError("section 'amules' : liste NON VIDE attendue (≥ 1 instance, spec §5)")
    endpoints: list[AmuleEndpoint] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(amules_raw):
        what = f"amules[{index}]"
        endpoint = _parse_endpoint(_require_mapping(entry, what), what, env)
        if endpoint.name in seen_names:
            raise ConfigError(
                f"nom d'instance en double : {endpoint.name!r} (doit être unique, spec §5)"
            )
        seen_names.add(endpoint.name)
        endpoints.append(endpoint)
    node_id_raw = raw.get("node_id")
    if node_id_raw is not None and (not isinstance(node_id_raw, str) or not node_id_raw):
        raise ConfigError(f"node_id : chaîne non vide ou absent attendu, obtenu {node_id_raw!r}")
    observability: ObservabilityConfig | None = None
    if "observability" in raw:
        observability = _parse_observability(
            _require_mapping(raw["observability"], "section 'observability'"), env
        )
    return CrawlerConfig(
        cycle_interval_seconds=_positive(raw, "cycle_interval_seconds", "crawler"),
        search_poll_budget_seconds=_positive(raw, "search_poll_budget_seconds", "crawler"),
        search_poll_interval_seconds=_positive(raw, "search_poll_interval_seconds", "crawler"),
        keyword_pause_min_seconds=pause_min,
        keyword_pause_max_seconds=pause_max,
        backoff=backoff,
        decision_poll_interval_seconds=_positive(raw, "decision_poll_interval_seconds", "crawler"),
        shutdown_deadline_seconds=_positive(raw, "shutdown_deadline_seconds", "crawler"),
        amules=tuple(endpoints),
        catalog_db_path=_require_str(raw, "catalog_db_path", "crawler", env),
        local_db_path=_require_str(raw, "local_db_path", "crawler", env),
        node_id=node_id_raw,
        observability=observability,
        download=_parse_download(raw, env),
        port_sync=_parse_port_sync(raw, env),
    )
