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


def test_build_app_assembles_a_crawler_app(tmp_path: Path) -> None:
    # crawler.yaml SANS section observability → couvre la branche `observability is None`
    # de build_app (le crawler.yaml versionné EN A une désormais ; on la retire ici).
    full = (_CONFIG / "crawler.yaml").read_text(encoding="utf-8")
    without_obs = full.split("\nobservability:")[0] + "\n"
    crawler_yaml = tmp_path / "crawler_no_obs.yaml"
    crawler_yaml.write_text(without_obs, encoding="utf-8")
    app = entry.build_app(_args(crawler=crawler_yaml))
    assert isinstance(app, CrawlerApp)


def test_build_app_applies_log_level_when_observability_configured(
    tmp_path: Path,
) -> None:
    """La branche observability is not None de build_app appelle setLevel."""
    base = (_CONFIG / "crawler.yaml").read_text(encoding="utf-8").split("\nobservability:")[0]
    crawler_yaml = tmp_path / "crawler_obs.yaml"
    crawler_yaml.write_text(
        base + "\nobservability:\n  log_level: DEBUG\n  notification_timeout_seconds: 5.0\n",
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


# ---------------------------------------------------------------- sous-commande validate-config


def _valid_run_argv() -> list[str]:
    """Les 4 chemins de config VALIDES versionnés (local.example.yaml fait foi du local)."""
    return [
        "--crawler",
        str(_CONFIG / "crawler.yaml"),
        "--local",
        str(_CONFIG / "local.example.yaml"),
        "--targets",
        str(_CONFIG / "targets.yaml"),
        "--matcher",
        str(_CONFIG / "matcher.yaml"),
    ]


def test_bare_invocation_still_runs_the_crawler(monkeypatch: pytest.MonkeyPatch) -> None:
    # CONTRAINTE DURE de rétro-compat : SANS sous-commande, on retombe EXACTEMENT sur le
    # chemin run (build_app → asyncio.run). C'est ce que fait compose.yaml.
    seen: dict[str, object] = {}

    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # ferme la coroutine sans la lancer

    def fake_build_app(args: argparse.Namespace) -> _SpyApp:
        seen["crawler"] = args.crawler  # prouve qu'on est passé par _parse_args
        return _SpyApp()

    monkeypatch.setattr("emule_indexer.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", fake_build_app)
    assert entry.main(_valid_run_argv()) == 0
    assert seen["crawler"] == _CONFIG / "crawler.yaml"


def test_validate_config_does_not_start_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    # validate-config ne démarre RIEN : ni build_app, ni asyncio.run.
    def boom_run(coro: object) -> None:  # pragma: no cover - ne doit jamais être appelé
        raise AssertionError("asyncio.run ne doit pas être appelé par validate-config")

    def boom_build_app(args: argparse.Namespace) -> CrawlerApp:  # pragma: no cover
        raise AssertionError("build_app ne doit pas être appelé par validate-config")

    monkeypatch.setattr("emule_indexer.composition.__main__.asyncio.run", boom_run)
    monkeypatch.setattr(entry, "build_app", boom_build_app)
    assert entry.main(["validate-config", *_valid_run_argv()]) == 0


def test_validate_config_reports_valid(capsys: pytest.CaptureFixture[str]) -> None:
    code = entry.main(["validate-config", *_valid_run_argv()])
    assert code == 0
    assert "Config valide" in capsys.readouterr().out


def test_validate_config_defaults_point_at_config_dir() -> None:
    # Les options de validate-config ont les MÊMES défauts config/*.yaml que le run.
    namespace = entry._parse_validate_args([])
    assert namespace.crawler == Path("config/crawler.yaml")
    assert namespace.local == Path("config/local.yaml")
    assert namespace.targets == Path("config/targets.yaml")
    assert namespace.matcher == Path("config/matcher.yaml")


def test_validate_config_rejects_broken_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "crawler.yaml"
    broken.write_text("ports: [unclosed\n", encoding="utf-8")  # YAML syntaxiquement cassé
    argv = ["validate-config", "--crawler", str(broken), "--local", str(_CONFIG / "local.yaml")]
    code = entry.main(argv)
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_validate_config_rejects_config_error_in_local(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_local = tmp_path / "local.yaml"
    bad_local.write_text("amules: []\ncatalog_db_path: c\nlocal_db_path: l\n", encoding="utf-8")
    argv = [
        "validate-config",
        "--crawler",
        str(_CONFIG / "crawler.yaml"),
        "--local",
        str(bad_local),
        "--targets",
        str(_CONFIG / "targets.yaml"),
        "--matcher",
        str(_CONFIG / "matcher.yaml"),
    ]
    code = entry.main(argv)
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_validate_config_rejects_config_error_in_crawler(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_crawler = tmp_path / "crawler.yaml"
    bad_crawler.write_text("search: {}\n", encoding="utf-8")  # mapping valide mais incomplet
    argv = [
        "validate-config",
        "--crawler",
        str(bad_crawler),
        "--local",
        str(_CONFIG / "local.example.yaml"),
        "--targets",
        str(_CONFIG / "targets.yaml"),
        "--matcher",
        str(_CONFIG / "matcher.yaml"),
    ]
    code = entry.main(argv)
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_validate_config_rejects_matcher_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_matcher = tmp_path / "matcher.yaml"
    # rules non-liste → MatcherConfigError (parse structural)
    bad_matcher.write_text("tokens: {}\nrules: {}\n", encoding="utf-8")
    argv = [
        "validate-config",
        "--crawler",
        str(_CONFIG / "crawler.yaml"),
        "--local",
        str(_CONFIG / "local.example.yaml"),
        "--targets",
        str(_CONFIG / "targets.yaml"),
        "--matcher",
        str(bad_matcher),
    ]
    code = entry.main(argv)
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_validate_config_rejects_config_error_in_targets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_targets = tmp_path / "targets.yaml"
    bad_targets.write_text("episodes: nope\n", encoding="utf-8")  # episodes non-liste → ConfigError
    argv = [
        "validate-config",
        "--crawler",
        str(_CONFIG / "crawler.yaml"),
        "--local",
        str(_CONFIG / "local.example.yaml"),
        "--targets",
        str(bad_targets),
        "--matcher",
        str(_CONFIG / "matcher.yaml"),
    ]
    code = entry.main(argv)
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err
