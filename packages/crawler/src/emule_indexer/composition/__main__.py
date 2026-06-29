"""Point d'entrée ``python -m emule_indexer`` : charge la config, monte l'app, tourne (§4).

Mode OBSERVATEUR (spec §2) : observe, catalogue, décide, boucle — rien d'autre. Le mode DOWNLOAD
s'active via ``download.enabled: true`` dans la config (le parseur unifié câble alors les boucles
download + vérification). Charge la config crawler unifiée (``crawler.yml``) + ``targets`` + la
config matcher (fail-fast au moindre souci → refus de démarrer, spec §5/§14), assemble les adapters
réels (horloge/RNG/nudge), puis ``asyncio.run(app.run())``. L'arrêt propre & borné est porté par
``CrawlerApp`` (spec §6).

Les chemins de config sont passés en arguments (``--config``/``--targets``/``--matcher``) avec des
défauts ``deploy/config/crawler/*.yml``. Les valeurs sensibles (secrets, URLs) de la config sont
interpolées depuis l'environnement par ``${NAME}`` (``os.environ``, adapter de config).
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from catalog_matching.validation import (
    ConfigError as MatcherConfigError,
)
from catalog_matching.validation import (
    parse_matcher_config,
    parse_targets,
)
from emule_indexer.adapters.clock_asyncio import AsyncioClock, SeededRng
from emule_indexer.adapters.config.crawler_config import ConfigError, parse_crawler_config
from emule_indexer.adapters.config.yaml_loader import YamlLoadError, load_yaml
from emule_indexer.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from emule_indexer.composition.app import CrawlerApp


def _add_config_options(parser: argparse.ArgumentParser) -> None:
    """Les chemins de config (mêmes options, mêmes défauts) pour le run ET validate-config."""
    parser.add_argument("--config", type=Path, default=Path("deploy/config/crawler/crawler.yml"))
    parser.add_argument("--targets", type=Path, default=Path("deploy/config/crawler/targets.yml"))
    parser.add_argument("--matcher", type=Path, default=Path("deploy/config/crawler/matcher.yml"))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emule_indexer", description="Crawler eMule (observateur)"
    )
    _add_config_options(parser)
    return parser.parse_args(argv)


def _parse_validate_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emule_indexer validate-config",
        description="Charge + valide la config (matcher/targets/crawler), sans rien démarrer",
    )
    _add_config_options(parser)
    return parser.parse_args(argv)


def build_app(args: argparse.Namespace) -> CrawlerApp:
    """Charge + valide toute la config (fail-fast §5/§14) et assemble la ``CrawlerApp``.

    La config crawler unifiée est interpolée depuis ``os.environ`` (``${NAME}`` → secrets/URLs).
    Toute erreur (``YamlLoadError``/``ConfigError``/``MatcherConfigError``) remonte telle quelle :
    ``main`` l'attrape, logge clair, et refuse de démarrer (spec §14).
    """
    crawler_config = parse_crawler_config(load_yaml(args.config), os.environ)
    if crawler_config.observability is not None:
        logging.getLogger().setLevel(crawler_config.observability.log_level)
    targets = parse_targets(load_yaml(args.targets))
    matcher_config = parse_matcher_config(load_yaml(args.matcher))
    return CrawlerApp(
        crawler_config=crawler_config,
        targets=targets,
        matcher_config=matcher_config,
        clock=AsyncioClock(),
        rng=SeededRng(),
        signal_hub=AsyncioDecisionSignal(),
    )


def validate_config(argv: list[str]) -> int:
    """Charge + valide la config via les parseurs EXISTANTS — un check PUR, ne démarre RIEN.

    Réutilise strictement ``load_yaml`` + ``parse_{crawler_config,targets,matcher_config}`` (aucune
    logique de validation nouvelle). Comme la config est interpolée depuis ``os.environ``, l'effet
    de bord VOULU est qu'on valide AUSSI la présence des variables d'env référencées par les
    sections ACTIVES. Toute erreur (``YamlLoadError``/``ConfigError``/``MatcherConfigError``) →
    message clair sur stderr + code 1, comme le refus de démarrer du run.
    """
    args = _parse_validate_args(argv)
    try:
        parse_crawler_config(load_yaml(args.config), os.environ)
        parse_targets(load_yaml(args.targets))
        parse_matcher_config(load_yaml(args.matcher))
    except (YamlLoadError, ConfigError, MatcherConfigError) as error:
        print(f"Config invalide : {error}", file=sys.stderr, flush=True)
        return 1
    print("Config valide", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entrée CLI. Rend un code de sortie (0 = arrêt propre, 1 = config invalide).

    Le ``try`` couvre AUSSI ``asyncio.run(app.run())`` : le gate full-mode (``download.enabled``)
    lève un ``ConfigError`` AU RUNTIME — health-check du verifier KO — qui est un refus de démarrer
    au même titre qu'une config invalide build-time. On le rend donc avec le MÊME message propre +
    code de sortie non-zéro (au lieu d'un traceback nu). Les ressources sont déjà fermées proprement
    par le ``run`` (stack LIFO) avant la levée.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    tokens = sys.argv[1:] if argv is None else argv
    # Dispatch en tête : la sous-commande ``validate-config`` route vers le check pur. L'invocation
    # NUE (sans sous-commande) retombe EXACTEMENT sur le chemin run — contrainte de rétro-compat
    # (compose lance ``python -m emule_indexer --config … --targets …`` sans sous-commande).
    if tokens and tokens[0] == "validate-config":
        return validate_config(list(tokens[1:]))
    args = _parse_args(tokens)
    try:
        app = build_app(args)
        asyncio.run(app.run())
    except (YamlLoadError, ConfigError, MatcherConfigError) as error:
        print(f"Config invalide, refus de démarrer : {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
