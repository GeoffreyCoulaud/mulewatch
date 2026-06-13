from typing import Any

import pytest

from emule_indexer.adapters.config.crawler_config import (
    BackoffConfig,
    ConfigError,
    CrawlerConfig,
    DownloadConfig,
    parse_crawler_config,
)


def _valid_raw() -> dict[str, Any]:
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
    }


def test_parses_a_valid_config() -> None:
    config = parse_crawler_config(_valid_raw())
    assert config == CrawlerConfig(
        cycle_interval_seconds=300.0,
        search_poll_budget_seconds=30.0,
        search_poll_interval_seconds=5.0,
        keyword_pause_min_seconds=1.0,
        keyword_pause_max_seconds=4.0,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=300.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=10.0,
    )


def test_jitter_ratio_zero_is_accepted() -> None:
    raw = _valid_raw()
    raw["backoff"]["jitter_ratio"] = 0.0  # 0 = aucun jitter (≥ 0 autorisé)
    assert parse_crawler_config(raw).backoff.jitter_ratio == 0.0


def test_negative_jitter_ratio_is_fatal() -> None:
    raw = _valid_raw()
    raw["backoff"]["jitter_ratio"] = -0.1
    with pytest.raises(ConfigError, match="≥ 0 attendu"):
        parse_crawler_config(raw)


def test_missing_key_is_fatal() -> None:
    raw = _valid_raw()
    del raw["cycle_interval_seconds"]
    with pytest.raises(ConfigError, match="cycle_interval_seconds"):
        parse_crawler_config(raw)


def test_non_numeric_value_is_fatal() -> None:
    raw = _valid_raw()
    raw["cycle_interval_seconds"] = "souvent"
    with pytest.raises(ConfigError, match="nombre attendu"):
        parse_crawler_config(raw)


def test_bool_is_not_accepted_as_a_number() -> None:
    raw = _valid_raw()
    raw["cycle_interval_seconds"] = True
    with pytest.raises(ConfigError, match="nombre attendu"):
        parse_crawler_config(raw)


def test_non_positive_value_is_fatal() -> None:
    raw = _valid_raw()
    raw["search_poll_budget_seconds"] = 0
    with pytest.raises(ConfigError, match="strictement positif"):
        parse_crawler_config(raw)


def test_backoff_section_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["backoff"] = [1, 2, 3]
    with pytest.raises(ConfigError, match="section 'backoff'"):
        parse_crawler_config(raw)


def test_backoff_factor_below_one_is_fatal() -> None:
    raw = _valid_raw()
    raw["backoff"]["factor"] = 0.5
    with pytest.raises(ConfigError, match="factor doit être ≥ 1"):
        parse_crawler_config(raw)


def test_backoff_cap_below_base_is_fatal() -> None:
    raw = _valid_raw()
    raw["backoff"]["cap_seconds"] = 1.0
    raw["backoff"]["base_seconds"] = 10.0
    with pytest.raises(ConfigError, match="plafond sous le plancher"):
        parse_crawler_config(raw)


def test_keyword_pause_max_below_min_is_fatal() -> None:
    raw = _valid_raw()
    raw["keyword_pause_min_seconds"] = 5.0
    raw["keyword_pause_max_seconds"] = 1.0
    with pytest.raises(ConfigError, match="intervalle vide"):
        parse_crawler_config(raw)


def test_download_section_is_optional() -> None:
    config = parse_crawler_config(_valid_raw())  # _valid_raw n'a pas de section download
    assert config.download is None


def test_download_section_is_parsed_when_present() -> None:
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 10.0, "disk_cap_bytes": 5_000_000_000}
    config = parse_crawler_config(raw)
    assert config.download == DownloadConfig(
        poll_interval_seconds=10.0, disk_cap_bytes=5_000_000_000
    )


def test_download_poll_interval_must_be_positive() -> None:
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 0.0, "disk_cap_bytes": 1}
    with pytest.raises(ConfigError, match="strictement positif"):
        parse_crawler_config(raw)


def test_download_disk_cap_must_be_positive_integer() -> None:
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 10.0, "disk_cap_bytes": 0}
    with pytest.raises(ConfigError, match="strictement positif"):
        parse_crawler_config(raw)


def test_download_disk_cap_key_is_required() -> None:
    # section download présente mais sans disk_cap_bytes → _positive_int branche clé manquante.
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 10.0}
    with pytest.raises(ConfigError, match="disk_cap_bytes"):
        parse_crawler_config(raw)


def test_download_disk_cap_must_be_an_integer() -> None:
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 10.0, "disk_cap_bytes": 50.5}
    with pytest.raises(ConfigError, match="strictement positif"):
        parse_crawler_config(raw)


def test_download_poll_interval_key_is_required() -> None:
    raw = _valid_raw()
    raw["download"] = {"disk_cap_bytes": 1}  # poll_interval_seconds manquant
    with pytest.raises(ConfigError, match="poll_interval_seconds"):
        parse_crawler_config(raw)


def test_download_section_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["download"] = [1, 2]
    with pytest.raises(ConfigError, match="section 'download'"):
        parse_crawler_config(raw)
