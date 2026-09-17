"""The amuled endpoint shared by the EC / download / orchestration integration suites.

The daemon is provided by the CALLER (env vars below), not started by the test process:
testcontainers' own bridge network is unusable on some hosts. See docs/contributing/testing.md.
"""

import os
from typing import NamedTuple

import pytest

HOST_VAR = "MULEWATCH_TEST_EC_HOST"
PORT_VAR = "MULEWATCH_TEST_EC_PORT"
PASSWORD_VAR = "MULEWATCH_TEST_EC_PASSWORD"

_SKIP_REASON = (
    f"{HOST_VAR} is not set: these suites need an amuled you provide. Start one with\n"
    "  docker run -d --rm --name mulewatch-test-amuled -e GUI_PWD=indexer-ec-test \\\n"
    "      -p 4712:4712 ngosang/amule:3.0.0-1\n"
    f"then export {HOST_VAR}=127.0.0.1 {PORT_VAR}=4712 {PASSWORD_VAR}=indexer-ec-test\n"
    "(full instructions: docs/contributing/testing.md)"
)


class EcEndpoint(NamedTuple):
    host: str
    port: int
    password: str


@pytest.fixture(scope="session")
def amuled() -> EcEndpoint:
    host = os.environ.get(HOST_VAR)
    if host is None:
        # CI always provides a daemon: a skip there would silently revive the dead-suite problem.
        if os.environ.get("CI"):
            pytest.fail(_SKIP_REASON)
        pytest.skip(_SKIP_REASON)
    return EcEndpoint(
        host=host,
        port=int(os.environ.get(PORT_VAR, "4712")),
        password=os.environ.get(PASSWORD_VAR, "indexer-ec-test"),
    )
