import argparse
import logging
from pathlib import Path

import pytest

from emule_indexer.composition import __main__ as entry
from emule_indexer.composition.app import CrawlerApp

_CONFIG = Path(__file__).resolve().parents[4] / "config"


def _args(**overrides: Path) -> argparse.Namespace:
    base = {
        "crawler": _CONFIG / "crawler.yaml",
        "local": _CONFIG / "local.example.yaml",
        "targets": _CONFIG / "targets.yaml",
        "matcher": _CONFIG / "matcher.yaml",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_app_assembles_a_crawler_app() -> None:
    # crawler.yaml sans observability → branche obs is None de build_app
    app = entry.build_app(_args())
    assert isinstance(app, CrawlerApp)


def test_build_app_applies_log_level_when_observability_configured(
    tmp_path: Path,
) -> None:
    """La branche observability is not None de build_app appelle setLevel."""
    # Crée un crawler.yaml minimal avec une section observability (log_level=DEBUG).
    crawler_yaml = tmp_path / "crawler_obs.yaml"
    crawler_yaml.write_text(
        ((_CONFIG / "crawler.yaml").read_text(encoding="utf-8"))
        + "\nobservability:\n  log_level: DEBUG\n  notification_timeout_seconds: 5.0\n",
        encoding="utf-8",
    )
    app = entry.build_app(_args(crawler=crawler_yaml))
    assert isinstance(app, CrawlerApp)
    # Le niveau racine a été appliqué (DEBUG=10).
    assert logging.getLogger().level == logging.DEBUG


class _SpyApp:
    """Faux app : sa coroutine ``run`` n'est jamais réellement exécutée (asyncio.run faux)."""

    async def run(self) -> None:  # pragma: no cover - jamais await (asyncio.run est faux)
        return None


def test_main_returns_zero_on_clean_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # ferme la coroutine sans la lancer

    monkeypatch.setattr("emule_indexer.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", lambda args: _SpyApp())
    assert entry.main([]) == 0


def test_main_renders_runtime_config_error_from_run_as_clean_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Le gate full-mode lève un ``ConfigError`` AU RUNTIME (health-check verifier KO / ensemble
    # download incomplet) depuis ``app.run()`` — pas depuis ``build_app``. ``main`` doit le rendre
    # avec le MÊME message propre + code de sortie 1 (et non un traceback nu).
    from emule_indexer.adapters.config.crawler_config import ConfigError

    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # ferme la coroutine sans la lancer
        raise ConfigError("verifier injoignable au démarrage (health-check KO)")

    monkeypatch.setattr("emule_indexer.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", lambda args: _SpyApp())
    code = entry.main([])
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_main_refuses_to_start_on_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_local = tmp_path / "local.yaml"
    bad_local.write_text("amules: []\ncatalog_db_path: c\nlocal_db_path: l\n", encoding="utf-8")
    code = entry.main(
        [
            "--crawler",
            str(_CONFIG / "crawler.yaml"),
            "--local",
            str(bad_local),
            "--targets",
            str(_CONFIG / "targets.yaml"),
            "--matcher",
            str(_CONFIG / "matcher.yaml"),
        ]
    )
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_main_refuses_on_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = entry.main(
        [
            "--crawler",
            str(tmp_path / "absent.yaml"),
            "--local",
            str(_CONFIG / "local.example.yaml"),
        ]
    )
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_default_args_point_at_config_dir() -> None:
    namespace = entry._parse_args([])
    assert namespace.crawler == Path("config/crawler.yaml")
    assert namespace.local == Path("config/local.yaml")


def test_package_main_shim_reexports_main() -> None:
    # `python -m emule_indexer` exécute le __main__ du PAQUET : il doit exposer la MÊME
    # fonction `main` que composition.__main__ (sinon la DoD §9.4 n'est pas tenue).
    from emule_indexer import __main__ as package_entry

    assert package_entry.main is entry.main
