"""A fake amuleapi, served to the client through ``httpx.MockTransport``.

Close enough to the real surface that the tests exercise the real httpx stack: the bearer is
checked, the list routes page the way the documented ones do (``limit`` defaults to 100, so a
client that forgets it silently gets the first hundred rows, which is §7.5's whole point), and
every error comes back in the ``{"error": {code, message}}`` envelope.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx

PASSWORD = "s3cret"
TOKEN = "eyJhbGciOi.fake.token"

# The real server's default page size. Unasked-for, it is what a forgetful client receives.
DEFAULT_LIMIT = 100


def error(status: int, code: str, message: str = "nope", **headers: str) -> httpx.Response:
    """A response in the surface's error envelope."""
    return httpx.Response(
        status, json={"error": {"code": code, "message": message}}, headers=headers
    )


class FakeAmuleApi:
    """Routes the handful of endpoints the adapter uses; everything else is a 404."""

    def __init__(
        self,
        *,
        password: str = PASSWORD,
        results: list[dict[str, Any]] | None = None,
        downloads: list[dict[str, Any]] | None = None,
        shared: list[dict[str, Any]] | None = None,
        status: dict[str, Any] | None = None,
        preferences: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
    ) -> None:
        self.password = password
        self.results = results if results is not None else []
        self.downloads = downloads if downloads is not None else []
        self.shared = shared if shared is not None else []
        self.status = status if status is not None else {"ed2k": {}, "kad": {}}
        self.preferences = (
            preferences if preferences is not None else {"connection": {"tcp_port": 4662}}
        )
        self.progress = progress if progress is not None else {"state": "finished", "percent": 100}
        self.requests: list[httpx.Request] = []
        self.logins = 0
        self.logouts = 0
        self.added_links: list[str] = []
        self.patched: list[dict[str, Any]] = []
        # Per-route overrides: a callable wins over the default behaviour, once per call.
        self.overrides: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = (request.method, request.url.path)
        override = self.overrides.get(route)
        if override is not None:
            return override(request)
        handler = _ROUTES.get(route)
        if handler is None:
            return error(404, "not_found", f"no route {route}")
        unauthenticated = route[1] in ("/api/v1/auth/login", "/api/v1/health")
        if not unauthenticated and request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return error(401, "unauthorized", "missing or stale token")
        return handler(self, request)

    # --- routes ---------------------------------------------------------------------------

    def _login(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("password") != self.password:
            return error(401, "invalid_credentials", "no role matched")
        self.logins += 1
        payload: dict[str, Any] = {"role": "admin", "expires_at": 0, "session_id": "s"}
        if request.url.params.get("include_token") == "true":
            payload["token"] = TOKEN
        return httpx.Response(200, json=payload)

    def _logout(self, request: httpx.Request) -> httpx.Response:
        self.logouts += 1
        return httpx.Response(204)

    def _start_search(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(202, json={"search_id": 42, "query": body.get("query")})

    def _search_results(self, request: httpx.Request) -> httpx.Response:
        window = _page(request, self.results)
        return httpx.Response(
            200,
            json={"results": window, "search_id": 42, "progress": self.progress},
        )

    def _stop_search(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    def _get_status(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.status)

    def _get_preferences(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.preferences)

    def _patch_preferences(self, request: httpx.Request) -> httpx.Response:
        self.patched.append(json.loads(request.content))
        return httpx.Response(200, json=self.preferences)

    def _add_links(self, request: httpx.Request) -> httpx.Response:
        links = json.loads(request.content)["links"]
        self.added_links.extend(links)
        return httpx.Response(202, json={"results": [{"id": link, "ok": True} for link in links]})

    def _get_downloads(self, request: httpx.Request) -> httpx.Response:
        window = _page(request, self.downloads)
        return httpx.Response(200, json={"downloads": window, "total": len(self.downloads)})

    def _get_shared(self, request: httpx.Request) -> httpx.Response:
        window = _page(request, self.shared)
        return httpx.Response(200, json={"shared": window, "total": len(self.shared)})


def _page(request: httpx.Request, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keyset paging on ``hash``, with the surface's own 100-row default for ``limit``."""
    params = request.url.params
    after = params.get("after")
    window = rows
    if after is not None:
        window = [row for row in rows if str(row.get("hash", "")) > after]
    limit = int(params.get("limit", DEFAULT_LIMIT))
    return window[:limit]


_ROUTES: dict[tuple[str, str], Callable[[FakeAmuleApi, httpx.Request], httpx.Response]] = {
    ("POST", "/api/v1/auth/login"): FakeAmuleApi._login,
    ("POST", "/api/v1/auth/logout"): FakeAmuleApi._logout,
    ("POST", "/api/v1/search"): FakeAmuleApi._start_search,
    ("GET", "/api/v1/search/42/results"): FakeAmuleApi._search_results,
    ("POST", "/api/v1/search/42/stop"): FakeAmuleApi._stop_search,
    ("GET", "/api/v1/status"): FakeAmuleApi._get_status,
    ("GET", "/api/v1/preferences"): FakeAmuleApi._get_preferences,
    ("PATCH", "/api/v1/preferences"): FakeAmuleApi._patch_preferences,
    ("POST", "/api/v1/downloads"): FakeAmuleApi._add_links,
    ("GET", "/api/v1/downloads"): FakeAmuleApi._get_downloads,
    ("GET", "/api/v1/shared"): FakeAmuleApi._get_shared,
}
