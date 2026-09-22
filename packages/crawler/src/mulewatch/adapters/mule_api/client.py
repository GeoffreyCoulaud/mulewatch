"""High-level amuleapi client: auth, search, status, preferences, download (spec §4).

STRUCTURALLY implements ``MuleClient`` AND ``MuleDownloadClient`` (without importing them, same
structural typing the EC adapter used). NO sleep, retry or reconnection here beyond the single
re-login a ``401`` mandates: the adapter signals, the caller decides.

The ports have no notion of a ``search_id``, so the client holds the id of the ONE search in
flight, reproducing exactly what EC allowed. Several concurrent searches are lot 2.
"""

import json
from contextlib import suppress
from typing import Any

import httpx

from mulewatch.adapters.mule_api.errors import (
    ApiAuthError,
    ApiRejectedError,
    ApiUnreachableError,
    error_from_response,
)
from mulewatch.adapters.mule_api.mapping import (
    map_download_entry,
    map_network_status,
    map_search_results,
    map_shared_entry,
)
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.mule_client import NetworkStatus, SearchChannel
from mulewatch.ports.mule_download_client import DownloadEntry, SharedFileEntry

# Rows asked for per list request. Every list route caps at 100 when `limit` is omitted, which
# would silently truncate the shared-file sweep completion detection depends on (§7.5).
_PAGE_SIZE = 500

_MAX_PROGRESS_PERCENT = 100


class AmuleApiClient:
    """Drives an ``amuled`` through ``amuleapi``'s REST surface.

    ``skipped_entries_total`` accumulates result entries discarded by the mapper. It is an EVENT
    counter: since the readout is cumulative, the same unusable entry re-seen on every readout
    counts each time - do not read it as "unique lost entries".
    """

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = f"http://{host}:{port}/api/v1"
        self._password = password
        self._timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._search_id: int | None = None
        self._current_keyword = ""
        self.skipped_entries_total = 0

    async def connect(self) -> None:
        """Opens the HTTP session and logs in. Failure → exception, NO retry.

        IDEMPOTENT: a second call on an already-connected client is a no-op. Essential to the
        pool: the composition root connects at wiring time, then the worker re-calls
        ``connect()`` in its ``_ensure_connected()``.
        """
        if self._http is not None:
            return
        if not self._password:
            raise ApiAuthError("empty amuleapi password (refused before asking the daemon)")
        http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
        )
        try:
            await self._login(http)
        except Exception:
            await http.aclose()
            raise
        self._http = http

    async def close(self) -> None:
        """Revokes the token, then closes the session.

        Deliberately bypasses the 401 rule of ``_request``: a rejected logout means the session
        is already gone, and logging back in to end it would burn a login for nothing.
        """
        http, self._http = self._http, None
        if http is None:
            return
        with suppress(ApiUnreachableError):
            await _send(http, "POST", "/auth/logout")
        await http.aclose()
        self._search_id = None

    async def start_search(self, keyword: str, channel: SearchChannel) -> None:
        """Starts a search and keeps its id. The previous search's results stay readable."""
        payload = await self._call(
            "POST", "/search", body={"query": keyword, "type": channel.value}
        )
        search_id = payload.get("search_id")
        if not isinstance(search_id, int) or isinstance(search_id, bool):
            raise ApiRejectedError("POST /search answered without a search_id")
        self._search_id = search_id
        self._current_keyword = keyword  # provenance, set AFTER success

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        """CUMULATIVE snapshot of what the daemon holds for the search in flight."""
        rows = await self._collect(f"/search/{self._require_search()}/results", "results")
        observations, skipped = map_search_results(rows, self._current_keyword)
        self.skipped_entries_total += skipped
        return observations

    async def stop_search(self) -> None:
        """Stops the search. Its results stay readable, unlike a DELETE."""
        await self._call("POST", f"/search/{self._require_search()}/stop")

    async def search_progress(self) -> int | None:
        """Percentage 0-100, or ``None`` when the daemon reports none (port contract).

        Asks for zero rows: the progress envelope travels with the results, and this is polled
        on a timer while the result set grows.
        """
        if self._search_id is None:
            return None
        payload = await self._call("GET", f"/search/{self._search_id}/results", params={"limit": 0})
        progress = payload.get("progress")
        percent = progress.get("percent") if isinstance(progress, dict) else None
        if not isinstance(percent, int) or isinstance(percent, bool):
            return None
        return min(percent, _MAX_PROGRESS_PERCENT)

    async def network_status(self) -> NetworkStatus:
        """Network status: the one GET that carries both networks and our own eD2k id."""
        return map_network_status(await self._call("GET", "/status"))

    async def get_listen_port(self) -> int:
        """amuled's current eD2k TCP listen port (port-sync High-ID, design §2.3/§4.2)."""
        payload = await self._call("GET", "/preferences")
        connection = payload.get("connection")
        port = connection.get("tcp_port") if isinstance(connection, dict) else None
        if not isinstance(port, int) or isinstance(port, bool):
            raise ApiUnreachableError("GET /preferences without connection.tcp_port")
        return port

    async def set_listen_port(self, port: int) -> None:
        """Updates the TCP AND UDP listen ports, which the EC adapter also moved together.

        The preference is written, not rebound: a restart is what makes amuled listen there.
        """
        await self._call(
            "PATCH", "/preferences", body={"connection": {"tcp_port": port, "udp_port": port}}
        )

    async def add_link(self, ed2k_link: str) -> None:
        """Adds an ed2k link to amuled's download queue.

        The route is a bulk one: a link the daemon refuses comes back inside a 2xx, per item,
        so the envelope is what reports the failure, not the status code.
        """
        payload = await self._call("POST", "/downloads", body={"links": [ed2k_link]})
        results = payload.get("results")
        outcome = results[0] if isinstance(results, list) and results else None
        if not isinstance(outcome, dict) or outcome.get("ok") is not True:
            raise ApiRejectedError(f"POST /downloads refused the link: {_outcome_reason(outcome)}")

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        """Snapshot of the download queue. NEVER reads the bytes.

        ``status=all`` on purpose: the default hides completed entries, and the disk-cap
        admission rule sums the whole queue's remaining bytes (§4.3).
        """
        rows = await self._collect("/downloads", "downloads", params={"status": "all"})
        return tuple(entry for row in rows if (entry := map_download_entry(row)) is not None)

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        """Snapshot of amuled's SHARED files: the completion signal. NEVER reads the bytes."""
        rows = await self._collect("/shared", "shared")
        return tuple(entry for row in rows if (entry := map_shared_entry(row)) is not None)

    # --- transport --------------------------------------------------------------------------

    def _require_search(self) -> int:
        """The id of the search in flight, or a clean refusal (the caller starts one first)."""
        if self._search_id is None:
            raise ApiRejectedError("no search in flight (call start_search first)")
        return self._search_id

    async def _call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One request on the connected session, decoded into a JSON object."""
        if self._http is None:
            raise ApiUnreachableError("amuleapi client not connected (call connect() first)")
        return await self._request(self._http, method, path, body=body, params=params)

    async def _request(
        self,
        http: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sends, renewing the token ONCE on a 401 (§7.3: retrying one only burns the limiter).

        A second 401 is terminal for the session and comes back as an unreachable daemon: only
        a successful login clears that state, and hammering it locks our IP out for 300 s.
        """
        response = await _send(http, method, path, body=body, params=params)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            await self._login(http)
            response = await _send(http, method, path, body=body, params=params)
        if not response.is_success:
            raise error_from_response(response)
        return _decode(response)

    async def _login(self, http: httpx.AsyncClient) -> None:
        """Mints a token and arms the Authorization header (the bearer wins over the cookie)."""
        response = await _send(
            http,
            "POST",
            "/auth/login",
            body={"password": self._password},
            params={"include_token": "true"},
        )
        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise ApiAuthError("amuleapi refused the admin password")
        if not response.is_success:
            raise error_from_response(response)
        token = _decode(response).get("token")
        if not isinstance(token, str) or not token:
            raise ApiAuthError("POST /auth/login answered without a token")
        http.headers["Authorization"] = f"Bearer {token}"

    async def _collect(
        self, path: str, envelope: str, *, params: dict[str, Any] | None = None
    ) -> list[Any]:
        """Sweeps a whole list route by KEYSET paging (§7.5).

        Never ``offset``: it is a position, so a row deleted below the cursor shifts the window
        and the row that slides past it is returned by nothing, ever. ``hash`` is the identity
        column of all three list routes we read, which is what ``after`` requires.
        """
        rows: list[Any] = []
        page_params = {**(params or {}), "sort": "hash", "limit": _PAGE_SIZE}
        while True:
            payload = await self._call("GET", path, params=page_params)
            page = payload.get(envelope)
            if not isinstance(page, list):
                return rows  # unexpected envelope: tolerated, sweep over
            rows.extend(page)
            if len(page) < _PAGE_SIZE:
                return rows
            anchor = page[-1].get("hash") if isinstance(page[-1], dict) else None
            if not isinstance(anchor, str):
                return rows  # no anchor to advance on: stop rather than re-read page one
            page_params = {**page_params, "after": anchor}


async def _send(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """One HTTP roundtrip; a transport failure is an unreachable daemon, never a crash."""
    try:
        return await http.request(method, path, json=body, params=params)
    except httpx.HTTPError as failure:
        raise ApiUnreachableError(f"{method} {path}: {failure}") from failure


def _decode(response: httpx.Response) -> dict[str, Any]:
    """A 2xx body → its JSON object. A 204 carries none; anything unreadable is a dead hop."""
    if not response.content:
        return {}
    try:
        payload = json.loads(response.content)
    except (json.JSONDecodeError, ValueError) as failure:
        raise ApiUnreachableError(f"{response.request.url.path}: unreadable body") from failure
    return payload if isinstance(payload, dict) else {}


def _outcome_reason(outcome: dict[str, Any] | None) -> str:
    """The per-item error message of a bulk envelope, or a safe label if it carries none."""
    error = outcome.get("error") if isinstance(outcome, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return message if isinstance(message, str) else "no per-item outcome reported"
