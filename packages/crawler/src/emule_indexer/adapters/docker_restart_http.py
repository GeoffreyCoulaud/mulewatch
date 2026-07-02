"""``HttpMuleRestarter`` adapter: restart amuled via the docker-socket-proxy (port-sync §5.3).

``POST {proxy}/v1.43/containers/amuled/restart`` to the minimal-surface proxy (wollomatic:
the allowlist permits ONLY this path+method; the crawler NEVER sees the Docker socket). Docker
returns **204 No Content** on a successful restart → any 2xx = success; non-2xx / timeout / connect
error → ``RestarterError`` (the loop absorbs it as an alert + backoff). NO internal retry (the
next cycle will retry, under rate-limit). ``aclose`` closes the httpx client (shutdown-time
composition).
"""

import logging

import httpx

from emule_indexer.ports.mule_restarter import RestarterError

_logger = logging.getLogger("emule_indexer.adapters.docker_restart_http")

# Docker Engine API path for amuled's restart: this is EXACTLY what the proxy's allowlist
# permits (regex ``/v1\..{1,2}/containers/amuled/restart`` wollomatic-side). v1.43 = stable API.
_DEFAULT_RESTART_PATH = "/v1.43/containers/amuled/restart"


class HttpMuleRestarter:
    """httpx implementation of the ``MuleRestarter`` port (STRUCTURAL satisfaction)."""

    def __init__(
        self, client: httpx.AsyncClient, *, restart_path: str = _DEFAULT_RESTART_PATH
    ) -> None:
        self._client = client
        self._restart_path = restart_path

    async def restart(self) -> None:
        """``POST`` the restart to the proxy; 2xx → success; else ``RestarterError`` (absorbed)."""
        try:
            response = await self._client.post(self._restart_path)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RestarterError(f"restart proxy replied {error.response.status_code}") from error
        except httpx.HTTPError as error:
            raise RestarterError(f"restart proxy unreachable ({error})") from error
        _logger.info("amuled restart requested (status %d)", response.status_code)

    async def aclose(self) -> None:
        """Closes the httpx client (called by composition at shutdown)."""
        await self._client.aclose()
