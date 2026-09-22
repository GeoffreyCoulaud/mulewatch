"""``AmuleApiClient``: auth, search, status, preferences (spec amuleapi §4)."""

import json

import httpx
import pytest

from mulewatch.adapters.mule_api.client import AmuleApiClient
from mulewatch.adapters.mule_api.errors import (
    ApiAuthError,
    ApiRejectedError,
    ApiUnreachableError,
)
from mulewatch.ports.mule_client import KadStatus, SearchChannel
from tests.adapters.mule_api.api_fakes import PASSWORD, TOKEN, FakeAmuleApi, error

_HASH = "8b54a3c20fae9e4b9f7e0c2c8c01b6b1"


def _client(api: FakeAmuleApi, *, password: str = PASSWORD) -> AmuleApiClient:
    return AmuleApiClient("amuled.test", 4711, password, transport=api.transport())


async def _connected(api: FakeAmuleApi) -> AmuleApiClient:
    client = _client(api)
    await client.connect()
    return client


def _paths(api: FakeAmuleApi, path: str) -> list[httpx.URL]:
    """Every request made to one route, in order (close() adds a logout after the assertions)."""
    return [request.url for request in api.requests if request.url.path == path]


def _result(name: str = "Keroro.095.avi") -> dict[str, object]:
    return {"hash": _HASH, "name": name, "size_bytes": 10, "sources": {"total": 1}}


@pytest.mark.asyncio
async def test_connect_logs_in_and_arms_the_bearer() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    try:
        await client.network_status()
    finally:
        await client.close()

    login = api.requests[0]
    assert login.url.path == "/api/v1/auth/login"
    # The token has to be asked for: the default reply carries only the cookie (spec §6).
    assert login.url.params.get("include_token") == "true"
    assert api.requests[1].headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.asyncio
async def test_connect_is_idempotent() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    await client.connect()
    await client.close()

    assert api.logins == 1


@pytest.mark.asyncio
async def test_connect_refuses_an_empty_password_without_asking() -> None:
    api = FakeAmuleApi()
    client = _client(api, password="")

    with pytest.raises(ApiAuthError):
        await client.connect()
    assert api.requests == []


@pytest.mark.asyncio
async def test_a_wrong_password_is_a_config_error_not_a_loop_case() -> None:
    api = FakeAmuleApi()
    client = _client(api, password="wrong")

    with pytest.raises(ApiAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_a_login_reply_without_a_token_is_a_config_error() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/auth/login")] = lambda _: httpx.Response(
        200, json={"role": "admin"}
    )
    client = _client(api)

    with pytest.raises(ApiAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_login_disabled_is_a_daemon_that_is_not_ready() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/auth/login")] = lambda _: error(
        503, "login_disabled", "no password configured"
    )
    client = _client(api)

    with pytest.raises(ApiUnreachableError):
        await client.connect()


@pytest.mark.asyncio
async def test_a_dead_transport_is_an_unreachable_daemon() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/auth/login")] = refuse
    client = _client(api)

    with pytest.raises(ApiUnreachableError):
        await client.connect()


@pytest.mark.asyncio
async def test_an_operation_before_connect_is_unreachable() -> None:
    client = _client(FakeAmuleApi())

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_close_logs_out_once_and_tolerates_a_failure() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("POST", "/api/v1/auth/logout")] = lambda _: error(401, "unauthorized")

    await client.close()
    await client.close()  # a second close is a no-op, not a second request

    assert [request.url.path for request in api.requests].count("/api/v1/auth/logout") == 1


@pytest.mark.asyncio
async def test_connect_after_close_logs_in_again() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    await client.close()
    await client.connect()
    await client.close()

    assert api.logins == 2


@pytest.mark.asyncio
async def test_a_stale_token_is_renewed_once() -> None:
    """§7.3: a 401 is terminal for the session, so we re-login once and never loop."""
    api = FakeAmuleApi()
    client = await _connected(api)
    stale = {"count": 0}

    def once(request: httpx.Request) -> httpx.Response:
        stale["count"] += 1
        if stale["count"] == 1:
            return error(401, "unauthorized", "credentials changed")
        return httpx.Response(200, json=api.status)

    api.overrides[("GET", "/api/v1/status")] = once
    await client.network_status()
    await client.close()

    assert api.logins == 2
    assert stale["count"] == 2


@pytest.mark.asyncio
async def test_a_second_401_ends_the_session_instead_of_looping() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: error(401, "unauthorized")

    with pytest.raises(ApiUnreachableError):
        await client.network_status()
    # One re-login, one retry, then it stops: the limiter locks an IP out after 30 rejects.
    assert api.logins == 2
    assert [request.url.path for request in api.requests].count("/api/v1/status") == 2


@pytest.mark.asyncio
async def test_a_rate_limited_answer_carries_its_retry_delay() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: error(
        429, "rate_limited", "locked out", **{"Retry-After": "300"}
    )

    with pytest.raises(ApiUnreachableError, match="300"):
        await client.network_status()


@pytest.mark.asyncio
async def test_a_broken_ec_link_is_an_unreachable_daemon() -> None:
    """503 ec_unavailable: amuleapi is up, its link to amuled is not (a NEW state)."""
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: error(503, "ec_unavailable", "cold")

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_is_an_unreachable_daemon() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: httpx.Response(200, content=b"<html>")

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_an_error_body_that_is_not_an_envelope_still_maps() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: httpx.Response(500, content=b"oops")

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_an_error_envelope_with_junk_fields_still_maps() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: httpx.Response(
        500, json={"error": {"code": 7, "message": None}}
    )

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_an_error_body_whose_error_key_is_not_an_object_still_maps() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: httpx.Response(500, json={"error": 7})

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


# --- search --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_search_sends_the_query_and_the_channel() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)

    await client.start_search("keroro", SearchChannel.KAD)
    await client.close()

    start = api.requests[1]
    assert start.url.path == "/api/v1/search"
    assert json.loads(start.content) == {"query": "keroro", "type": "kad"}


@pytest.mark.asyncio
async def test_start_search_stops_the_previous_one_first() -> None:
    """Observed on the image: Kad refuses a keyword still on its search list (§7.2 neighbour).

    Only `POST /search/{id}/stop` takes the keyword off Kademlia's list; freeing the search with
    a DELETE does not. Stopping first is also what EC did, where starting wiped the previous.
    """
    api = FakeAmuleApi()
    client = await _connected(api)

    await client.start_search("keroro", SearchChannel.KAD)
    await client.start_search("keroro", SearchChannel.KAD)
    await client.close()

    paths = [request.url.path for request in api.requests]
    assert paths.index("/api/v1/search/42/stop") < paths.index("/api/v1/search", 2)


@pytest.mark.asyncio
async def test_a_previous_search_already_gone_does_not_block_the_next() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    await client.start_search("keroro", SearchChannel.KAD)
    api.overrides[("POST", "/api/v1/search/42/stop")] = lambda _: error(404, "not_found")

    await client.start_search("titar", SearchChannel.KAD)
    await client.close()

    assert api.requests[-2].url.path == "/api/v1/search"


@pytest.mark.asyncio
async def test_a_search_the_daemon_refuses_is_a_channel_failure() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("POST", "/api/v1/search")] = lambda _: error(
        400, "amuled_rejected", "not connected to any server"
    )

    with pytest.raises(ApiRejectedError):
        await client.start_search("keroro", SearchChannel.GLOBAL)


@pytest.mark.asyncio
async def test_a_search_reply_without_an_id_is_a_channel_failure() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("POST", "/api/v1/search")] = lambda _: httpx.Response(202, json={"query": "k"})

    with pytest.raises(ApiRejectedError):
        await client.start_search("keroro", SearchChannel.GLOBAL)


@pytest.mark.asyncio
async def test_fetch_results_maps_the_snapshot_and_counts_what_it_drops() -> None:
    api = FakeAmuleApi(results=[_result(), {"hash": "nope"}])
    client = await _connected(api)

    await client.start_search("keroro", SearchChannel.GLOBAL)
    observations = await client.fetch_results()
    await client.close()

    assert [observation.filename for observation in observations] == ["Keroro.095.avi"]
    assert [observation.keyword for observation in observations] == ["keroro"]
    assert client.skipped_entries_total == 1


@pytest.mark.asyncio
async def test_fetch_results_pages_past_the_first_window() -> None:
    rows = [_result(f"Keroro.{index:04d}.avi") | {"hash": f"{index:032x}"} for index in range(501)]
    api = FakeAmuleApi(results=rows)
    client = await _connected(api)

    await client.start_search("keroro", SearchChannel.GLOBAL)
    observations = await client.fetch_results()
    await client.close()

    assert len(observations) == 501


@pytest.mark.asyncio
async def test_fetch_results_without_a_search_is_a_channel_failure() -> None:
    client = await _connected(FakeAmuleApi())

    with pytest.raises(ApiRejectedError):
        await client.fetch_results()


@pytest.mark.asyncio
async def test_search_progress_reads_the_progress_envelope() -> None:
    api = FakeAmuleApi(progress={"state": "running", "percent": 67})
    client = await _connected(api)

    await client.start_search("keroro", SearchChannel.GLOBAL)
    assert await client.search_progress() == 67
    await client.close()


@pytest.mark.asyncio
async def test_search_progress_asks_for_no_result_row() -> None:
    api = FakeAmuleApi(results=[_result()])
    client = await _connected(api)

    await client.start_search("keroro", SearchChannel.GLOBAL)
    await client.search_progress()
    await client.close()

    assert _paths(api, "/api/v1/search/42/results")[-1].params.get("limit") == "0"


@pytest.mark.asyncio
async def test_search_progress_is_unknown_without_a_search() -> None:
    client = await _connected(FakeAmuleApi())

    assert await client.search_progress() is None


@pytest.mark.asyncio
async def test_a_progress_the_daemon_does_not_report_is_unknown() -> None:
    api = FakeAmuleApi(progress={"state": "running"})
    client = await _connected(api)

    await client.start_search("keroro", SearchChannel.GLOBAL)
    assert await client.search_progress() is None
    await client.close()


@pytest.mark.asyncio
async def test_stop_search_stops_the_search_it_started() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)

    await client.start_search("keroro", SearchChannel.GLOBAL)
    await client.stop_search()
    await client.close()

    assert _paths(api, "/api/v1/search/42/stop") != []


@pytest.mark.asyncio
async def test_stop_search_without_a_search_is_a_channel_failure() -> None:
    client = await _connected(FakeAmuleApi())

    with pytest.raises(ApiRejectedError):
        await client.stop_search()


# --- status and preferences ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_status_maps_the_daemon_state() -> None:
    api = FakeAmuleApi(
        status={
            "ed2k": {"state": "connected", "high_id": True, "user_id": 42},
            "kad": {"state": "connected", "firewalled_tcp": False},
        }
    )
    client = await _connected(api)

    status = await client.network_status()
    await client.close()

    assert status.ed2k_id == 42
    assert status.ed2k_high is True
    assert status.kad_status is KadStatus.CONNECTED


@pytest.mark.asyncio
async def test_get_listen_port_reads_the_connection_preferences() -> None:
    client = await _connected(FakeAmuleApi())

    assert await client.get_listen_port() == 4662
    await client.close()


@pytest.mark.asyncio
async def test_preferences_without_a_tcp_port_are_unusable() -> None:
    api = FakeAmuleApi(preferences={"connection": {}})
    client = await _connected(api)

    with pytest.raises(ApiUnreachableError):
        await client.get_listen_port()


@pytest.mark.asyncio
async def test_set_listen_port_moves_tcp_and_udp_together() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)

    await client.set_listen_port(51820)
    await client.close()

    assert api.patched == [{"connection": {"tcp_port": 51820, "udp_port": 51820}}]


# --- downloads -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_link_queues_the_link() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)

    await client.add_link("ed2k://|file|a|10|abc|/")
    await client.close()

    assert api.added_links == ["ed2k://|file|a|10|abc|/"]


@pytest.mark.asyncio
async def test_a_link_the_daemon_refuses_is_reported() -> None:
    """The bulk envelope answers 2xx even when the one item inside it failed."""
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/downloads")] = lambda _: httpx.Response(
        207,
        json={
            "results": [
                {
                    "id": "ed2k://x",
                    "ok": False,
                    "error": {"code": "amuled_rejected", "message": "malformed"},
                }
            ]
        },
    )
    client = await _connected(api)

    with pytest.raises(ApiRejectedError, match="malformed"):
        await client.add_link("ed2k://x")


@pytest.mark.asyncio
async def test_a_bulk_envelope_without_an_outcome_is_reported() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/downloads")] = lambda _: httpx.Response(202, json={})
    client = await _connected(api)

    with pytest.raises(ApiRejectedError):
        await client.add_link("ed2k://x")


@pytest.mark.asyncio
async def test_a_failed_bulk_entry_without_a_message_is_still_reported() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/downloads")] = lambda _: httpx.Response(
        207, json={"results": [{"id": "ed2k://x", "ok": False}]}
    )
    client = await _connected(api)

    with pytest.raises(ApiRejectedError):
        await client.add_link("ed2k://x")


@pytest.mark.asyncio
async def test_download_queue_asks_for_the_completed_entries_too() -> None:
    """§4.3: the default `status=active` hides completions, and the disk cap sums the queue."""
    api = FakeAmuleApi(downloads=[{"hash": _HASH, "size_bytes": 10, "completed_bytes": 4}])
    client = await _connected(api)

    queue = await client.download_queue()
    await client.close()

    assert _paths(api, "/api/v1/downloads")[0].params.get("status") == "all"
    assert [entry.remaining_bytes for entry in queue] == [6]


@pytest.mark.asyncio
async def test_download_queue_drops_an_entry_with_no_usable_hash() -> None:
    api = FakeAmuleApi(downloads=[{"size_bytes": 10}, {"hash": _HASH}])
    client = await _connected(api)

    queue = await client.download_queue()
    await client.close()

    assert [entry.ed2k_hash for entry in queue] == [_HASH]


@pytest.mark.asyncio
async def test_shared_files_pages_past_the_first_hundred() -> None:
    """§7.5: the newest shared file is the one we wait for, and it is the one that falls out."""
    rows = [{"hash": f"{index:032x}", "name": f"{index}.avi"} for index in range(501)]
    api = FakeAmuleApi(shared=rows)
    client = await _connected(api)

    shared = await client.shared_files()
    await client.close()

    assert len(shared) == 501
    assert shared[-1].ed2k_hash == f"{500:032x}"
    # Keyset paging, never offset: a row removed mid-sweep must not shift the window (§7.5).
    sweep = _paths(api, "/api/v1/shared")
    assert all(url.params.get("offset") is None for url in sweep)
    assert sweep[-1].params.get("after") == f"{499:032x}"


@pytest.mark.asyncio
async def test_a_page_whose_last_row_has_no_anchor_stops_the_sweep() -> None:
    rows: list[dict[str, object]] = [{"hash": f"{index:032x}"} for index in range(500)]
    rows[-1] = {"name": "anchorless"}
    api = FakeAmuleApi(shared=rows)
    client = await _connected(api)

    shared = await client.shared_files()
    await client.close()

    assert len(shared) == 499


@pytest.mark.asyncio
async def test_a_list_envelope_that_is_not_a_list_stops_the_sweep() -> None:
    api = FakeAmuleApi()
    api.overrides[("GET", "/api/v1/shared")] = lambda _: httpx.Response(200, json={"shared": 7})
    client = await _connected(api)

    assert await client.shared_files() == ()
    await client.close()
