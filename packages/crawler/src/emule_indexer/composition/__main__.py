"""Point d'entrée ``python -m emule_indexer`` : charge la config, monte l'app, tourne (§4).

Mode OBSERVATEUR (spec §2) : observe, catalogue, décide, boucle — rien d'autre (pas de
download/notify : plans D/E). Charge ``crawler.yaml`` + ``local.yaml`` + ``targets.yaml`` +
la config matcher (fail-fast au moindre souci → refus de démarrer, spec §5/§14), assemble
les adapters réels (horloge/RNG/nudge), puis ``asyncio.run(app.run())``. L'arrêt propre &
borné est porté par ``CrawlerApp`` (spec §6).

Les chemins de config sont passés en arguments (``--crawler``/``--local``/``--targets``/
``--matcher``) avec des défauts ``config/*.yaml`` ; aucune variable d'environnement (spec §3).
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from emule_indexer.adapters.clock_asyncio import AsyncioClock, SeededRng
from emule_indexer.adapters.config.crawler_config import ConfigError, parse_crawler_config
from emule_indexer.adapters.config.local_config import parse_local_config
from emule_indexer.adapters.config.yaml_loader import YamlLoadError, load_yaml
from emule_indexer.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from emule_indexer.composition.app import CrawlerApp
from emule_indexer.domain.matching.validation import (
    ConfigError as MatcherConfigError,
)
from emule_indexer.domain.matching.validation import (
    parse_matcher_config,
    parse_targets,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emule_indexer", description="Crawler eMule (observateur)"
    )
    parser.add_argument("--crawler", type=Path, default=Path("config/crawler.yaml"))
    parser.add_argument("--local", type=Path, default=Path("config/local.yaml"))
    parser.add_argument("--targets", type=Path, default=Path("config/targets.yaml"))
    parser.add_argument("--matcher", type=Path, default=Path("config/matcher.yaml"))
    return parser.parse_args(argv)


def build_app(args: argparse.Namespace) -> CrawlerApp:
    """Charge + valide toute la config (fail-fast §5/§14) et assemble la ``CrawlerApp``.

    Toute erreur de config (``YamlLoadError``/``ConfigError``/``MatcherConfigError``) remonte
    telle quelle : ``main`` l'attrape, logge clair, et refuse de démarrer (spec §14).
    """
    crawler_config = parse_crawler_config(load_yaml(args.crawler))
    if crawler_config.observability is not None:
        logging.getLogger().setLevel(crawler_config.observability.log_level)
    local_config = parse_local_config(load_yaml(args.local))
    targets = parse_targets(load_yaml(args.targets))
    matcher_config = parse_matcher_config(load_yaml(args.matcher))
    return CrawlerApp(
        crawler_config=crawler_config,
        local_config=local_config,
        targets=targets,
        matcher_config=matcher_config,
        clock=AsyncioClock(),
        rng=SeededRng(),
        signal_hub=AsyncioDecisionSignal(),
    )


def main(argv: list[str] | None = None) -> int:
    """Entrée CLI. Rend un code de sortie (0 = arrêt propre, 1 = config invalide).

    Le ``try`` couvre AUSSI ``asyncio.run(app.run())`` : le gate full-mode (mode ``verifier_url``)
    lève un ``ConfigError`` AU RUNTIME — health-check du verifier KO ou ensemble download
    incomplet — qui est un refus de démarrer au même titre qu'une config invalide build-time.
    On le rend donc avec le MÊME message propre + code de sortie non-zéro (au lieu d'un traceback
    nu). Les ressources sont déjà fermées proprement par le ``run`` (stack LIFO) avant la levée.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        app = build_app(args)
        asyncio.run(app.run())
    except (YamlLoadError, ConfigError, MatcherConfigError) as error:
        print(f"Config invalide, refus de démarrer : {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
