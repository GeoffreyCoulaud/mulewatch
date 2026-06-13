"""Config de POLITIQUE du crawler (``crawler.yaml``, versionné — spec orchestration §5).

Cadences, budgets de polling, jitter, backoff, filet du nudge, délai d'arrêt. Parsé depuis
le dict YAML déjà chargé par ``load_yaml`` (l'I/O est dans ``yaml_loader``) en une
dataclass GELÉE, avec validation FAIL-FAST (bornes cohérentes → ``ConfigError``, refus de
démarrer, spec §5/§14). Aucune variable d'environnement (spec §3).
"""

from dataclasses import dataclass
from typing import Any


class ConfigError(Exception):
    """Config invalide → refus de démarrer (fail-fast, spec §5/§14)."""


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
class DownloadConfig:
    """Politique de téléchargement (spec download §3/§7). OPTIONNELLE (DÉCISION D11).

    ``poll_interval_seconds`` : cadence de relevé de la file de download (le nudge réveille
    plus tôt). ``disk_cap_bytes`` : plafond disque APPLICATIF — somme des ``size_bytes`` des
    downloads actifs au-dessus de laquelle on diffère (back-pressure gracieux, jamais
    d'abandon). Le quota INFRA (FS/Docker) est hors périmètre (Plan F).
    """

    poll_interval_seconds: float
    disk_cap_bytes: int


@dataclass(frozen=True)
class CrawlerConfig:
    """Politique du crawler (spec §5). Toutes les durées en SECONDES.

    ``cycle_interval_seconds`` : cadence visée d'un cycle complet. ``search_poll_budget_seconds``
    : temps max d'attente des résultats d'une recherche avant ``fetch``+passage au suivant.
    ``search_poll_interval_seconds`` : pas de polling de la progression. ``keyword_pause`` :
    bornes (min/max) du jitter inter-mots-clés. ``decision_poll_interval_seconds`` : filet
    du nudge (un consommateur futur re-vérifie la table). ``shutdown_deadline_seconds`` :
    borne dure de l'arrêt propre (dépassée → on force, spec §6).
    """

    cycle_interval_seconds: float
    search_poll_budget_seconds: float
    search_poll_interval_seconds: float
    keyword_pause_min_seconds: float
    keyword_pause_max_seconds: float
    backoff: BackoffConfig
    decision_poll_interval_seconds: float
    shutdown_deadline_seconds: float
    download: DownloadConfig | None = None


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


def parse_crawler_config(raw: dict[str, Any]) -> CrawlerConfig:
    """Construit un ``CrawlerConfig`` validé depuis le dict YAML parsé (fail-fast §5/§14)."""
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
    download: DownloadConfig | None = None
    if "download" in raw:
        download_raw = _require_mapping(raw["download"], "section 'download'")
        download = DownloadConfig(
            poll_interval_seconds=_positive(download_raw, "poll_interval_seconds", "download"),
            disk_cap_bytes=_positive_int(download_raw, "disk_cap_bytes", "download"),
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
        download=download,
    )
