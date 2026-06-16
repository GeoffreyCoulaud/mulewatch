"""Sonde EC : recherche réelle contre un amuled + dump de TOUS les tags reçus (spec §8.4).

Usage :
    uv run python -m emule_indexer.tools.ec_probe --host 127.0.0.1 --port 4712 \\
        --password <pwd> --keyword keroro --channel global

C'est l'outil de MESURE de la richesse des champs (rapport livrable 5) : chaque entrée
``raw_meta`` (tags non mappés, connus ou inconnus) est affichée nom-hex + valeur. Avec
``--all-tags``, le dump bascule sur la liste COMPLÈTE des tags bruts de chaque résultat
(mappés inclus, via ``fetch_results_raw``) pour mesurer le taux de remplissage empirique
de CHAQUE tag — pas seulement les non mappés. La convenance ``search_and_wait`` (poll +
budget) vit ICI, pas dans le port : le polling appartient à l'appelant (spec §3) — ici
l'appelant, c'est nous. Réutilisable tel quel contre le homelab.
"""

import argparse
import asyncio
import contextlib
import math
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.codec import INT_WIDTHS, EcPacket, EcTag
from emule_indexer.adapters.mule_ec.errors import EcError
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import MuleClient, NetworkStatus, SearchChannel


class RawSearchClient(MuleClient, Protocol):
    """Le port + le BRUT : ``fetch_results_raw`` rend l'``EcPacket`` décodé AVANT mapping.

    La sonde est le seul appelant qui a besoin des tags bruts (mesure du taux de remplissage,
    C2) ; ce besoin reste DANS l'outil, il ne pollue pas le port métier ``MuleClient``.
    """

    async def fetch_results_raw(self) -> EcPacket: ...


Sleeper = Callable[[float], Awaitable[None]]
ClientFactory = Callable[[argparse.Namespace], RawSearchClient]


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
    parser.add_argument(
        "--all-tags",
        action="store_true",
        help="dumpe TOUS les tags bruts de chaque résultat (mappés inclus), pour mesurer "
        "le taux de remplissage empirique de chaque tag (au lieu du seul raw_meta non mappé)",
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


def _render_tag_value(tag: EcTag) -> str:
    """Rendu d'une valeur de tag qui ne lève JAMAIS : entier décimal, texte, sinon hex brut.

    Proche de ``mapping._render_value`` (le mapper ne l'exporte pas), mais les chaînes sont
    rendues via ``repr()`` : un enregistrement = une ligne non ambiguë (un nom de fichier ou
    une valeur hostile reste lisible, guillemets/échappements visibles) — aucune métadonnée perdue.
    """
    if tag.tag_type in INT_WIDTHS and len(tag.value) == INT_WIDTHS[tag.tag_type]:
        return str(int.from_bytes(tag.value, "big"))
    if tag.tag_type == codes.EC_TAGTYPE_STRING and tag.value.endswith(b"\x00"):
        return repr(tag.value[:-1].decode("utf-8", errors="replace"))
    return tag.value.hex()


def _dump_subtree(tag: EcTag, depth: int, lines: list[str]) -> None:
    """Un tag puis ses enfants récursivement : ``nom hex + type + valeur`` indenté par niveau."""
    indent = "    " * depth
    lines.append(f"{indent}0x{tag.name:04X} type=0x{tag.tag_type:02X} = {_render_tag_value(tag)}")
    for child in tag.children:
        _dump_subtree(child, depth + 1, lines)


def format_raw_tags(packet: EcPacket) -> str:
    """Dump COMPLET des tags d'un ``EC_OP_SEARCH_RESULTS`` : CHAQUE tag de CHAQUE entrée.

    Contrairement à ``raw_meta`` (qui exclut les tags mappés et écartés), ce dump expose
    TOUS les tags — c'est l'outil de mesure du taux de remplissage empirique (C2). Les tags
    de premier niveau autres que ``EC_TAG_SEARCHFILE`` sont ignorés (pas des entrées).
    """
    entries = [tag for tag in packet.tags if tag.name == codes.EC_TAG_SEARCHFILE]
    lines = [f"[probe] dump complet des tags bruts : {len(entries)} résultat(s)"]
    for index, entry in enumerate(entries, start=1):
        lines.append(f"[probe] résultat #{index}")
        _dump_subtree(entry, depth=1, lines=lines)
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


async def collect_raw_results(
    client: RawSearchClient,
    keyword: str,
    channel: SearchChannel,
    *,
    timeout: float,
    interval: float,
    sleep: Sleeper = asyncio.sleep,
) -> EcPacket:
    """Variante BRUTE de ``search_and_wait`` : relève l'``EcPacket`` non mappé (mode --all-tags).

    Mêmes invariants de polling (``ceil(timeout/interval)`` relevés, ``sleep`` entre deux,
    arrêt anticipé à 100 %, ``stop_search`` garanti) mais via ``fetch_results_raw`` : aucun
    mapping, on garde TOUS les tags pour la mesure. Le dernier paquet relevé est retourné.
    """
    await client.start_search(keyword, channel)
    rounds = max(1, math.ceil(timeout / interval))
    packet = EcPacket(codes.EC_OP_SEARCH_RESULTS)
    try:
        for round_index in range(rounds):
            packet = await client.fetch_results_raw()
            progress = await client.search_progress()
            shown = "?" if progress is None else f"{progress}%"
            entries = sum(1 for tag in packet.tags if tag.name == codes.EC_TAG_SEARCHFILE)
            print(
                f"[probe] relevé brut {round_index + 1}/{rounds} : "
                f"{entries} résultat(s), progression {shown}"
            )
            if progress == 100:
                break
            if round_index < rounds - 1:
                await sleep(interval)
    finally:
        with contextlib.suppress(EcError):
            await client.stop_search()
    return packet


async def run_probe(
    client: RawSearchClient, args: argparse.Namespace, *, sleep: Sleeper = asyncio.sleep
) -> int:
    try:
        await client.connect()
        print(format_status(await client.network_status()))
        keyword = str(args.keyword)
        channel = SearchChannel(str(args.channel))
        timeout = float(args.timeout)
        interval = float(args.interval)
        if args.all_tags:
            packet = await collect_raw_results(
                client, keyword, channel, timeout=timeout, interval=interval, sleep=sleep
            )
            print(format_raw_tags(packet))
        else:
            results = await search_and_wait(
                client, keyword, channel, timeout=timeout, interval=interval, sleep=sleep
            )
            print(f"[probe] total : {len(results)} résultat(s)")
            for observation in results:
                print(format_observation(observation))
    finally:
        await client.close()
    return 0


def _default_client(args: argparse.Namespace) -> RawSearchClient:
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
