import pytest

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcAuthError, EcError
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus
from emule_indexer.ports.mule_download_client import DownloadEntry, SharedFileEntry
from emule_indexer.tools.download_probe import (
    _default_client,
    build_parser,
    format_entry,
    format_status,
    main,
)

_HASH = "000102030405060708090a0b0c0d0e0f"
_STATUS = NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


class FakeDownloadClient:
    """Fake MuleDownloadClient: call log + canned queue."""

    def __init__(
        self,
        *,
        queue: tuple[DownloadEntry, ...] = (),
        connect_error: EcError | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.added: list[str] = []
        self._queue = queue
        self._connect_error = connect_error

    async def connect(self) -> None:
        self.calls.append("connect")
        if self._connect_error is not None:
            raise self._connect_error

    async def close(self) -> None:
        self.calls.append("close")

    async def add_link(self, ed2k_link: str) -> None:
        self.calls.append("add_link")
        self.added.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        self.calls.append("download_queue")
        return self._queue

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        return ()

    async def network_status(self) -> NetworkStatus:
        self.calls.append("status")
        return _STATUS


# ---------------------------------------------------------------- parsing


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--password", "pwd", "--link", "ed2k://x"])
    assert args.host == "127.0.0.1"
    assert args.port == 4712
    assert args.link == "ed2k://x"


def test_parser_requires_link() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--password", "pwd"])
    assert excinfo.value.code == 2


def test_parser_password_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EC_PROBE_PASSWORD", "env-secret")
    args = build_parser().parse_args(["--link", "ed2k://x"])
    assert args.password == "env-secret"


# ---------------------------------------------------------------- formatting


def test_format_entry_shows_progress_and_completeness() -> None:
    line = format_entry(DownloadEntry(ed2k_hash=_HASH, size_done=5, size_full=10))
    assert _HASH in line
    assert "5/10" in line
    assert "complete=False" in line


def test_format_status_renders_a_line() -> None:
    assert "network status" in format_status(_STATUS)


# ---------------------------------------------------------------- full cycle via main()


def test_main_success_adds_link_and_dumps_queue(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeDownloadClient(queue=(DownloadEntry(ed2k_hash=_HASH, size_done=10, size_full=10),))
    code = main(
        ["--password", "pwd", "--link", "ed2k://|file|x|1|" + _HASH + "|/"],
        client_factory=lambda args: fake,
    )
    assert code == 0
    assert fake.calls == ["connect", "status", "add_link", "download_queue", "close"]
    assert fake.added == ["ed2k://|file|x|1|" + _HASH + "|/"]
    out = capsys.readouterr().out
    assert "add_link accepted" in out
    assert "download queue: 1 entry(ies)" in out
    assert "complete=True" in out


def test_main_errors_when_password_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("EC_PROBE_PASSWORD", raising=False)
    fake = FakeDownloadClient()
    with pytest.raises(SystemExit) as excinfo:
        main(["--link", "ed2k://x"], client_factory=lambda args: fake)
    assert excinfo.value.code == 2
    assert "password required" in capsys.readouterr().err
    assert fake.calls == []  # the client is never built nor connected


def test_main_returns_1_on_ec_error_and_still_closes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeDownloadClient(connect_error=EcAuthError("Invalid password"))
    code = main(["--password", "bad", "--link", "ed2k://x"], client_factory=lambda args: fake)
    assert code == 1
    assert fake.calls == ["connect", "close"]  # close() ALWAYS called (finally)
    assert "Invalid password" in capsys.readouterr().err


def test_main_returns_130_on_keyboard_interrupt(capsys: pytest.CaptureFixture[str]) -> None:
    class _Interrupting(FakeDownloadClient):
        async def add_link(self, ed2k_link: str) -> None:
            raise KeyboardInterrupt

    fake = _Interrupting()
    code = main(["--password", "pwd", "--link", "ed2k://x"], client_factory=lambda args: fake)
    assert code == 130
    assert "interrupted" in capsys.readouterr().err
    assert "close" in fake.calls  # close() ALWAYS called (finally)


# ---------------------------------------------------------------- real factory


def test_default_client_builds_an_amule_ec_client() -> None:
    args = build_parser().parse_args(
        ["--host", "homelab", "--port", "4713", "--password", "pwd", "--link", "ed2k://x"]
    )
    client = _default_client(args)
    assert isinstance(client, AmuleEcClient)  # I/O-free constructor: safe in test
    assert client._host == "homelab"
    assert client._port == 4713
    assert client._password == "pwd"
