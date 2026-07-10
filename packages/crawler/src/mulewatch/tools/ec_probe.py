"""EC probe: real search against an amuled + dump of ALL received tags (spec §8.4).

Usage:
    uv run python -m mulewatch.tools.ec_probe --host 127.0.0.1 --port 4712 \\
        --password <pwd> --keyword keroro --channel global

This is the field-richness MEASUREMENT tool (deliverable 5 report): each ``raw_meta``
entry (unmapped tags, known or unknown) is printed as hex-name + value. With
``--all-tags``, the dump switches to the COMPLETE list of raw tags of each result
(mapped included, via ``fetch_results_raw``) to measure the empirical fill rate
of EACH tag - not just the unmapped ones. The ``search_and_wait`` convenience (poll +
budget) lives HERE, not in the port: polling belongs to the caller (spec §3) - here
the caller is us. Reusable as-is against the homelab.
"""

import argparse
import asyncio
import contextlib
import math
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from mulewatch.adapters.mule_ec import codes
from mulewatch.adapters.mule_ec.client import AmuleEcClient
from mulewatch.adapters.mule_ec.codec import INT_WIDTHS, EcPacket, EcTag
from mulewatch.adapters.mule_ec.errors import EcError
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.mule_client import MuleClient, NetworkStatus, SearchChannel


class RawSearchClient(MuleClient, Protocol):
    """The port + the RAW: ``fetch_results_raw`` returns the ``EcPacket`` decoded BEFORE mapping.

    The probe is the only caller that needs the raw tags (fill-rate measurement,
    C2); this need stays IN the tool, it does not pollute the ``MuleClient`` domain port.
    """

    async def fetch_results_raw(self) -> EcPacket: ...


Sleeper = Callable[[float], Awaitable[None]]
ClientFactory = Callable[[argparse.Namespace], RawSearchClient]


def _positive_float(text: str) -> float:
    """``type=`` argparse: strictly positive float, otherwise a clean exit (code 2)."""
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be strictly positive: {text!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ec_probe", description="EC probe: real search + dump of received tags"
    )
    parser.add_argument("--host", default="127.0.0.1", help="amuled host")
    parser.add_argument("--port", type=int, default=4712, help="EC port (ECPort)")
    parser.add_argument(
        "--password",
        default=os.environ.get("EC_PROBE_PASSWORD"),
        help="EC password (plaintext; default: EC_PROBE_PASSWORD environment variable)",
    )
    parser.add_argument("--keyword", required=True, help="search keyword")
    parser.add_argument(
        "--channel",
        choices=[channel.value for channel in SearchChannel],
        default=SearchChannel.GLOBAL.value,
        help="search channel",
    )
    parser.add_argument(
        "--timeout", type=_positive_float, default=60.0, help="total polling budget (s)"
    )
    parser.add_argument(
        "--interval", type=_positive_float, default=5.0, help="interval between readouts (s)"
    )
    parser.add_argument(
        "--all-tags",
        action="store_true",
        help="dump ALL raw tags of each result (mapped included), to measure "
        "the empirical fill rate of each tag (instead of only the unmapped raw_meta)",
    )
    return parser


def format_status(status: NetworkStatus) -> str:
    server = "n/a"
    if status.server_name is not None or status.server_addr is not None:
        server = f"{status.server_name or '?'} ({status.server_addr or '?'})"
    return (
        "[probe] network status:\n"
        f"  eD2k: id={status.ed2k_id} high={status.ed2k_high}\n"
        f"  Kad:  {status.kad_status.value}\n"
        f"  server: {server}"
    )


def format_observation(observation: FileObservation) -> str:
    # Filenames and tag values = hostile input (newlines, control chars…):
    # repr() guarantees "one record = one unambiguous line".
    lines = [
        f"[probe] {observation.filename!r}",
        f"  hash={observation.ed2k_hash} size={observation.size_bytes} B",
        f"  sources={observation.source_count} complete={observation.complete_source_count}",
    ]
    for name, value in observation.raw_meta:
        lines.append(f"  raw {name} = {value!r}")
    return "\n".join(lines)


def _render_tag_value(tag: EcTag) -> str:
    """Render a tag value that NEVER raises: decimal integer, text, otherwise raw hex.

    Close to ``mapping._render_value`` (the mapper does not export it), but strings are
    rendered via ``repr()``: one record = one unambiguous line (a filename or
    a hostile value stays readable, quotes/escapes visible) - no metadata lost.
    """
    if tag.tag_type in INT_WIDTHS and len(tag.value) == INT_WIDTHS[tag.tag_type]:
        return str(int.from_bytes(tag.value, "big"))
    if tag.tag_type == codes.EC_TAGTYPE_STRING and tag.value.endswith(b"\x00"):
        return repr(tag.value[:-1].decode("utf-8", errors="replace"))
    return tag.value.hex()


def _dump_subtree(tag: EcTag, depth: int, lines: list[str]) -> None:
    """A tag then its children recursively: ``hex name + type + value`` indented per level."""
    indent = "    " * depth
    lines.append(f"{indent}0x{tag.name:04X} type=0x{tag.tag_type:02X} = {_render_tag_value(tag)}")
    for child in tag.children:
        _dump_subtree(child, depth + 1, lines)


def format_raw_tags(packet: EcPacket) -> str:
    """COMPLETE dump of the tags of an ``EC_OP_SEARCH_RESULTS``: EVERY tag of EVERY entry.

    Unlike ``raw_meta`` (which excludes mapped and discarded tags), this dump exposes
    ALL tags - it's the empirical fill-rate measurement tool (C2). Top-level tags
    other than ``EC_TAG_SEARCHFILE`` are ignored (not entries).
    """
    entries = [tag for tag in packet.tags if tag.name == codes.EC_TAG_SEARCHFILE]
    lines = [f"[probe] full raw-tag dump: {len(entries)} result(s)"]
    for index, entry in enumerate(entries, start=1):
        lines.append(f"[probe] result #{index}")
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
    """Launch a search then poll at a fixed interval up to the ``timeout`` budget.

    Clock-independent (deterministic in tests): ``ceil(timeout / interval)`` readouts,
    ``sleep(interval)`` between two, early stop if progress reaches 100%.
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
                f"[probe] readout {round_index + 1}/{rounds}: "
                f"{len(results)} result(s), progress {shown}"
            )
            if progress == 100:
                break
            if round_index < rounds - 1:
                await sleep(interval)
    finally:
        # The original diagnostic wins: if fetch_results/search_progress failed
        # (e.g. EcTimeoutError → transport invalidated), stop_search() would raise EcConnectError
        # that would replace the original exception. An impossible stop_search() adds nothing.
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
    """RAW variant of ``search_and_wait``: reads the unmapped ``EcPacket`` (--all-tags mode).

    Same polling invariants (``ceil(timeout/interval)`` readouts, ``sleep`` between two,
    early stop at 100%, guaranteed ``stop_search``) but via ``fetch_results_raw``: no
    mapping, we keep ALL tags for the measurement. The last read packet is returned.
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
                f"[probe] raw readout {round_index + 1}/{rounds}: "
                f"{entries} result(s), progress {shown}"
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
            print(f"[probe] total: {len(results)} result(s)")
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
