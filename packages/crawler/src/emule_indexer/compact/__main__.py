"""Entry point `python -m emule_indexer.compact`: safe-by-default compaction CLI.

`main(argv) -> int`: 0 = OK; 2 = usage/compaction error (clear message on stderr, never
a bare traceback); argparse itself returns 2 for a parsing error. No environment
variable (repo doctrine). Safe-by-default (spec §6): the output must NOT exist
(no --force, no append); missing source → error; keep-recent-days >= 0.
"""

import argparse
import logging
import sys
from pathlib import Path

from emule_indexer.compact.compactor import compact_catalog
from emule_indexer.compact.errors import CompactError

_LOGGER = logging.getLogger("emule_indexer.compact")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emule_indexer.compact",
        description=("Compact a catalog.db (daily rollup of observations) into a fresh output."),
    )
    parser.add_argument("source", type=Path, help="catalog.db to compact.")
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        type=Path,
        help="FRESH output file (refused if it exists; delete it to redo).",
    )
    parser.add_argument(
        "--keep-recent-days",
        type=int,
        default=90,
        help="Recent days kept raw (default 90; 0 = compact the whole history).",
    )
    return parser.parse_args(argv)


def _validate(args: argparse.Namespace) -> None:
    """Safe-by-default rules, BEFORE any opening/creation (CompactError, clear message)."""
    if not args.source.exists():
        raise CompactError(f"source not found: {args.source}")
    if args.output.exists():
        raise CompactError(f"output already exists: {args.output} (delete it to redo)")
    if args.keep_recent_days < 0:
        raise CompactError("--keep-recent-days must be >= 0")


def main(argv: list[str] | None = None) -> int:
    """CLI entry. 0 = OK, 2 = usage/compaction error (clear message on stderr)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _validate(args)
        _LOGGER.info(
            "compact %s → %s (keep_recent_days=%d)", args.source, args.output, args.keep_recent_days
        )
        compact_catalog(args.source, args.output, keep_recent_days=args.keep_recent_days)
    except CompactError as error:
        print(f"Compaction failed: {error}", file=sys.stderr, flush=True)
        return 2
    _LOGGER.info("compaction done: %s", args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
