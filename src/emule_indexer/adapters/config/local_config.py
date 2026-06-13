"""Config LOCALE (machine + secret) du crawler (``local.yaml``, gitignoré — spec §5).

Endpoints EC + mots de passe + chemins des bases + override optionnel du ``node_id``.
Parsé depuis le dict YAML chargé par ``load_yaml`` en dataclasses GELÉES, validation
FAIL-FAST (≥ 1 instance, champs présents → ``ConfigError`` sinon, spec §5/§14). Aucune
variable d'environnement (spec §3). ``local.example.yaml`` est versionné comme modèle ;
``local.yaml`` ne l'est jamais (``.gitignore``).
"""

from dataclasses import dataclass
from typing import Any

from emule_indexer.adapters.config.crawler_config import ConfigError


@dataclass(frozen=True)
class AmuleEndpoint:
    """Un daemon ``amuled`` joignable par EC (spec §5). ``name`` est l'étiquette d'instance
    (logging, clé de backoff/scheduler_state) ; UNIQUE par config."""

    name: str
    host: str
    port: int
    password: str


@dataclass(frozen=True)
class LocalConfig:
    """Config machine-spécifique (spec §5). ``node_id`` ``None`` = celui de ``local.db``."""

    amules: tuple[AmuleEndpoint, ...]
    catalog_db_path: str
    local_db_path: str
    node_id: str | None
    download_endpoint: AmuleEndpoint | None = None
    staging_dir: str | None = None
    quarantine_dir: str | None = None


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} : mapping attendu, obtenu {type(value).__name__}")
    return value


def _require_str(mapping: dict[str, Any], key: str, what: str) -> str:
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{what}.{key} : chaîne non vide attendue, obtenu {value!r}")
    return value


def _require_port(mapping: dict[str, Any], what: str) -> int:
    if "port" not in mapping:
        raise ConfigError(f"{what} : clé 'port' manquante")
    value = mapping["port"]
    if not isinstance(value, int) or isinstance(value, bool) or not (0 < value < 65536):
        raise ConfigError(f"{what}.port : entier 1..65535 attendu, obtenu {value!r}")
    return value


def parse_local_config(raw: dict[str, Any]) -> LocalConfig:
    """Construit un ``LocalConfig`` validé depuis le dict YAML parsé (fail-fast §5/§14)."""
    amules_raw = raw.get("amules")
    if not isinstance(amules_raw, list) or not amules_raw:
        raise ConfigError("section 'amules' : liste NON VIDE attendue (≥ 1 instance, spec §5)")
    endpoints: list[AmuleEndpoint] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(amules_raw):
        what = f"amules[{index}]"
        mapping = _require_mapping(entry, what)
        name = _require_str(mapping, "name", what)
        if name in seen_names:
            raise ConfigError(f"nom d'instance en double : {name!r} (doit être unique, spec §5)")
        seen_names.add(name)
        endpoints.append(
            AmuleEndpoint(
                name=name,
                host=_require_str(mapping, "host", what),
                port=_require_port(mapping, what),
                password=_require_str(mapping, "password", what),
            )
        )
    node_id_raw = raw.get("node_id")
    if node_id_raw is not None and (not isinstance(node_id_raw, str) or not node_id_raw):
        raise ConfigError(f"node_id : chaîne non vide ou absent attendu, obtenu {node_id_raw!r}")
    download_endpoint: AmuleEndpoint | None = None
    staging_dir: str | None = None
    quarantine_dir: str | None = None
    if "download_endpoint" in raw:
        endpoint_raw = _require_mapping(raw["download_endpoint"], "section 'download_endpoint'")
        download_endpoint = AmuleEndpoint(
            name=_require_str(endpoint_raw, "name", "download_endpoint"),
            host=_require_str(endpoint_raw, "host", "download_endpoint"),
            port=_require_port(endpoint_raw, "download_endpoint"),
            password=_require_str(endpoint_raw, "password", "download_endpoint"),
        )
        staging_dir = _require_str(raw, "staging_dir", "local")
        quarantine_dir = _require_str(raw, "quarantine_dir", "local")
    return LocalConfig(
        amules=tuple(endpoints),
        catalog_db_path=_require_str(raw, "catalog_db_path", "local"),
        local_db_path=_require_str(raw, "local_db_path", "local"),
        node_id=node_id_raw,
        download_endpoint=download_endpoint,
        staging_dir=staging_dir,
        quarantine_dir=quarantine_dir,
    )
