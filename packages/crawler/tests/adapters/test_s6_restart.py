"""Tests for ``S6MuleRestarter`` (single-container design §9): ``s6-svc -r`` on amuled's service.

The boundary faked here is the PROGRAM, not the spawning: every test really runs
``asyncio.create_subprocess_exec`` and really reaps a real child, only against ``/bin/true``,
``/bin/false`` or a nonexistent path instead of ``s6-svc``. That is the honest fake, because the
adapter's whole job IS the spawn-and-reap: what it must get right is the exit-code reading, the
stderr capture and the OSError path, all of which a real child exercises. ``s6-svc`` itself cannot
run in a unit test (it needs a live s6-supervise and its control FIFO), and mocking the spawn would
test nothing but the mock.
"""

import pytest

from mulewatch.adapters.s6_restart import _RESTART_COMMAND, S6MuleRestarter
from mulewatch.ports.mule_restarter import RestarterError


@pytest.mark.asyncio
async def test_exit_zero_is_success() -> None:
    await S6MuleRestarter(command=("/usr/bin/true",)).restart()  # no exception


@pytest.mark.asyncio
async def test_non_zero_exit_raises_restarter_error() -> None:
    with pytest.raises(RestarterError, match="exited 1"):
        await S6MuleRestarter(command=("/usr/bin/false",)).restart()


@pytest.mark.asyncio
async def test_stderr_is_reported_in_the_error() -> None:
    # s6-svc explains its refusals on stderr ("unable to control ..."); the message must reach
    # the loop's alert, otherwise an operator sees only a bare exit code.
    restarter = S6MuleRestarter(command=("/bin/sh", "-c", "echo 'unable to control' >&2; exit 111"))
    with pytest.raises(RestarterError, match="unable to control"):
        await restarter.restart()


@pytest.mark.asyncio
async def test_missing_binary_raises_restarter_error() -> None:
    # s6-svc absent (a run outside the container) → absorbed, never an escaping OSError.
    with pytest.raises(RestarterError, match="cannot run"):
        await S6MuleRestarter(command=("/nonexistent/s6-svc",)).restart()


def test_default_command_restarts_the_amuled_service() -> None:
    assert _RESTART_COMMAND == ("s6-svc", "-r", "/etc/services.d/amuled")
