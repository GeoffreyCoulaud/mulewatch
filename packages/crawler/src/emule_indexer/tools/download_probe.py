"""EC download probe: add_link + dump of the download queue (download spec §4/§11).

Usage:
    uv run python -m emule_indexer.tools.download_probe --host 127.0.0.1 --port 4712 \\
        --password <pwd> --link 'ed2k://|file|name|123|<hash32>|/'

Mirror of ``tools/ec_probe.py`` for DOWNLOAD: adds an ed2k link to amuled, then reads
the download queue and prints each entry (hash, done/full, complete?). Validates that
``add_link`` is accepted and that the link appears in ``download_queue`` (real EC
mechanics — option A). Completion is NOT reachable without eD2k sources (ephemeral
container). Reusable as-is against a homelab to observe a real completion.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Sequence

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcError
from emule_indexer.ports.mule_client import NetworkStatus
from emule_indexer.ports.mule_download_client import DownloadEntry, MuleDownloadClient

ClientFactory = Callable[[argparse.Namespace], MuleDownloadClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="download_probe", description="EC probe: add_link + dump of the download queue"
    )
    parser.add_argument("--host", default="127.0.0.1", help="amuled host")
    parser.add_argument("--port", type=int, default=4712, help="EC port (ECPort)")
    parser.add_argument(
        "--password",
        default=os.environ.get("EC_PROBE_PASSWORD"),
        help="EC password (plaintext; default: EC_PROBE_PASSWORD environment variable)",
    )
    parser.add_argument("--link", required=True, help="ed2k link to add (ed2k://|file|…|/)")
    return parser


def format_entry(entry: DownloadEntry) -> str:
    return (
        f"[probe] {entry.ed2k_hash}: {entry.size_done}/{entry.size_full} B "
        f"(complete={entry.is_complete})"
    )


async def run_probe(client: MuleDownloadClient, args: argparse.Namespace) -> int:
    try:
        await client.connect()
        print(format_status(await client.network_status()))
        await client.add_link(str(args.link))
        print(f"[probe] add_link accepted for: {args.link}")
        queue = await client.download_queue()
        print(f"[probe] download queue: {len(queue)} entry(ies)")
        for entry in queue:
            print(format_entry(entry))
    finally:
        await client.close()
    return 0


def format_status(status: NetworkStatus) -> str:
    return f"[probe] network status: {status}"


def _default_client(args: argparse.Namespace) -> MuleDownloadClient:
    return AmuleEcClient(str(args.host), int(args.port), str(args.password))


def main(
    argv: Sequence[str] | None = None, *, client_factory: ClientFactory = _default_client
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.password is None:
        parser.error("password required (--password or EC_PROBE_PASSWORD)")
    try:
        return asyncio.run(run_probe(client_factory(args), args))
    except KeyboardInterrupt:
        print("[probe] interrupted", file=sys.stderr)
        return 130
    except EcError as exc:
        print(f"[probe] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
