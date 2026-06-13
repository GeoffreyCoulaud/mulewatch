from typing import Any

import pytest

from emule_indexer.adapters.config.crawler_config import ConfigError
from emule_indexer.adapters.config.local_config import (
    AmuleEndpoint,
    LocalConfig,
    parse_local_config,
)


def _valid_raw() -> dict[str, Any]:
    return {
        "amules": [
            {"name": "amule-1", "host": "gluetun", "port": 4712, "password": "secret"},
        ],
        "catalog_db_path": "/data/catalog.db",
        "local_db_path": "/data/local.db",
    }


def test_parses_a_valid_config_without_node_id() -> None:
    config = parse_local_config(_valid_raw())
    assert config == LocalConfig(
        amules=(AmuleEndpoint(name="amule-1", host="gluetun", port=4712, password="secret"),),
        catalog_db_path="/data/catalog.db",
        local_db_path="/data/local.db",
        node_id=None,
    )


def test_node_id_override_is_kept() -> None:
    raw = _valid_raw()
    raw["node_id"] = "fixed-node"
    assert parse_local_config(raw).node_id == "fixed-node"


def test_multiple_instances_are_parsed_in_order() -> None:
    raw = _valid_raw()
    raw["amules"].append({"name": "amule-2", "host": "h2", "port": 4713, "password": "p2"})
    config = parse_local_config(raw)
    assert [a.name for a in config.amules] == ["amule-1", "amule-2"]


def test_empty_amules_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"] = []
    with pytest.raises(ConfigError, match="≥ 1 instance"):
        parse_local_config(raw)


def test_amules_not_a_list_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"] = {"name": "x"}
    with pytest.raises(ConfigError, match="liste NON VIDE"):
        parse_local_config(raw)


def test_instance_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["amules"] = ["pas-un-mapping"]
    with pytest.raises(ConfigError, match="mapping attendu"):
        parse_local_config(raw)


def test_duplicate_instance_name_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"].append({"name": "amule-1", "host": "h2", "port": 4713, "password": "p2"})
    with pytest.raises(ConfigError, match="nom d'instance en double"):
        parse_local_config(raw)


def test_missing_string_field_is_fatal() -> None:
    raw = _valid_raw()
    del raw["amules"][0]["host"]
    with pytest.raises(ConfigError, match="'host' manquante"):
        parse_local_config(raw)


def test_empty_string_field_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"][0]["password"] = ""
    with pytest.raises(ConfigError, match="chaîne non vide"):
        parse_local_config(raw)


def test_missing_port_is_fatal() -> None:
    raw = _valid_raw()
    del raw["amules"][0]["port"]
    with pytest.raises(ConfigError, match="'port' manquante"):
        parse_local_config(raw)


def test_out_of_range_port_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"][0]["port"] = 70000
    with pytest.raises(ConfigError, match="1..65535"):
        parse_local_config(raw)


def test_bool_port_is_rejected() -> None:
    raw = _valid_raw()
    raw["amules"][0]["port"] = True
    with pytest.raises(ConfigError, match="1..65535"):
        parse_local_config(raw)


def test_missing_db_path_is_fatal() -> None:
    raw = _valid_raw()
    del raw["catalog_db_path"]
    with pytest.raises(ConfigError, match="catalog_db_path"):
        parse_local_config(raw)


def test_empty_node_id_string_is_fatal() -> None:
    raw = _valid_raw()
    raw["node_id"] = ""
    with pytest.raises(ConfigError, match="node_id"):
        parse_local_config(raw)


def test_download_endpoint_is_optional() -> None:
    config = parse_local_config(_valid_raw())
    assert config.download_endpoint is None
    assert config.staging_dir is None
    assert config.quarantine_dir is None


def test_download_endpoint_is_parsed_when_present() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = {
        "name": "amule-dl",
        "host": "gluetun",
        "port": 4713,
        "password": "dl-secret",
    }
    raw["staging_dir"] = "/data/incoming"
    raw["quarantine_dir"] = "/data/quarantine"
    config = parse_local_config(raw)
    assert config.download_endpoint == AmuleEndpoint(
        name="amule-dl", host="gluetun", port=4713, password="dl-secret"
    )
    assert config.staging_dir == "/data/incoming"
    assert config.quarantine_dir == "/data/quarantine"


def test_download_endpoint_present_requires_dirs() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = {
        "name": "amule-dl",
        "host": "h",
        "port": 4713,
        "password": "p",
    }  # staging_dir/quarantine_dir manquants
    with pytest.raises(ConfigError, match="staging_dir"):
        parse_local_config(raw)


def test_download_endpoint_present_requires_quarantine_dir() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = {"name": "d", "host": "h", "port": 4713, "password": "p"}
    raw["staging_dir"] = "/data/incoming"  # quarantine_dir manquant
    with pytest.raises(ConfigError, match="quarantine_dir"):
        parse_local_config(raw)


def test_download_endpoint_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = "pas-un-mapping"
    with pytest.raises(ConfigError, match="download_endpoint"):
        parse_local_config(raw)


def test_download_endpoint_invalid_port_is_fatal() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = {"name": "d", "host": "h", "port": 0, "password": "p"}
    raw["staging_dir"] = "/s"
    raw["quarantine_dir"] = "/q"
    with pytest.raises(ConfigError, match="1..65535"):
        parse_local_config(raw)
