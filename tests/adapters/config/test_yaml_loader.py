from pathlib import Path

import pytest

from emule_indexer.adapters.config.yaml_loader import YamlLoadError, load_yaml


def test_load_yaml_reads_mapping(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("tokens:\n  keroro: { keyword: keroro }\n", encoding="utf-8")
    data = load_yaml(path)
    assert data == {"tokens": {"keroro": {"keyword": "keroro"}}}


def test_load_yaml_parses_dates(tmp_path: Path) -> None:
    path = tmp_path / "t.yaml"
    path.write_text("episodes:\n  - { number: 62, broadcast_date: 2008-09-21 }\n", encoding="utf-8")
    import datetime

    data = load_yaml(path)
    assert data["episodes"][0]["broadcast_date"] == datetime.date(2008, 9, 21)


def test_load_yaml_non_mapping_root_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(YamlLoadError, match="mapping"):
        load_yaml(path)


def test_load_yaml_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(YamlLoadError, match="mapping"):
        load_yaml(path)


def test_load_yaml_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(YamlLoadError, match="illisible"):
        load_yaml(tmp_path / "does_not_exist.yaml")


def test_load_yaml_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("a: [1, 2\n", encoding="utf-8")  # crochet non fermé
    with pytest.raises(YamlLoadError, match="invalide"):
        load_yaml(path)
