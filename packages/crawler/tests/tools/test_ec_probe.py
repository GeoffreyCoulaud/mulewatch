import pytest

from mulewatch.adapters.mule_ec import codes
from mulewatch.adapters.mule_ec.client import AmuleEcClient
from mulewatch.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    hash16_tag,
    string_tag,
    uint_tag,
)
from mulewatch.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcError,
    EcFailureError,
    EcTimeoutError,
)
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
from mulewatch.tools.ec_probe import (
    _default_client,
    build_parser,
    collect_raw_results,
    format_raw_tags,
    main,
    search_and_wait,
)

_STATUS_FULL = NetworkStatus(
    ed2k_id=33554433,
    ed2k_high=True,
    kad_status=KadStatus.CONNECTED,
    server_name="TestServer",
    server_addr="1.2.3.4:4661",
)
_STATUS_OFF = NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)

_OBSERVATION = FileObservation(
    ed2k_hash="000102030405060708090a0b0c0d0e0f",
    filename="Keroro 062A.avi",
    size_bytes=234567890,
    source_count=5,
    complete_source_count=2,
    keyword="keroro",
    raw_meta=(("0x0308", "0"), ("0x0999", "mystère")),
)


class FakeMuleClient:
    """Fake port-compliant client: call log + canned data."""

    def __init__(
        self,
        *,
        status: NetworkStatus,
        batches: list[tuple[FileObservation, ...]],
        progresses: list[int | None],
        connect_error: EcError | None = None,
        fetch_error: BaseException | None = None,
        stop_error: BaseException | None = None,
        raw_packet: EcPacket | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._status = status
        self._batches = batches
        self._progresses = progresses
        self._connect_error = connect_error
        self._fetch_error = fetch_error
        self._stop_error = stop_error
        self._raw_packet = raw_packet

    async def connect(self) -> None:
        self.calls.append("connect")
        if self._connect_error is not None:
            raise self._connect_error

    async def close(self) -> None:
        self.calls.append("close")

    async def start_search(self, keyword: str, channel: SearchChannel) -> None:
        self.calls.append(f"start:{keyword}:{channel.value}")

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        self.calls.append("fetch")
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._batches.pop(0) if len(self._batches) > 1 else self._batches[0]

    async def fetch_results_raw(self) -> EcPacket:
        self.calls.append("fetch_raw")
        if self._fetch_error is not None:
            raise self._fetch_error
        assert self._raw_packet is not None  # provided when the --all-tags mode is tested
        return self._raw_packet

    async def stop_search(self) -> None:
        self.calls.append("stop")
        if self._stop_error is not None:
            raise self._stop_error

    async def search_progress(self) -> int | None:
        self.calls.append("progress")
        return self._progresses.pop(0) if len(self._progresses) > 1 else self._progresses[0]

    async def network_status(self) -> NetworkStatus:
        self.calls.append("status")
        return self._status


# ---------------------------------------------------------------- parsing


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--password", "pwd", "--keyword", "keroro"])
    assert args.host == "127.0.0.1"
    assert args.port == 4712
    assert args.channel == "global"
    assert args.timeout == 60.0
    assert args.interval == 5.0


def test_parser_rejects_unknown_channel() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--password", "p", "--keyword", "k", "--channel", "web"])
    assert excinfo.value.code == 2


def test_parser_requires_password_and_keyword() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args([])
    assert excinfo.value.code == 2


def test_parser_accepts_explicit_positive_timeout_and_interval() -> None:
    args = build_parser().parse_args(
        ["--password", "p", "--keyword", "k", "--timeout", "30", "--interval", "2"]
    )
    assert args.timeout == 30.0
    assert args.interval == 2.0


def test_parser_rejects_zero_interval() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--password", "p", "--keyword", "k", "--interval", "0"])
    assert excinfo.value.code == 2


def test_parser_rejects_negative_timeout() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--password", "p", "--keyword", "k", "--timeout", "-3"])
    assert excinfo.value.code == 2


def test_parser_password_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EC_PROBE_PASSWORD", "env-secret")
    args = build_parser().parse_args(["--keyword", "k"])
    assert args.password == "env-secret"


def test_main_errors_when_password_absent_everywhere(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("EC_PROBE_PASSWORD", raising=False)
    fake = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[None])
    with pytest.raises(SystemExit) as excinfo:
        main(["--keyword", "keroro"], client_factory=lambda args: fake)
    assert excinfo.value.code == 2
    assert "password required" in capsys.readouterr().err
    assert fake.calls == []  # the client is never built nor connected


# ---------------------------------------------------------------- format_raw_tags (dump ALL)


def _searchfile(*children: EcTag) -> EcTag:
    return EcTag(codes.EC_TAG_SEARCHFILE, codes.EC_TAGTYPE_UINT8, b"\x00", children)


def test_parser_has_all_tags_flag_off_by_default() -> None:
    args = build_parser().parse_args(["--password", "p", "--keyword", "k"])
    assert args.all_tags is False


def test_parser_accepts_all_tags_flag() -> None:
    args = build_parser().parse_args(["--password", "p", "--keyword", "k", "--all-tags"])
    assert args.all_tags is True


def test_format_raw_tags_dumps_every_tag_including_mapped_and_unknown() -> None:
    # The mapper EXCLUDES the mapped tags from raw_meta; format_raw_tags shows them ALL — that
    # is the point of C2 (measuring the empirical fill rate of EACH tag).
    entry = _searchfile(
        string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro.avi"),  # MAPPED → absent from raw_meta
        hash16_tag(codes.EC_TAG_PARTFILE_HASH, bytes(range(16))),  # MAPPED
        uint_tag(0x0999, 42),  # unknown
    )
    out = format_raw_tags(EcPacket(codes.EC_OP_SEARCH_RESULTS, (entry,)))
    assert "result #1" in out
    assert "0x0301" in out  # EC_TAG_PARTFILE_NAME (mapped): present in the full dump
    assert "Keroro.avi" in out
    assert "0x031E" in out  # EC_TAG_PARTFILE_HASH (mapped)
    assert "0x0999 type=0x02 = 42" in out  # unknown, type + integer value


def test_format_raw_tags_recurses_into_subtags() -> None:
    grandchild = string_tag(0x0AAA, "deep")
    child = uint_tag(0x0BBB, 7, (grandchild,))
    out = format_raw_tags(EcPacket(codes.EC_OP_SEARCH_RESULTS, (_searchfile(child),)))
    assert "0x0BBB" in out
    assert "0x0AAA" in out  # grandchild reached (recursion)
    assert "deep" in out


def test_format_raw_tags_skips_non_searchfile_top_level() -> None:
    stray = uint_tag(0x0001, 1)  # top-level tag that is NOT an entry
    out = format_raw_tags(EcPacket(codes.EC_OP_SEARCH_RESULTS, (stray,)))
    assert "0 result(s)" in out  # no entry dumped


def test_format_raw_tags_renders_string_hash_and_opaque_values() -> None:
    entry = _searchfile(
        hash16_tag(0x031E, bytes(range(16))),  # HASH16 → hex
        EcTag(0x0444, codes.EC_TAGTYPE_CUSTOM, b"\x01\x02\xff"),  # opaque → raw hex
    )
    out = format_raw_tags(EcPacket(codes.EC_OP_SEARCH_RESULTS, (entry,)))
    assert "000102030405060708090a0b0c0d0e0f" in out  # hash in hex
    assert "0102ff" in out  # opaque bytes in hex


# ---------------------------------------------------------------- full cycle via main()


def test_main_all_tags_dumps_raw_tag_stream(capsys: pytest.CaptureFixture[str]) -> None:
    raw = EcPacket(
        codes.EC_OP_SEARCH_RESULTS,
        (_searchfile(string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro.avi"), uint_tag(0x0999, 42)),),
    )
    fake = FakeMuleClient(
        status=_STATUS_FULL, batches=[(_OBSERVATION,)], progresses=[100], raw_packet=raw
    )
    code = main(
        ["--password", "pwd", "--keyword", "keroro", "--all-tags"],
        client_factory=lambda args: fake,
    )
    assert code == 0
    assert "fetch_raw" in fake.calls  # the raw mode goes through fetch_results_raw
    assert "fetch" not in fake.calls  # ... and NOT through the mapped path
    out = capsys.readouterr().out
    assert "0x0999 type=0x02 = 42" in out  # unknown tag dumped


def test_main_success_dumps_status_results_and_raw_meta(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeMuleClient(status=_STATUS_FULL, batches=[(_OBSERVATION,)], progresses=[100])
    code = main(
        ["--password", "pwd", "--keyword", "keroro"],
        client_factory=lambda args: fake,
    )
    assert code == 0
    assert fake.calls == [
        "connect",
        "status",
        "start:keroro:global",
        "fetch",
        "progress",
        "stop",
        "close",
    ]
    out = capsys.readouterr().out
    assert "TestServer (1.2.3.4:4661)" in out
    # Filenames = hostile input: displayed via repr() (an unambiguous line).
    assert "[probe] 'Keroro 062A.avi'" in out
    assert "hash=000102030405060708090a0b0c0d0e0f" in out
    # Dump of ALL received tags, including unknown ones (raw names/hex) — deliverable 4.
    assert "raw 0x0308 = '0'" in out
    assert "raw 0x0999 = 'mystère'" in out


def test_main_kad_channel_and_status_without_server(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[100])
    code = main(
        ["--password", "pwd", "--keyword", "keroro", "--channel", "kad"],
        client_factory=lambda args: fake,
    )
    assert code == 0
    assert "start:keroro:kad" in fake.calls
    out = capsys.readouterr().out
    assert "server: —" in out  # format's "no server" branch
    assert "total: 0 result(s)" in out  # zero-iteration observations loop


def test_main_returns_1_on_ec_error_and_still_closes(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeMuleClient(
        status=_STATUS_OFF,
        batches=[()],
        progresses=[None],
        connect_error=EcAuthError("Invalid password"),
    )
    code = main(["--password", "bad", "--keyword", "keroro"], client_factory=lambda args: fake)
    assert code == 1
    assert fake.calls == ["connect", "close"]  # close() ALWAYS called (finally)
    assert "Invalid password" in capsys.readouterr().err


def test_main_returns_130_on_keyboard_interrupt_and_still_closes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeMuleClient(
        status=_STATUS_OFF,
        batches=[()],
        progresses=[None],
        fetch_error=KeyboardInterrupt(),
    )
    code = main(["--password", "pwd", "--keyword", "keroro"], client_factory=lambda args: fake)
    assert code == 130
    assert "interrupted" in capsys.readouterr().err
    assert "close" in fake.calls  # close() ALWAYS called (finally)


# ---------------------------------------------------------------- search_and_wait


@pytest.mark.asyncio
async def test_search_and_wait_polls_until_budget_exhausted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sleeps: list[float] = []

    async def _instant_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[None])
    results = await search_and_wait(
        client, "keroro", SearchChannel.GLOBAL, timeout=10.0, interval=5.0, sleep=_instant_sleep
    )
    assert results == ()
    assert sleeps == [5.0]  # 2 polls (ceil(10/5)), only 1 sleep (not after the last)
    assert client.calls.count("fetch") == 2
    assert client.calls[-1] == "stop"
    assert "progress ?" in capsys.readouterr().out  # progress None shown as "?"


@pytest.mark.asyncio
async def test_search_and_wait_breaks_early_when_progress_reaches_100() -> None:
    sleeps: list[float] = []

    async def _instant_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[100])
    await search_and_wait(
        client, "keroro", SearchChannel.GLOBAL, timeout=60.0, interval=5.0, sleep=_instant_sleep
    )
    assert sleeps == []  # early stop: no sleep
    assert client.calls.count("fetch") == 1


@pytest.mark.asyncio
async def test_search_and_wait_stops_search_even_when_fetch_raises() -> None:
    async def _instant_sleep(delay: float) -> None:
        pass  # never reached: the error occurs on the first poll

    client = FakeMuleClient(
        status=_STATUS_OFF, batches=[()], progresses=[None], fetch_error=EcFailureError("boom")
    )
    with pytest.raises(EcFailureError):
        await search_and_wait(
            client, "keroro", SearchChannel.GLOBAL, timeout=10.0, interval=5.0, sleep=_instant_sleep
        )
    assert "stop" in client.calls  # stop_search() ALWAYS called (finally)


@pytest.mark.asyncio
async def test_search_and_wait_propagates_original_error_when_stop_search_also_fails() -> None:
    # Original diagnostic wins: if fetch_results fails (e.g. EcTimeoutError → transport
    # invalidated), the finally attempts stop_search() which raises EcConnectError — this second
    # error MUST NOT replace the original exception (the diagnostic shown to the user
    # would be wrong). The impossible stop_search() adds nothing: contextlib.suppress(EcError).
    async def _instant_sleep(delay: float) -> None:
        pass  # never reached

    client = FakeMuleClient(
        status=_STATUS_OFF,
        batches=[()],
        progresses=[None],
        fetch_error=EcTimeoutError("timed out"),
        stop_error=EcConnectError("not connected"),
    )
    with pytest.raises(EcTimeoutError):
        await search_and_wait(
            client, "keroro", SearchChannel.GLOBAL, timeout=10.0, interval=5.0, sleep=_instant_sleep
        )
    assert "stop" in client.calls  # stop_search() always attempted


# ---------------------------------------------------------------- collect_raw_results


@pytest.mark.asyncio
async def test_collect_raw_polls_until_budget_exhausted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sleeps: list[float] = []

    async def _instant_sleep(delay: float) -> None:
        sleeps.append(delay)

    packet = EcPacket(codes.EC_OP_SEARCH_RESULTS, (_searchfile(uint_tag(0x0999, 1)),))
    client = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[None], raw_packet=packet)
    result = await collect_raw_results(
        client, "keroro", SearchChannel.GLOBAL, timeout=10.0, interval=5.0, sleep=_instant_sleep
    )
    assert result is packet
    assert sleeps == [5.0]  # 2 polls (ceil(10/5)), only 1 sleep (not after the last)
    assert client.calls.count("fetch_raw") == 2
    assert "fetch" not in client.calls  # RAW path: never the mapper
    assert client.calls[-1] == "stop"
    assert "raw readout 1/2: 1 result(s), progress ?" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_collect_raw_breaks_early_when_progress_reaches_100() -> None:
    sleeps: list[float] = []

    async def _instant_sleep(delay: float) -> None:
        sleeps.append(delay)

    packet = EcPacket(codes.EC_OP_SEARCH_RESULTS)
    client = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[100], raw_packet=packet)
    await collect_raw_results(
        client, "keroro", SearchChannel.GLOBAL, timeout=60.0, interval=5.0, sleep=_instant_sleep
    )
    assert sleeps == []  # early stop: no sleep
    assert client.calls.count("fetch_raw") == 1


# ---------------------------------------------------------------- real factory


def test_default_client_builds_an_amule_ec_client() -> None:
    args = build_parser().parse_args(
        ["--host", "homelab", "--port", "4713", "--password", "pwd", "--keyword", "k"]
    )
    client = _default_client(args)
    assert isinstance(client, AmuleEcClient)  # I/O-free constructor: safe in test
    # Pinned wiring (private peek acceptable in test): host/port/password from the args.
    assert client._host == "homelab"
    assert client._port == 4713
    assert client._password == "pwd"
