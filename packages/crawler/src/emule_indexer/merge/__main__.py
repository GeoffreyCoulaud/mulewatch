"""Point d'entrée ``python -m emule_indexer.merge`` : CLI safe-by-default du merge.

Aligné sur ``tools/ec_probe.py``/``composition/__main__.py`` (pas de ``[project.scripts]`` :
invocation ``python -m emule_indexer.merge``). ``main(argv) -> int`` rend un code de sortie :
``0`` = merge OK ; ``2`` = erreur d'usage/merge (message clair sur ``stderr``, jamais de
traceback nu) ; argparse rend lui-même ``2`` pour une erreur de parsing (groupe mutuellement
exclusif/requis). Aucune variable d'environnement (doctrine du repo, spec §6).

Safe-by-default (zéro perte accidentelle, spec §6) :
- ``--output`` neuf → créé + mergé ; ``--output`` existant → refus SAUF ``--force`` (append
  idempotent, jamais de truncate).
- ``--into <source>`` → dest = une des sources, qui DOIT être listée ; on merge les autres
  dedans. Mutuellement exclusif avec ``--output`` ; l'un des deux est requis.
- ``--force`` n'a de sens qu'avec ``--output`` (rejeté avec ``--into``).
- une source absente → ``MergeError`` AVANT de créer/ouvrir la sortie (fail-fast).
"""

import argparse
import logging
import sys
from pathlib import Path

from emule_indexer.merge.errors import MergeError
from emule_indexer.merge.merger import merge_catalogs

_LOGGER = logging.getLogger("emule_indexer.merge")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emule_indexer.merge",
        description="Fusionne N catalog.db en un seul (idempotent, append-only préservé).",
    )
    parser.add_argument(
        "sources", nargs="+", type=Path, help="1..N bases catalog.db à fusionner (≥ 1)."
    )
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Fichier de sortie NEUF (refus s'il existe, sauf --force).",
    )
    destination.add_argument(
        "--into", type=Path, help="Destination = une des sources listées (mergé dedans)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Autorise un --output existant (append idempotent, jamais de truncate).",
    )
    return parser.parse_args(argv)


def _resolve_destination(args: argparse.Namespace) -> tuple[Path, bool]:
    """Valide les règles safe-by-default et rend ``(output, dest_is_source)``.

    Lève ``MergeError`` (message clair) AVANT toute ouverture/création de la sortie :
    sources absentes, ``--force`` avec ``--into``, ``--output`` existant sans ``--force``,
    ``--into`` non listée dans les sources.
    """
    for source in args.sources:
        if not source.exists():
            raise MergeError(f"source introuvable : {source}")

    if args.into is not None:
        if args.force:
            raise MergeError("--force n'a pas de sens avec --into")
        resolved_sources = {source.resolve() for source in args.sources}
        if args.into.resolve() not in resolved_sources:
            raise MergeError(f"--into doit désigner une source listée : {args.into}")
        return args.into, True

    # Mode --output (le groupe required garantit qu'on a l'un OU l'autre ; ici c'est --output).
    if args.output.exists() and not args.force:
        raise MergeError(
            f"la sortie existe déjà : {args.output} (utilisez --force pour merger dedans)"
        )
    return args.output, False


def main(argv: list[str] | None = None) -> int:
    """Entrée CLI. ``0`` = OK, ``2`` = erreur d'usage/merge (message clair sur stderr)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        output, dest_is_source = _resolve_destination(args)
        _LOGGER.info("merge → %s (%d source(s))", output, len(args.sources))
        merge_catalogs(output, args.sources, dest_is_source=dest_is_source)
    except MergeError as error:
        print(f"Merge impossible : {error}", file=sys.stderr, flush=True)
        return 2
    _LOGGER.info("merge terminé : %s", output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
