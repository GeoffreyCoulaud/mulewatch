"""``S6MuleRestarter`` adapter: restart amuled through s6 (single-container design §9).

The crawler and amuled share one container and one process tree, supervised by s6, so restarting
amuled is a local ``s6-svc -r`` on its service directory — no Docker API, no socket proxy. We use
the documented ``s6-svc`` command rather than writing to ``supervise/control`` ourselves: the
byte-level FIFO protocol is an s6 internal detail.

``s6-svc`` returns immediately once the signal is queued (it does not wait for the restart), so
there is nothing to poll here. Exit 0 → success; any other exit code, or an ``s6-svc`` that cannot
be run at all → ``RestarterError``, absorbed by the port-sync loop (alert + backoff). NO internal
retry: the next cycle retries under the rate-limit.
"""

import asyncio
import logging
from collections.abc import Sequence

from mulewatch.ports.mule_restarter import RestarterError

_logger = logging.getLogger("mulewatch.adapters.s6_restart")

# The service directory is fixed by the image layout (/etc/services.d/amuled), like the webui's
# 0.0.0.0:8080 bind: one container, one amuled, nothing for an operator to point elsewhere.
_RESTART_COMMAND: tuple[str, ...] = ("s6-svc", "-r", "/etc/services.d/amuled")


class S6MuleRestarter:
    """``MuleRestarter`` implementation over ``s6-svc`` (STRUCTURAL satisfaction).

    ``command`` is injectable so a test can substitute a real program with a known exit code;
    production always takes the default.
    """

    def __init__(self, *, command: Sequence[str] = _RESTART_COMMAND) -> None:
        self._command = tuple(command)

    async def restart(self) -> None:
        """Run ``s6-svc -r``; exit 0 → success, anything else → ``RestarterError`` (absorbed)."""
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise RestarterError(f"cannot run {self._command[0]} ({error})") from error
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise RestarterError(f"{self._command[0]} exited {process.returncode}: {detail}")
        _logger.info("amuled restart requested (%s)", " ".join(self._command))
