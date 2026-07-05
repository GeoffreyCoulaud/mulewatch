"""Tests for ``HttpMuleRestarter`` (port-sync, design §5.3): 2xx → success, else ``RestarterError``.

httpx ``MockTransport`` idiom: Docker returns 204 No Content on a successful restart; anything
that is not 2xx / a timeout / a connect error → ``RestarterError`` (absorbed by the loop). No
internal retry.
"""

from collections.abc import Callable

import httpx
import pytest

from mulewatch.adapters.docker_restart_http import HttpMuleRestarter
from mulewatch.ports.mule_restarter import RestarterError

_RESTART_PATH = "/v1.43/containers/amuled/restart"


def _restarter_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> HttpMuleRestarter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://docker-proxy:2375")
    return HttpMuleRestarter(client, restart_path=_RESTART_PATH)


@pytest.mark.asyncio
async def test_204_is_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == _RESTART_PATH
        return httpx.Response(204)  # Docker: No Content on a successful restart

    restarter = _restarter_with_handler(handler)
    try:
        await restarter.restart()  # no exception
    finally:
        await restarter.aclose()


@pytest.mark.asyncio
async def test_200_is_success() -> None:
    restarter = _restarter_with_handler(lambda r: httpx.Response(200))
    try:
        await restarter.restart()  # any 2xx accepted
    finally:
        await restarter.aclose()


@pytest.mark.asyncio
async def test_404_raises_restarter_error() -> None:
    restarter = _restarter_with_handler(lambda r: httpx.Response(404, text="no such container"))
    try:
        with pytest.raises(RestarterError):
            await restarter.restart()
    finally:
        await restarter.aclose()


@pytest.mark.asyncio
async def test_500_raises_restarter_error() -> None:
    restarter = _restarter_with_handler(lambda r: httpx.Response(500, text="boom"))
    try:
        with pytest.raises(RestarterError):
            await restarter.restart()
    finally:
        await restarter.aclose()


@pytest.mark.asyncio
async def test_403_from_proxy_allowlist_raises_restarter_error() -> None:
    # If the proxy refuses (allowlist), 4xx → RestarterError (the loop alerts + backs off).
    restarter = _restarter_with_handler(lambda r: httpx.Response(403, text="forbidden"))
    try:
        with pytest.raises(RestarterError):
            await restarter.restart()
    finally:
        await restarter.aclose()


@pytest.mark.asyncio
async def test_connect_error_raises_restarter_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("proxy down")

    restarter = _restarter_with_handler(handler)
    try:
        with pytest.raises(RestarterError):
            await restarter.restart()
    finally:
        await restarter.aclose()


@pytest.mark.asyncio
async def test_timeout_raises_restarter_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    restarter = _restarter_with_handler(handler)
    try:
        with pytest.raises(RestarterError):
            await restarter.restart()
    finally:
        await restarter.aclose()


@pytest.mark.asyncio
async def test_default_restart_path_targets_amuled() -> None:
    # The default path targets the amuled container (the proxy allows only THIS path).
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.path)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://docker-proxy:2375")
    restarter = HttpMuleRestarter(client)  # default path
    try:
        await restarter.restart()
    finally:
        await restarter.aclose()
    assert captured == ["/v1.43/containers/amuled/restart"]


@pytest.mark.asyncio
async def test_aclose_closes_client() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(204))
    client = httpx.AsyncClient(transport=transport, base_url="http://docker-proxy:2375")
    restarter = HttpMuleRestarter(client)
    await restarter.aclose()
    assert client.is_closed is True
