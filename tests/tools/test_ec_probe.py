import pytest

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcError,
    EcFailureError,
    EcTimeoutError,
)
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
from emule_indexer.tools.ec_probe import (
    _default_client,
    build_parser,
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
    """Faux client conforme au port : journal d'appels + données en conserve."""

    def __init__(
        self,
        *,
        status: NetworkStatus,
        batches: list[tuple[FileObservation, ...]],
        progresses: list[int | None],
        connect_error: EcError | None = None,
        fetch_error: BaseException | None = None,
        stop_error: BaseException | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._status = status
        self._batches = batches
        self._progresses = progresses
        self._connect_error = connect_error
        self._fetch_error = fetch_error
        self._stop_error = stop_error

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
    assert "mot de passe requis" in capsys.readouterr().err
    assert fake.calls == []  # le client n'est jamais construit ni connecté


# ---------------------------------------------------------------- cycle complet via main()


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
    # Noms de fichiers = entrée hostile : affichés via repr() (une ligne non ambiguë).
    assert "[probe] 'Keroro 062A.avi'" in out
    assert "hash=000102030405060708090a0b0c0d0e0f" in out
    # Dump de TOUS les tags reçus, y compris inconnus (noms bruts/hex) — livrable 4.
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
    assert "serveur : —" in out  # branche « pas de serveur » du format
    assert "total : 0 résultat(s)" in out  # boucle d'observations à zéro itération


def test_main_returns_1_on_ec_error_and_still_closes(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeMuleClient(
        status=_STATUS_OFF,
        batches=[()],
        progresses=[None],
        connect_error=EcAuthError("Invalid password"),
    )
    code = main(["--password", "bad", "--keyword", "keroro"], client_factory=lambda args: fake)
    assert code == 1
    assert fake.calls == ["connect", "close"]  # close() TOUJOURS appelé (finally)
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
    assert "interrompu" in capsys.readouterr().err
    assert "close" in fake.calls  # close() TOUJOURS appelé (finally)


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
    assert sleeps == [5.0]  # 2 relevés (ceil(10/5)), 1 seul sleep (pas après le dernier)
    assert client.calls.count("fetch") == 2
    assert client.calls[-1] == "stop"
    assert "progression ?" in capsys.readouterr().out  # progress None affiché « ? »


@pytest.mark.asyncio
async def test_search_and_wait_breaks_early_when_progress_reaches_100() -> None:
    sleeps: list[float] = []

    async def _instant_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[100])
    await search_and_wait(
        client, "keroro", SearchChannel.GLOBAL, timeout=60.0, interval=5.0, sleep=_instant_sleep
    )
    assert sleeps == []  # arrêt anticipé : aucun sleep
    assert client.calls.count("fetch") == 1


@pytest.mark.asyncio
async def test_search_and_wait_stops_search_even_when_fetch_raises() -> None:
    async def _instant_sleep(delay: float) -> None:
        pass  # jamais atteint : l'erreur survient au premier relevé

    client = FakeMuleClient(
        status=_STATUS_OFF, batches=[()], progresses=[None], fetch_error=EcFailureError("boom")
    )
    with pytest.raises(EcFailureError):
        await search_and_wait(
            client, "keroro", SearchChannel.GLOBAL, timeout=10.0, interval=5.0, sleep=_instant_sleep
        )
    assert "stop" in client.calls  # stop_search() TOUJOURS appelé (finally)


@pytest.mark.asyncio
async def test_search_and_wait_propagates_original_error_when_stop_search_also_fails() -> None:
    # Diagnostic d'origine prime : si fetch_results échoue (ex. EcTimeoutError → transport
    # invalidé), le finally tente stop_search() qui lève EcConnectError — cette seconde
    # erreur NE DOIT PAS remplacer l'exception d'origine (le diagnostic montré à l'utilisateur
    # serait faux). Le stop_search() impossible n'apporte rien : contextlib.suppress(EcError).
    async def _instant_sleep(delay: float) -> None:
        pass  # jamais atteint

    client = FakeMuleClient(
        status=_STATUS_OFF,
        batches=[()],
        progresses=[None],
        fetch_error=EcTimeoutError("délai dépassé"),
        stop_error=EcConnectError("non connecté"),
    )
    with pytest.raises(EcTimeoutError):
        await search_and_wait(
            client, "keroro", SearchChannel.GLOBAL, timeout=10.0, interval=5.0, sleep=_instant_sleep
        )
    assert "stop" in client.calls  # stop_search() toujours tenté


# ---------------------------------------------------------------- fabrique réelle


def test_default_client_builds_an_amule_ec_client() -> None:
    args = build_parser().parse_args(
        ["--host", "homelab", "--port", "4713", "--password", "pwd", "--keyword", "k"]
    )
    client = _default_client(args)
    assert isinstance(client, AmuleEcClient)  # constructeur sans I/O : sûr en test
    # Câblage épinglé (regard privé acceptable en test) : host/port/password des args.
    assert client._host == "homelab"
    assert client._port == 4713
    assert client._password == "pwd"
