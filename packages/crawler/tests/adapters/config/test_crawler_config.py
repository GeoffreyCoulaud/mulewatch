from typing import Any

import pytest

from mulewatch.adapters.config.crawler_config import (
    AmuleEndpoint,
    BackoffConfig,
    ConfigError,
    CrawlerConfig,
    DownloadConfig,
    MetricsConfig,
    NotificationTarget,
    ObservabilityConfig,
    PortSyncConfig,
    VerifyConfig,
    WebuiConfig,
    parse_crawler_config,
)
from mulewatch.domain.observability.policy import Audience


def _minimal_raw() -> dict[str, Any]:
    """Valid policy + minimal wiring (amules without ${}, base paths) — observer mode."""
    return {
        "cycle_interval_seconds": 300.0,
        "search_poll_budget_seconds": 30.0,
        "search_poll_interval_seconds": 5.0,
        "keyword_pause_min_seconds": 1.0,
        "keyword_pause_max_seconds": 4.0,
        "backoff": {
            "base_seconds": 2.0,
            "cap_seconds": 300.0,
            "factor": 2.0,
            "jitter_ratio": 0.3,
        },
        "decision_poll_interval_seconds": 5.0,
        "shutdown_deadline_seconds": 10.0,
        "amules": [{"name": "amule-1", "host": "amuled", "port": 4712, "password": "secret"}],
        "catalog_db_path": "/data/catalog.db",
        "local_db_path": "/data/local.db",
    }


def _env() -> dict[str, str]:
    return {"AMULE_EC_PASSWORD": "s3cr3t"}


def _full_download_section() -> dict[str, Any]:
    return {
        "enabled": True,
        "poll_interval_seconds": 30.0,
        "disk_cap_bytes": 1_000_000_000,
        "endpoint": {"name": "amule-dl", "host": "amuled", "port": 4713, "password": "dl-secret"},
        "staging_dir": "/data/staging",
        "quarantine_dir": "/data/quarantine",
        "verifier_url": "http://verifier:8000",
        "verify": {"poll_interval_seconds": 10.0},  # no client_timeout → default 180
    }


def _full_port_sync_section() -> dict[str, Any]:
    return {
        "enabled": True,
        "poll_interval_seconds": 60.0,
        "restart_min_interval_seconds": 300.0,
        "gluetun_control_url": "http://gluetun:8000",
        "restarter_url": "http://docker-proxy:2375",
    }


# --------------------------------------------------------------------- policy


def test_parses_a_valid_config() -> None:
    config = parse_crawler_config(_minimal_raw(), _env())
    assert config == CrawlerConfig(
        cycle_interval_seconds=300.0,
        search_poll_budget_seconds=30.0,
        search_poll_interval_seconds=5.0,
        keyword_pause_min_seconds=1.0,
        keyword_pause_max_seconds=4.0,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=300.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=10.0,
        amules=(AmuleEndpoint(name="amule-1", host="amuled", port=4712, password="secret"),),
        catalog_db_path="/data/catalog.db",
        local_db_path="/data/local.db",
        node_id=None,
        observability=None,
        download=None,
        port_sync=None,
    )


def test_jitter_ratio_zero_is_accepted() -> None:
    raw = _minimal_raw()
    raw["backoff"]["jitter_ratio"] = 0.0  # 0 = no jitter (≥ 0 allowed)
    assert parse_crawler_config(raw, _env()).backoff.jitter_ratio == 0.0


def test_negative_jitter_ratio_is_fatal() -> None:
    raw = _minimal_raw()
    raw["backoff"]["jitter_ratio"] = -0.1
    with pytest.raises(ConfigError, match="≥ 0 expected"):
        parse_crawler_config(raw, _env())


def test_missing_key_is_fatal() -> None:
    raw = _minimal_raw()
    del raw["cycle_interval_seconds"]
    with pytest.raises(ConfigError, match="cycle_interval_seconds"):
        parse_crawler_config(raw, _env())


def test_non_numeric_value_is_fatal() -> None:
    raw = _minimal_raw()
    raw["cycle_interval_seconds"] = "souvent"
    with pytest.raises(ConfigError, match="number expected"):
        parse_crawler_config(raw, _env())


def test_bool_is_not_accepted_as_a_number() -> None:
    raw = _minimal_raw()
    raw["cycle_interval_seconds"] = True
    with pytest.raises(ConfigError, match="number expected"):
        parse_crawler_config(raw, _env())


def test_non_positive_value_is_fatal() -> None:
    raw = _minimal_raw()
    raw["search_poll_budget_seconds"] = 0
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


def test_backoff_section_must_be_a_mapping() -> None:
    raw = _minimal_raw()
    raw["backoff"] = [1, 2, 3]
    with pytest.raises(ConfigError, match="section 'backoff'"):
        parse_crawler_config(raw, _env())


def test_backoff_factor_below_one_is_fatal() -> None:
    raw = _minimal_raw()
    raw["backoff"]["factor"] = 0.5
    with pytest.raises(ConfigError, match="factor must be ≥ 1"):
        parse_crawler_config(raw, _env())


def test_backoff_cap_below_base_is_fatal() -> None:
    raw = _minimal_raw()
    raw["backoff"]["cap_seconds"] = 1.0
    raw["backoff"]["base_seconds"] = 10.0
    with pytest.raises(ConfigError, match="cap below floor"):
        parse_crawler_config(raw, _env())


def test_keyword_pause_max_below_min_is_fatal() -> None:
    raw = _minimal_raw()
    raw["keyword_pause_min_seconds"] = 5.0
    raw["keyword_pause_max_seconds"] = 1.0
    with pytest.raises(ConfigError, match="empty interval"):
        parse_crawler_config(raw, _env())


# ------------------------------------------------------------- amules (formerly local)


def test_node_id_override_is_kept() -> None:
    raw = _minimal_raw()
    raw["node_id"] = "fixed-node"
    assert parse_crawler_config(raw, _env()).node_id == "fixed-node"


def test_multiple_instances_are_parsed_in_order() -> None:
    raw = _minimal_raw()
    raw["amules"].append({"name": "amule-2", "host": "h2", "port": 4713, "password": "p2"})
    config = parse_crawler_config(raw, _env())
    assert [a.name for a in config.amules] == ["amule-1", "amule-2"]


def test_empty_amules_is_fatal() -> None:
    raw = _minimal_raw()
    raw["amules"] = []
    with pytest.raises(ConfigError, match="≥ 1 instance"):
        parse_crawler_config(raw, _env())


def test_amules_not_a_list_is_fatal() -> None:
    raw = _minimal_raw()
    raw["amules"] = {"name": "x"}
    with pytest.raises(ConfigError, match="NON-EMPTY list"):
        parse_crawler_config(raw, _env())


def test_instance_must_be_a_mapping() -> None:
    raw = _minimal_raw()
    raw["amules"] = ["pas-un-mapping"]
    with pytest.raises(ConfigError, match="mapping expected"):
        parse_crawler_config(raw, _env())


def test_duplicate_instance_name_is_fatal() -> None:
    raw = _minimal_raw()
    raw["amules"].append({"name": "amule-1", "host": "h2", "port": 4713, "password": "p2"})
    with pytest.raises(ConfigError, match="duplicate instance name"):
        parse_crawler_config(raw, _env())


def test_missing_string_field_is_fatal() -> None:
    raw = _minimal_raw()
    del raw["amules"][0]["host"]
    with pytest.raises(ConfigError, match="'host' missing"):
        parse_crawler_config(raw, _env())


def test_non_string_field_is_fatal() -> None:
    raw = _minimal_raw()
    raw["amules"][0]["host"] = 1234  # non-string → isinstance branch of _require_str
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_crawler_config(raw, _env())


def test_empty_string_field_is_fatal() -> None:
    raw = _minimal_raw()
    raw["amules"][0]["password"] = ""
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_crawler_config(raw, _env())


def test_missing_port_is_fatal() -> None:
    raw = _minimal_raw()
    del raw["amules"][0]["port"]
    with pytest.raises(ConfigError, match="'port' missing"):
        parse_crawler_config(raw, _env())


def test_out_of_range_port_is_fatal() -> None:
    raw = _minimal_raw()
    raw["amules"][0]["port"] = 70000
    with pytest.raises(ConfigError, match="1..65535"):
        parse_crawler_config(raw, _env())


def test_bool_port_is_rejected() -> None:
    raw = _minimal_raw()
    raw["amules"][0]["port"] = True
    with pytest.raises(ConfigError, match="1..65535"):
        parse_crawler_config(raw, _env())


def test_missing_db_path_is_fatal() -> None:
    raw = _minimal_raw()
    del raw["catalog_db_path"]
    with pytest.raises(ConfigError, match="catalog_db_path"):
        parse_crawler_config(raw, _env())


def test_empty_node_id_string_is_fatal() -> None:
    raw = _minimal_raw()
    raw["node_id"] = ""
    with pytest.raises(ConfigError, match="node_id"):
        parse_crawler_config(raw, _env())


# ----------------------------------------------------------- interpolation ${}


def test_password_interpolated_from_env() -> None:
    raw = _minimal_raw() | {
        "amules": [
            {"name": "a1", "host": "amuled", "port": 4712, "password": "${AMULE_EC_PASSWORD}"}
        ],
    }
    cfg = parse_crawler_config(raw, {"AMULE_EC_PASSWORD": "s3cr3t"})
    assert cfg.amules[0].password == "s3cr3t"


def test_missing_env_var_raises() -> None:
    raw = _minimal_raw() | {
        "amules": [
            {"name": "a1", "host": "amuled", "port": 4712, "password": "${AMULE_EC_PASSWORD}"}
        ],
    }
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})  # AMULE_EC_PASSWORD not set


# ----------------------------------------------------------------- download


def test_download_absent_is_observer() -> None:
    cfg = parse_crawler_config(_minimal_raw(), _env())
    assert cfg.download is None


def test_download_enabled_false_is_observer_without_requiring_wiring() -> None:
    # enabled:false ⇒ we do NOT read the rest: a missing verifier_url is NOT an error.
    raw = _minimal_raw() | {"download": {"enabled": False}}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.download is None


def test_download_section_without_enabled_defaults_to_observer() -> None:
    # enabled missing → default false → download None (missing-key branch of _bool_default).
    raw = _minimal_raw() | {"download": {"poll_interval_seconds": 30.0}}
    assert parse_crawler_config(raw, _env()).download is None


def test_download_enabled_non_bool_is_fatal() -> None:
    raw = _minimal_raw() | {"download": {"enabled": "oui"}}
    with pytest.raises(ConfigError, match="boolean expected"):
        parse_crawler_config(raw, _env())


def test_download_section_must_be_a_mapping() -> None:
    raw = _minimal_raw() | {"download": [1, 2]}
    with pytest.raises(ConfigError, match="section 'download'"):
        parse_crawler_config(raw, _env())


def test_download_enabled_true_requires_endpoint_and_dirs() -> None:
    raw = _minimal_raw() | {
        "download": {"enabled": True, "poll_interval_seconds": 30, "disk_cap_bytes": 1024}
    }  # wiring missing
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, _env())


def test_download_enabled_true_full_is_download_mode() -> None:
    raw = _minimal_raw() | {"download": _full_download_section()}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.download == DownloadConfig(
        poll_interval_seconds=30.0,
        disk_cap_bytes=1_000_000_000,
        endpoint=AmuleEndpoint(name="amule-dl", host="amuled", port=4713, password="dl-secret"),
        staging_dir="/data/staging",
        quarantine_dir="/data/quarantine",
        verifier_url="http://verifier:8000",
        verify=VerifyConfig(poll_interval_seconds=10.0, client_timeout_seconds=180.0),
    )


def test_download_verify_client_timeout_is_parsed_when_present() -> None:
    section = _full_download_section()
    section["verify"] = {"poll_interval_seconds": 10.0, "client_timeout_seconds": 240.0}
    raw = _minimal_raw() | {"download": section}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.download is not None
    assert cfg.download.verify.client_timeout_seconds == 240.0


def test_download_poll_interval_must_be_positive() -> None:
    section = _full_download_section() | {"poll_interval_seconds": 0.0}
    raw = _minimal_raw() | {"download": section}
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


def test_download_disk_cap_must_be_positive_integer() -> None:
    section = _full_download_section() | {"disk_cap_bytes": 0}
    raw = _minimal_raw() | {"download": section}
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


def test_download_disk_cap_key_is_required() -> None:
    # download enabled but no disk_cap_bytes → missing-key branch of _positive_int.
    section = _full_download_section()
    del section["disk_cap_bytes"]
    raw = _minimal_raw() | {"download": section}
    with pytest.raises(ConfigError, match="disk_cap_bytes"):
        parse_crawler_config(raw, _env())


def test_download_endpoint_secret_interpolated_from_env() -> None:
    section = _full_download_section()
    section["endpoint"] = {
        "name": "amule-dl",
        "host": "amuled",
        "port": 4713,
        "password": "${AMULE_EC_PASSWORD}",
    }
    raw = _minimal_raw() | {"download": section}
    cfg = parse_crawler_config(raw, {"AMULE_EC_PASSWORD": "s3cr3t"})
    assert cfg.download is not None
    assert cfg.download.endpoint.password == "s3cr3t"


# ----------------------------------------------------------------- port_sync


def test_port_sync_absent_is_off() -> None:
    assert parse_crawler_config(_minimal_raw(), _env()).port_sync is None


def test_port_sync_enabled_false_is_off() -> None:
    raw = _minimal_raw() | {"port_sync": {"enabled": False}}
    assert parse_crawler_config(raw, _env()).port_sync is None


def test_port_sync_enabled_true_full() -> None:
    raw = _minimal_raw() | {"port_sync": _full_port_sync_section()}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.port_sync == PortSyncConfig(
        poll_interval_seconds=60.0,
        restart_min_interval_seconds=300.0,
        gluetun_control_url="http://gluetun:8000",
        restarter_url="http://docker-proxy:2375",
    )


def test_port_sync_section_must_be_a_mapping() -> None:
    raw = _minimal_raw() | {"port_sync": [1, 2]}
    with pytest.raises(ConfigError, match="section 'port_sync'"):
        parse_crawler_config(raw, _env())


def test_port_sync_poll_interval_must_be_positive() -> None:
    section = _full_port_sync_section() | {"poll_interval_seconds": 0.0}
    raw = _minimal_raw() | {"port_sync": section}
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


def test_port_sync_restart_min_interval_must_be_positive() -> None:
    section = _full_port_sync_section() | {"restart_min_interval_seconds": 0.0}
    raw = _minimal_raw() | {"port_sync": section}
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


# ------------------------------------------------------------------- search


def test_search_keywords_defaults_to_keroro_and_titar_when_section_absent() -> None:
    config = parse_crawler_config(_minimal_raw(), {})
    assert config.search_keywords == ("keroro", "titar")


def test_search_keywords_defaults_when_section_present_without_keywords_key() -> None:
    raw = _minimal_raw()
    raw["search"] = {}
    config = parse_crawler_config(raw, {})
    assert config.search_keywords == ("keroro", "titar")


def test_search_keywords_read_from_section() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": ["keroro", "titar", "mission titar"]}
    config = parse_crawler_config(raw, {})
    assert config.search_keywords == ("keroro", "titar", "mission titar")


def test_search_keywords_rejects_empty_list() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": []}
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})


def test_search_keywords_rejects_non_string_entry() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": ["keroro", 42]}
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})


# ----------------------------------------------------------- observability


def test_observability_absent_defaults_to_none() -> None:
    assert parse_crawler_config(_minimal_raw(), _env()).observability is None


def test_observability_parsed() -> None:
    raw = _minimal_raw() | {
        "observability": {
            "log_level": "DEBUG",
            "metrics": {"enabled": True, "port": 9100},
            "notification_timeout_seconds": 3.0,
        }
    }
    cfg = parse_crawler_config(raw, _env())
    assert cfg.observability == ObservabilityConfig(
        log_level="DEBUG",
        metrics=MetricsConfig(enabled=True, port=9100),
        notification_timeout_seconds=3.0,
        notifications=(),
    )


def test_observability_metrics_optional() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "INFO"}}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.observability == ObservabilityConfig(
        log_level="INFO", metrics=None, notification_timeout_seconds=5.0, notifications=()
    )


def test_observability_bad_log_level_rejected() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "LOUD"}}
    with pytest.raises(ConfigError, match="log_level"):
        parse_crawler_config(raw, _env())


def test_observability_metrics_enabled_key_missing_rejected() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "INFO", "metrics": {"port": 9100}}}
    with pytest.raises(ConfigError, match="'enabled' missing"):
        parse_crawler_config(raw, _env())


def test_observability_metrics_enabled_non_bool_rejected() -> None:
    raw = _minimal_raw() | {
        "observability": {"log_level": "INFO", "metrics": {"enabled": 1, "port": 9100}}
    }
    with pytest.raises(ConfigError, match="boolean expected"):
        parse_crawler_config(raw, _env())


def test_notifications_absent_is_empty() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "INFO"}}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.observability is not None
    assert cfg.observability.notifications == ()


def test_notifications_parsed() -> None:
    raw = _minimal_raw() | {
        "observability": {
            "log_level": "INFO",
            "notifications": [
                {"url": "discord://a", "tag": "community"},
                {"url": "discord://b", "tag": "operations"},
            ],
        }
    }
    cfg = parse_crawler_config(raw, _env())
    assert cfg.observability is not None
    assert cfg.observability.notifications == (
        NotificationTarget(url="discord://a", tag=Audience.COMMUNITY),
        NotificationTarget(url="discord://b", tag=Audience.OPERATIONS),
    )


def test_notifications_bad_tag_rejected() -> None:
    raw = _minimal_raw() | {
        "observability": {"log_level": "INFO", "notifications": [{"url": "x", "tag": "nope"}]}
    }
    with pytest.raises(ConfigError, match="tag"):
        parse_crawler_config(raw, _env())


def test_notification_url_interpolated_substring() -> None:
    raw = _minimal_raw() | {
        "observability": {
            "log_level": "INFO",
            "notifications": [{"url": "discord://${WID}/${WTOK}", "tag": "operations"}],
        }
    }
    cfg = parse_crawler_config(raw, _env() | {"WID": "1", "WTOK": "t"})
    assert cfg.observability is not None
    assert cfg.observability.notifications[0].url == "discord://1/t"


# ------------------------------------------------------------------- webui


def test_webui_absent_defaults_to_enabled_on_localhost() -> None:
    # No webui section → the in-process webui is ON by default, bound to 127.0.0.1:8080.
    cfg = parse_crawler_config(_minimal_raw(), _env())
    assert cfg.webui == WebuiConfig(enabled=True, host="127.0.0.1", port=8080)


def test_webui_section_all_fields_parsed() -> None:
    raw = _minimal_raw() | {"webui": {"enabled": True, "host": "0.0.0.0", "port": 9000}}
    assert parse_crawler_config(raw, _env()).webui == WebuiConfig(
        enabled=True, host="0.0.0.0", port=9000
    )


def test_webui_enabled_defaults_true_when_key_absent_in_section() -> None:
    # Section present but no `enabled` key → default True (missing-key branch of _bool_default).
    raw = _minimal_raw() | {"webui": {"host": "0.0.0.0", "port": 9000}}
    assert parse_crawler_config(raw, _env()).webui.enabled is True


def test_webui_disabled_keeps_host_and_port_defaults() -> None:
    raw = _minimal_raw() | {"webui": {"enabled": False}}
    assert parse_crawler_config(raw, _env()).webui == WebuiConfig(
        enabled=False, host="127.0.0.1", port=8080
    )


def test_webui_host_defaults_when_absent() -> None:
    raw = _minimal_raw() | {"webui": {"port": 9000}}
    assert parse_crawler_config(raw, _env()).webui.host == "127.0.0.1"


def test_webui_port_defaults_when_absent() -> None:
    raw = _minimal_raw() | {"webui": {"host": "0.0.0.0"}}
    assert parse_crawler_config(raw, _env()).webui.port == 8080


def test_webui_host_interpolated_from_env() -> None:
    raw = _minimal_raw() | {"webui": {"host": "${WEBUI_BIND}"}}
    cfg = parse_crawler_config(raw, _env() | {"WEBUI_BIND": "0.0.0.0"})
    assert cfg.webui.host == "0.0.0.0"


def test_webui_section_must_be_a_mapping() -> None:
    raw = _minimal_raw() | {"webui": [1, 2]}
    with pytest.raises(ConfigError, match="section 'webui'"):
        parse_crawler_config(raw, _env())


def test_webui_enabled_non_bool_is_fatal() -> None:
    raw = _minimal_raw() | {"webui": {"enabled": "yes"}}
    with pytest.raises(ConfigError, match="boolean expected"):
        parse_crawler_config(raw, _env())


def test_webui_host_non_string_is_fatal() -> None:
    raw = _minimal_raw() | {"webui": {"host": 1234}}
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_crawler_config(raw, _env())


def test_webui_out_of_range_port_is_fatal() -> None:
    raw = _minimal_raw() | {"webui": {"port": 70000}}
    with pytest.raises(ConfigError, match="1..65535"):
        parse_crawler_config(raw, _env())


def test_webui_bool_port_is_rejected() -> None:
    raw = _minimal_raw() | {"webui": {"port": True}}
    with pytest.raises(ConfigError, match="1..65535"):
        parse_crawler_config(raw, _env())
