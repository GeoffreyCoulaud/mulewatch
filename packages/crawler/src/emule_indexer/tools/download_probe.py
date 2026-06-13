"""Sonde EC download : add_link + dump de la file de download (spec download §4/§11).

Usage :
    uv run python -m emule_indexer.tools.download_probe --host 127.0.0.1 --port 4712 \\
        --password <pwd> --link 'ed2k://|file|nom|123|<hash32>|/'

Miroir de ``tools/ec_probe.py`` pour le DOWNLOAD : ajoute un lien ed2k à amuled, puis relève
la file de download et affiche chaque entrée (hash, done/full, complète ?). Valide que
``add_link`` est accepté et que le lien apparaît dans ``download_queue`` (mécaniques EC
réelles — option A). La complétion n'est PAS atteignable sans sources eD2k (conteneur
éphémère). Réutilisable tel quel contre un homelab pour observer une vraie complétion.
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
        prog="download_probe", description="Sonde EC : add_link + dump de la file de download"
    )
    parser.add_argument("--host", default="127.0.0.1", help="hôte amuled")
    parser.add_argument("--port", type=int, default=4712, help="port EC (ECPort)")
    parser.add_argument(
        "--password",
        default=os.environ.get("EC_PROBE_PASSWORD"),
        help="mot de passe EC (en clair ; défaut : variable d'environnement EC_PROBE_PASSWORD)",
    )
    parser.add_argument("--link", required=True, help="lien ed2k à ajouter (ed2k://|file|…|/)")
    return parser


def format_entry(entry: DownloadEntry) -> str:
    return (
        f"[probe] {entry.ed2k_hash} : {entry.size_done}/{entry.size_full} o "
        f"(complet={entry.is_complete})"
    )


async def run_probe(client: MuleDownloadClient, args: argparse.Namespace) -> int:
    try:
        await client.connect()
        print(format_status(await client.network_status()))
        await client.add_link(str(args.link))
        print(f"[probe] add_link accepté pour : {args.link}")
        queue = await client.download_queue()
        print(f"[probe] file de download : {len(queue)} entrée(s)")
        for entry in queue:
            print(format_entry(entry))
    finally:
        await client.close()
    return 0


def format_status(status: NetworkStatus) -> str:
    return f"[probe] statut réseau : {status}"


def _default_client(args: argparse.Namespace) -> MuleDownloadClient:
    return AmuleEcClient(str(args.host), int(args.port), str(args.password))


def main(
    argv: Sequence[str] | None = None, *, client_factory: ClientFactory = _default_client
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.password is None:
        parser.error("mot de passe requis (--password ou EC_PROBE_PASSWORD)")
    try:
        return asyncio.run(run_probe(client_factory(args), args))
    except KeyboardInterrupt:
        print("[probe] interrompu", file=sys.stderr)
        return 130
    except EcError as exc:
        print(f"[probe] ERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
