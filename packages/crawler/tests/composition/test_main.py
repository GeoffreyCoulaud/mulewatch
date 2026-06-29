import argparse
import logging
from pathlib import Path

import pytest

from emule_indexer.composition import __main__ as entry
from emule_indexer.composition.app import CrawlerApp

_CONFIG = Path(__file__).resolve().parents[4] / "deploy" / "config" / "crawler"

# Config crawler UNIFIÉE minimale (politique + câblage observer), secret par ${...}. Le fichier
# unifié versionné (deploy/config/crawler/crawler.yml) est créé par une tâche ultérieure ; les
# tests qui chargent réellement la config écrivent donc leur propre fixture dans tmp_path.
_UNIFIED_CONFIG = """\
cycle_interval_seconds: 300.0
search_poll_budget_seconds: 30.0
search_poll_interval_seconds: 5.0
keyword_pause_min_seconds: 1.0
keyword_pause_max_seconds: 4.0
decision_poll_interval_seconds: 5.0
shutdown_deadline_seconds: 10.0
backoff:
  base_seconds: 2.0
  cap_seconds: 300.0
  factor: 2.0
  jitter_ratio: 0.3
amules:
  - name: amule-1
    host: amuled
    port: 4712
    password: ${AMULE_EC_PASSWORD}
catalog_db_path: /data/catalog.db
local_db_path: /data/local.db
"""

_UNIFIED_CONFIG_WITH_OBS = (
    _UNIFIED_CONFIG
    + """\
observability:
  log_level: DEBUG
  notification_timeout_seconds: 5.0
"""
)


def _write_config(tmp_path: Path, *, body: str = _UNIFIED_CONFIG) -> Path:
    path = tmp_path / "crawler.yml"
    path.write_text(body, encoding="utf-8")
    return path


def _args(config: Path) -> argparse.Namespace:
    return argparse.Namespace(
        config=config,
        targets=_CONFIG / "targets.yaml",
        matcher=_CONFIG / "matcher.yaml",
    )


def _argv(config: Path) -> list[str]:
    return [
        "--config",
        str(config),
        "--targets",
        str(_CONFIG / "targets.yaml"),
        "--matcher",
        str(_CONFIG / "matcher.yaml"),
    ]


def test_build_app_assembles_a_crawler_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Config SANS section observability → couvre la branche `observability is None` de build_app.
    monkeypatch.setenv("AMULE_EC_PASSWORD", "s3cr3t")
    app = entry.build_app(_args(_write_config(tmp_path)))
    assert isinstance(app, CrawlerApp)


def test_build_app_applies_log_level_when_observability_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La branche observability is not None de build_app appelle setLevel."""
    monkeypatch.setenv("AMULE_EC_PASSWORD", "s3cr3t")
    config = _write_config(tmp_path, body=_UNIFIED_CONFIG_WITH_OBS)
    app = entry.build_app(_args(config))
    assert isinstance(app, CrawlerApp)
    assert logging.getLogger().level == logging.DEBUG  # niveau racine appliqué (DEBUG=10)


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
    # Le gate full-mode lève un ``ConfigError`` AU RUNTIME (health-check verifier KO) depuis
    # ``app.run()`` — pas depuis ``build_app``. ``main`` doit le rendre avec le MÊME message propre
    # + code de sortie 1 (et non un traceback nu).
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
    bad = tmp_path / "crawler.yml"
    bad.write_text("amules: []\ncatalog_db_path: c\nlocal_db_path: l\n", encoding="utf-8")
    code = entry.main(["--config", str(bad)])
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_main_refuses_on_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = entry.main(["--config", str(tmp_path / "absent.yml")])
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_default_args_point_at_config_dir() -> None:
    namespace = entry._parse_args([])
    assert namespace.config == Path("deploy/config/crawler/crawler.yml")
    assert namespace.targets == Path("deploy/config/crawler/targets.yml")
    assert namespace.matcher == Path("deploy/config/crawler/matcher.yml")


def test_package_main_shim_reexports_main() -> None:
    # `python -m emule_indexer` exécute le __main__ du PAQUET : il doit exposer la MÊME
    # fonction `main` que composition.__main__ (sinon la DoD §9.4 n'est pas tenue).
    from emule_indexer import __main__ as package_entry

    assert package_entry.main is entry.main


# ---------------------------------------------------------------- sous-commande validate-config


def test_bare_invocation_still_runs_the_crawler(monkeypatch: pytest.MonkeyPatch) -> None:
    # CONTRAINTE DURE de rétro-compat : SANS sous-commande, on retombe EXACTEMENT sur le
    # chemin run (build_app → asyncio.run). C'est ce que fait le compose.
    seen: dict[str, object] = {}

    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # ferme la coroutine sans la lancer

    def fake_build_app(args: argparse.Namespace) -> _SpyApp:
        seen["config"] = args.config  # prouve qu'on est passé par _parse_args
        return _SpyApp()

    monkeypatch.setattr("emule_indexer.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", fake_build_app)
    assert entry.main(_argv(Path("crawler.yml"))) == 0
    assert seen["config"] == Path("crawler.yml")


def test_validate_config_does_not_start_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # validate-config ne démarre RIEN : ni build_app, ni asyncio.run.
    monkeypatch.setenv("AMULE_EC_PASSWORD", "s3cr3t")

    def boom_run(coro: object) -> None:  # pragma: no cover - ne doit jamais être appelé
        raise AssertionError("asyncio.run ne doit pas être appelé par validate-config")

    def boom_build_app(args: argparse.Namespace) -> CrawlerApp:  # pragma: no cover
        raise AssertionError("build_app ne doit pas être appelé par validate-config")

    monkeypatch.setattr("emule_indexer.composition.__main__.asyncio.run", boom_run)
    monkeypatch.setattr(entry, "build_app", boom_build_app)
    assert entry.main(["validate-config", *_argv(_write_config(tmp_path))]) == 0


def test_validate_config_reports_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AMULE_EC_PASSWORD", "s3cr3t")
    code = entry.main(["validate-config", *_argv(_write_config(tmp_path))])
    assert code == 0
    assert "Config valide" in capsys.readouterr().out


def test_validate_config_defaults_point_at_config_dir() -> None:
    # Les options de validate-config ont les MÊMES défauts deploy/config/crawler/*.yml que le run.
    namespace = entry._parse_validate_args([])
    assert namespace.config == Path("deploy/config/crawler/crawler.yml")
    assert namespace.targets == Path("deploy/config/crawler/targets.yml")
    assert namespace.matcher == Path("deploy/config/crawler/matcher.yml")


def test_validate_config_reports_missing_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # EFFET DE BORD VOULU : la config référence ${AMULE_EC_PASSWORD} (section active : amules) ;
    # la variable absente de l'environnement → interpolation fail-fast → code 1, message clair.
    monkeypatch.delenv("AMULE_EC_PASSWORD", raising=False)
    code = entry.main(["validate-config", *_argv(_write_config(tmp_path))])
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_validate_config_rejects_broken_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "crawler.yml"
    broken.write_text("ports: [unclosed\n", encoding="utf-8")  # YAML syntaxiquement cassé
    code = entry.main(["validate-config", "--config", str(broken)])
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_validate_config_rejects_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "crawler.yml"
    bad.write_text("amules: []\ncatalog_db_path: c\nlocal_db_path: l\n", encoding="utf-8")
    code = entry.main(["validate-config", "--config", str(bad)])
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_validate_config_rejects_matcher_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AMULE_EC_PASSWORD", "s3cr3t")
    bad_matcher = tmp_path / "matcher.yaml"
    # rules non-liste → MatcherConfigError (parse structural)
    bad_matcher.write_text("tokens: {}\nrules: {}\n", encoding="utf-8")
    argv = [
        "validate-config",
        "--config",
        str(_write_config(tmp_path)),
        "--targets",
        str(_CONFIG / "targets.yaml"),
        "--matcher",
        str(bad_matcher),
    ]
    code = entry.main(argv)
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_validate_config_rejects_config_error_in_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AMULE_EC_PASSWORD", "s3cr3t")
    bad_targets = tmp_path / "targets.yaml"
    bad_targets.write_text("episodes: nope\n", encoding="utf-8")  # episodes non-liste → ConfigError
    argv = [
        "validate-config",
        "--config",
        str(_write_config(tmp_path)),
        "--targets",
        str(bad_targets),
        "--matcher",
        str(_CONFIG / "matcher.yaml"),
    ]
    code = entry.main(argv)
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err
