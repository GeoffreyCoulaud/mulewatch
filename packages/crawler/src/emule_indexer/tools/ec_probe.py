"""Sonde EC : recherche réelle contre un amuled + dump de TOUS les tags reçus (spec §8.4).

Usage :
    uv run python -m emule_indexer.tools.ec_probe --host 127.0.0.1 --port 4712 \\
        --password <pwd> --keyword keroro --channel global

C'est l'outil de MESURE de la richesse des champs (rapport livrable 5) : chaque entrée
``raw_meta`` (tags non mappés, connus ou inconnus) est affichée nom-hex + valeur. La
convenance ``search_and_wait`` (poll + budget) vit ICI, pas dans le port : le polling
appartient à l'appelant (spec §3) — ici l'appelant, c'est nous. Réutilisable tel quel
contre le homelab.
"""

import argparse
import asyncio
import contextlib
import math
import os
import sys
from collections.abc import Awaitable, Callable, Sequence

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcError
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import MuleClient, NetworkStatus, SearchChannel

Sleeper = Callable[[float], Awaitable[None]]
ClientFactory = Callable[[argparse.Namespace], MuleClient]


def _positive_float(text: str) -> float:
    """``type=`` argparse : flottant strictement positif, sinon sortie propre (code 2)."""
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"doit être strictement positif : {text!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ec_probe", description="Sonde EC : recherche réelle + dump des tags reçus"
    )
    parser.add_argument("--host", default="127.0.0.1", help="hôte amuled")
    parser.add_argument("--port", type=int, default=4712, help="port EC (ECPort)")
    parser.add_argument(
        "--password",
        default=os.environ.get("EC_PROBE_PASSWORD"),
        help="mot de passe EC (en clair ; défaut : variable d'environnement EC_PROBE_PASSWORD)",
    )
    parser.add_argument("--keyword", required=True, help="mot-clé de recherche")
    parser.add_argument(
        "--channel",
        choices=[channel.value for channel in SearchChannel],
        default=SearchChannel.GLOBAL.value,
        help="canal de recherche",
    )
    parser.add_argument(
        "--timeout", type=_positive_float, default=60.0, help="budget total de polling (s)"
    )
    parser.add_argument(
        "--interval", type=_positive_float, default=5.0, help="intervalle entre relevés (s)"
    )
    return parser


def format_status(status: NetworkStatus) -> str:
    server = "—"
    if status.server_name is not None or status.server_addr is not None:
        server = f"{status.server_name or '?'} ({status.server_addr or '?'})"
    return (
        "[probe] statut réseau :\n"
        f"  eD2k : id={status.ed2k_id} high={status.ed2k_high}\n"
        f"  Kad  : {status.kad_status.value}\n"
        f"  serveur : {server}"
    )


def format_observation(observation: FileObservation) -> str:
    # Noms de fichiers et valeurs de tags = entrée hostile (retours à la ligne, contrôle…) :
    # repr() garantit « un enregistrement = une ligne non ambiguë ».
    lines = [
        f"[probe] {observation.filename!r}",
        f"  hash={observation.ed2k_hash} taille={observation.size_bytes} o",
        f"  sources={observation.source_count} complètes={observation.complete_source_count}",
    ]
    for name, value in observation.raw_meta:
        lines.append(f"  raw {name} = {value!r}")
    return "\n".join(lines)


async def search_and_wait(
    client: MuleClient,
    keyword: str,
    channel: SearchChannel,
    *,
    timeout: float,
    interval: float,
    sleep: Sleeper = asyncio.sleep,
) -> tuple[FileObservation, ...]:
    """Lance une recherche puis relève à intervalle fixe jusqu'au budget ``timeout``.

    Horloge-indépendant (déterministe en test) : ``ceil(timeout / interval)`` relevés,
    ``sleep(interval)`` entre deux, arrêt anticipé si la progression atteint 100 %.
    """
    await client.start_search(keyword, channel)
    rounds = max(1, math.ceil(timeout / interval))
    results: tuple[FileObservation, ...] = ()
    try:
        for round_index in range(rounds):
            results = await client.fetch_results()
            progress = await client.search_progress()
            shown = "?" if progress is None else f"{progress}%"
            print(
                f"[probe] relevé {round_index + 1}/{rounds} : "
                f"{len(results)} résultat(s), progression {shown}"
            )
            if progress == 100:
                break
            if round_index < rounds - 1:
                await sleep(interval)
    finally:
        # Le diagnostic d'origine prime : si fetch_results/search_progress a échoué
        # (ex. EcTimeoutError → transport invalidé), stop_search() lèverait EcConnectError
        # qui remplacerait l'exception d'origine. Un stop_search() impossible n'apporte rien.
        with contextlib.suppress(EcError):
            await client.stop_search()
    return results


async def run_probe(
    client: MuleClient, args: argparse.Namespace, *, sleep: Sleeper = asyncio.sleep
) -> int:
    try:
        await client.connect()
        print(format_status(await client.network_status()))
        results = await search_and_wait(
            client,
            str(args.keyword),
            SearchChannel(str(args.channel)),
            timeout=float(args.timeout),
            interval=float(args.interval),
            sleep=sleep,
        )
        print(f"[probe] total : {len(results)} résultat(s)")
        for observation in results:
            print(format_observation(observation))
    finally:
        await client.close()
    return 0


def _default_client(args: argparse.Namespace) -> MuleClient:
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
