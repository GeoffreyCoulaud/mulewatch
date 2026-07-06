"""UNIFIED crawler config (``crawler.yml``, versioned — deploy-simplification design).

Merges the former POLICY config (cadences, polling budgets, jitter, backoff, shutdown
deadline) and the former LOCAL config (EC endpoints + secrets + DB paths + download/port-sync
wiring). Parsed from the YAML dict already loaded by ``load_yaml`` (the file I/O is in
``yaml_loader``) into FROZEN dataclasses, with FAIL-FAST validation (consistent bounds,
fields present → ``ConfigError`` otherwise, refuse to start, spec §5/§14).

Deployment-sensitive values (secrets, URLs) are interpolated from the environment via
``${NAME}`` (substring, LAZY: a disabled section requires no variable, D1). The
``download`` and ``port_sync`` sections are present ⟺ enabled (``enabled: true``, D5):
``enabled`` absent/``false`` ⇒ section ``None`` (we don't descend into the rest); ``enabled:
true`` ⇒ all wiring fields required.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mulewatch.adapters.config.errors import ConfigError as ConfigError  # explicit re-export
from mulewatch.adapters.config.interpolation import interpolate
from mulewatch.domain.observability.policy import Audience


@dataclass(frozen=True)
class BackoffConfig:
    """Exponential backoff + jitter per (instance, channel) (spec §3/§5).

    ``jitter_ratio``: fraction of the nominal delay drawn as additional jitter
    (anti-thundering-herd) — 0 = no jitter, 0.3 = up to +30%.
    """

    base_seconds: float
    cap_seconds: float
    factor: float
    jitter_ratio: float


@dataclass(frozen=True)
class AmuleEndpoint:
    """An ``amuled`` daemon reachable over EC (spec §5). ``name`` is the instance label
    (logging, backoff/scheduler_state key); UNIQUE per config."""

    name: str
    host: str
    port: int
    password: str


@dataclass(frozen=True)
class NotificationTarget:
    """An apprise target (secret via ``${...}``). ``tag`` = the consuming audience (E-D7)."""

    url: str
    tag: Audience


@dataclass(frozen=True)
class VerifyConfig:
    """Verification policy (verify spec §6). Nested inside ``download``.

    ``poll_interval_seconds``: cadence at which the verify loop ``claim``s the queue when
    it is empty (the durable queue is the coupling — no dedicated nudge, DECISION DV5).
    ``client_timeout_seconds``: read timeout of the HTTP client to the verifier; MUST cover
    the worst-case analysis (clamav ~120-150 s), else a healthy-but-slow file goes to dead-letter
    on ReadTimeout (concurrency-async#1). Generous default; the connect stays short (adapter).
    """

    poll_interval_seconds: float
    client_timeout_seconds: float


@dataclass(frozen=True)
class DownloadConfig:
    """Download policy + wiring (download spec §3/§7). Present ⟺ ``enabled``.

    ``poll_interval_seconds``: cadence for polling the download queue (the nudge wakes it
    earlier). ``disk_cap_bytes``: APPLICATION-level disk cap (graceful back-pressure). ``endpoint``:
    2nd EC connection dedicated to download (DECISION D3). ``staging_dir`` = amuled's Incoming;
    ``quarantine_dir`` = the buffer zone before promotion. ``verifier_url`` = verify service.
    ``verify`` = the verification-loop policy.
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
    """High-ID port-sync policy + wiring (port-sync design §8.1). Present ⟺ ``enabled``.

    ``poll_interval_seconds``: cadence of the gluetun poll + port comparison.
    ``restart_min_interval_seconds``: rate-limit window for restarts.
    ``gluetun_control_url`` = gluetun control-server (forwarded port);
    ``restarter_url`` = docker-socket-proxy (amuled restart).
    """

    poll_interval_seconds: float
    restart_min_interval_seconds: float
    gluetun_control_url: str
    restarter_url: str


@dataclass(frozen=True)
class WebuiConfig:
    """In-process read-only webui HTTP surface (monolith-consolidation spec §8).

    ``enabled`` gates the WHOLE HTTP surface (``false`` ⇒ headless crawler, no port); ``host``/
    ``port`` are the uvicorn bind. The section is OPTIONAL: absent ⇒ enabled on 127.0.0.1:8080.
    Unlike ``download``/``port_sync`` it is NOT lazy (it carries no secret): every field is read
    (with defaults) whatever ``enabled`` is.
    """

    enabled: bool
    host: str
    port: int


_DEFAULT_WEBUI = WebuiConfig(enabled=True, host="127.0.0.1", port=8080)

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class MetricsConfig:
    """Crawler's Prometheus metrics server (E-D9). ``port`` = dedicated HTTP server."""

    enabled: bool
    port: int


@dataclass(frozen=True)
class ObservabilityConfig:
    """Observability settings (``crawler.yml``). ``log_level`` drives the global logging
    (bootstrap → setLevel); ``notifications`` = apprise targets (interpolated URLs, E-D2/E-D7)."""

    log_level: str
    metrics: MetricsConfig | None
    notification_timeout_seconds: float
    notifications: tuple[NotificationTarget, ...]


@dataclass(frozen=True)
class CrawlerConfig:
    """Unified crawler config (policy + wiring). All durations in SECONDS.

    Policy (unchanged): ``cycle_interval_seconds`` (target cadence of a cycle),
    ``search_poll_budget_seconds`` (max wait time for results), ``search_poll_interval_
    seconds`` (polling step), ``keyword_pause_{min,max}_seconds`` (inter-keyword jitter),
    ``backoff``, ``decision_poll_interval_seconds`` (nudge safety net),
    ``shutdown_deadline_seconds`` (hard bound of the clean shutdown).

    Wiring (ex-local): ``amules`` (EC pool), DB paths, ``node_id`` (``None`` = the one from
    ``local.db``), ``observability``, ``download`` (``None`` ⟺ observer mode), ``port_sync``
    (``None`` ⟺ port-sync off).

    ``search_keywords``: keywords queried by the search loop (``search`` section
    optional; default ``("keroro", "titar")`` if absent).
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
    search_keywords: tuple[str, ...] = ("keroro", "titar")
    observability: ObservabilityConfig | None = None
    download: DownloadConfig | None = None
    port_sync: PortSyncConfig | None = None
    webui: WebuiConfig = _DEFAULT_WEBUI


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what}: mapping expected, got {type(value).__name__}")
    return value


def _number(mapping: dict[str, Any], key: str, what: str) -> float:
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    value = mapping[key]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ConfigError(f"{what}.{key}: number expected, got {value!r}")
    return float(value)


def _positive(mapping: dict[str, Any], key: str, what: str) -> float:
    number = _number(mapping, key, what)
    if number <= 0:
        raise ConfigError(f"{what}.{key}: strictly positive expected, got {number}")
    return number


def _non_negative(mapping: dict[str, Any], key: str, what: str) -> float:
    number = _number(mapping, key, what)
    if number < 0:
        raise ConfigError(f"{what}.{key}: ≥ 0 expected, got {number}")
    return number


def _positive_int(mapping: dict[str, Any], key: str, what: str) -> int:
    """Strictly positive integer (bool refused), else ``ConfigError`` (fail-fast §5/§14)."""
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    value = mapping[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{what}.{key}: strictly positive integer expected, got {value!r}")
    return value


def _bool(mapping: dict[str, Any], key: str, what: str) -> bool:
    """REQUIRED boolean, else ``ConfigError`` (fail-fast §5/§14)."""
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key}: boolean expected, got {value!r}")
    return value


def _bool_default(mapping: dict[str, Any], key: str, default: bool, what: str) -> bool:
    """OPTIONAL boolean (default ``default``); refuses a non-bool — fail-fast (§5/§14)."""
    if key not in mapping:
        return default
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key}: boolean expected, got {value!r}")
    return value


def _require_str(mapping: dict[str, Any], key: str, what: str, env: Mapping[str, str]) -> str:
    """Non-empty string, ``${NAME}``-interpolated from ``env`` AFTER reading, BEFORE the non-empty
    check (fail-fast §5/§14). Missing env variable ⇒ ``ConfigError`` (via ``interpolate``)."""
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    value = mapping[key]
    if not isinstance(value, str):
        raise ConfigError(f"{what}.{key}: non-empty string expected, got {value!r}")
    interpolated = interpolate(value, env, f"{what}.{key}")
    if not interpolated:
        raise ConfigError(f"{what}.{key}: non-empty string expected, got {interpolated!r}")
    return interpolated


def _require_port(mapping: dict[str, Any], what: str) -> int:
    if "port" not in mapping:
        raise ConfigError(f"{what}: key 'port' missing")
    value = mapping["port"]
    if not isinstance(value, int) or isinstance(value, bool) or not (0 < value < 65536):
        raise ConfigError(f"{what}.port: integer 1..65535 expected, got {value!r}")
    return value


def _parse_endpoint(mapping: dict[str, Any], what: str, env: Mapping[str, str]) -> AmuleEndpoint:
    """Builds an ``AmuleEndpoint`` (name/host/password interpolated, port validated)."""
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
            f"observability.log_level: one of {sorted(_LOG_LEVELS)} expected, got {log_level!r}"
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
                f"{what}.tag: 'community' or 'operations' expected, got {tag_raw!r}"
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
        return None  # laziness: we read/interpolate NOTHING else (no variable required)
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


def _parse_search_keywords(raw: dict[str, Any]) -> tuple[str, ...]:
    """`search.keywords`: list of non-empty keywords. Absent → default (keroro, titar)."""
    if "search" not in raw:
        return ("keroro", "titar")
    section = _require_mapping(raw["search"], "section 'search'")
    if "keywords" not in section:
        return ("keroro", "titar")
    keywords = section["keywords"]
    if not isinstance(keywords, list) or not keywords:
        raise ConfigError("search.keywords: non-empty list of strings expected")
    result: list[str] = []
    for entry in keywords:
        if not isinstance(entry, str) or not entry:
            raise ConfigError(f"search.keywords: non-empty string expected, got {entry!r}")
        result.append(entry)
    return tuple(result)


def _parse_port_sync(raw: dict[str, Any], env: Mapping[str, str]) -> PortSyncConfig | None:
    if "port_sync" not in raw:
        return None
    section = _require_mapping(raw["port_sync"], "section 'port_sync'")
    if not _bool_default(section, "enabled", False, "port_sync"):
        return None  # laziness: we read/interpolate NOTHING else
    return PortSyncConfig(
        poll_interval_seconds=_positive(section, "poll_interval_seconds", "port_sync"),
        restart_min_interval_seconds=_positive(
            section, "restart_min_interval_seconds", "port_sync"
        ),
        gluetun_control_url=_require_str(section, "gluetun_control_url", "port_sync", env),
        restarter_url=_require_str(section, "restarter_url", "port_sync", env),
    )


def _parse_webui(raw: dict[str, Any], env: Mapping[str, str]) -> WebuiConfig:
    """`webui` section (optional). Absent ⇒ enabled on 127.0.0.1:8080. Present ⇒ ``enabled``
    (default True), ``host`` (interpolated, default 127.0.0.1), ``port`` (1..65535, default
    8080)."""
    if "webui" not in raw:
        return _DEFAULT_WEBUI
    section = _require_mapping(raw["webui"], "section 'webui'")
    host = _require_str(section, "host", "webui", env) if "host" in section else "127.0.0.1"
    port = _require_port(section, "webui") if "port" in section else 8080
    return WebuiConfig(
        enabled=_bool_default(section, "enabled", True, "webui"),
        host=host,
        port=port,
    )


def parse_crawler_config(raw: dict[str, Any], env: Mapping[str, str]) -> CrawlerConfig:
    """Builds a validated ``CrawlerConfig`` from the parsed YAML dict + the ``env`` environment
    (interpolation of ``${NAME}``). Fail-fast §5/§14: any inconsistency → ``ConfigError``."""
    backoff_raw = _require_mapping(raw.get("backoff", {}), "section 'backoff'")
    factor = _positive(backoff_raw, "factor", "backoff")
    if factor < 1:
        raise ConfigError(f"backoff.factor must be ≥ 1 (growth), got {factor}")
    backoff = BackoffConfig(
        base_seconds=_positive(backoff_raw, "base_seconds", "backoff"),
        cap_seconds=_positive(backoff_raw, "cap_seconds", "backoff"),
        factor=factor,
        jitter_ratio=_non_negative(backoff_raw, "jitter_ratio", "backoff"),
    )
    if backoff.cap_seconds < backoff.base_seconds:
        raise ConfigError(
            f"backoff.cap_seconds ({backoff.cap_seconds}) < base_seconds "
            f"({backoff.base_seconds}): cap below floor"
        )
    pause_min = _positive(raw, "keyword_pause_min_seconds", "crawler")
    pause_max = _positive(raw, "keyword_pause_max_seconds", "crawler")
    if pause_max < pause_min:
        raise ConfigError(
            f"keyword_pause_max_seconds ({pause_max}) < min ({pause_min}): empty interval"
        )
    amules_raw = raw.get("amules")
    if not isinstance(amules_raw, list) or not amules_raw:
        raise ConfigError("section 'amules': NON-EMPTY list expected (≥ 1 instance, spec §5)")
    endpoints: list[AmuleEndpoint] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(amules_raw):
        what = f"amules[{index}]"
        endpoint = _parse_endpoint(_require_mapping(entry, what), what, env)
        if endpoint.name in seen_names:
            raise ConfigError(
                f"duplicate instance name: {endpoint.name!r} (must be unique, spec §5)"
            )
        seen_names.add(endpoint.name)
        endpoints.append(endpoint)
    node_id_raw = raw.get("node_id")
    if node_id_raw is not None and (not isinstance(node_id_raw, str) or not node_id_raw):
        raise ConfigError(f"node_id: non-empty string or absent expected, got {node_id_raw!r}")
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
        search_keywords=_parse_search_keywords(raw),
        observability=observability,
        download=_parse_download(raw, env),
        port_sync=_parse_port_sync(raw, env),
        webui=_parse_webui(raw, env),
    )
