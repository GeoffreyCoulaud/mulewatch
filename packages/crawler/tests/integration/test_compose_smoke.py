"""e2e smoke of the ASSEMBLED docker compose stack, without VPN (packaging spec §5 - F-D1).

Dedicated run: ( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
Docker + docker compose v2 required. Brings up amuled + crawler (the whole stack:
tests/smoke/compose.yaml has no profiles) and asserts the WIRING, NO real download (amuled has
neither an eD2k server nor a VPN; only its EC server is exercised):
  1. `docker compose build` succeeds (the image builds).
  2. the crawler stays Up and its in-process webui answers /health.
  3. both deployment entry points render with `docker compose config`.
Ephemeral volumes: each scenario runs `docker compose down -v` in a finally.

Mechanics established EMPIRICALLY (compose v5, Docker 29):
  * The compose files' relative paths are resolved against the project-directory. We PIN it
    explicitly to `_REPO_ROOT` via `--project-directory` (cf. `_run`): `./tests/smoke/...` and
    `context: .` resolve deterministically, without depending on the default (cwd vs the `-f`
    file's directory). The `subprocess.run` calls also run `cwd=_REPO_ROOT`.
  * The DBs are written by the crawler (uid 999, ``read_only: true``) into the REAL named
    volumes ``catalog-db``/``local-db`` (mounted ``/data/catalog`` + ``/data/local``). The
    Dockerfile creates these mount points owned by ``nonroot`` => an EMPTY named volume inherits
    999:999 ownership at first mount, so the non-root crawler can create its SQLite files there.
    The smoke DELIBERATELY exercises this real persistence path to catch any perms regression
    (root-owned named volume => ``unable to open database file``).
"""

import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.compose_integration

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SMOKE = _REPO_ROOT / "tests/smoke/compose.yaml"

# In CI, the build step pre-builds the image and passes IMAGE_TAG; the smoke then consumes it
# WITHOUT a rebuild. Locally (IMAGE_TAG absent) we rebuild via compose, as before.
_IMAGE_TAG = os.environ.get("IMAGE_TAG")
_USES_PREBUILT = _IMAGE_TAG is not None
_BUILD_FLAGS: tuple[str, ...] = () if _USES_PREBUILT else ("--build",)

# Explicit (label, path) pairs rather than string-formatting f"{entry}.compose.yml": the two stack
# files no longer share a common naming pattern (the default stack file is just `compose.yaml`).
_ENTRY_POINTS: tuple[tuple[str, str], ...] = (
    ("compose", "deploy/compose.yaml"),
    ("gluetun", "deploy/gluetun.compose.yml"),
)
# No compose profile anywhere: every service of a stack starts unconditionally.
_ALWAYS_ON_SERVICES = frozenset({"crawler", "amuled"})
# VPN-stack-only: gluetun itself plus the docker-proxy that serves the port-sync.
_GLUETUN_ONLY_SERVICES = frozenset({"gluetun", "docker-proxy"})

_CONFIG_ENV = {
    "WIREGUARD_PRIVATE_KEY": "x",
    "AMULE_EC_PASSWORD": "x",
    "SERVER_COUNTRIES": "",
    "LISTEN_PORT": "4662",
}

# Isolated project (unique prefix per run) so we NEVER touch a real stack on the host.
_PROJECT = f"emule_smoke_{uuid.uuid4().hex[:8]}"

# gluetun is disabled in the smoke, but compose interpolates its variables at PARSE time: we
# stub them so `config`/`build`/`up` do not fail on missing variables.
_ENV_STUB = {
    "WIREGUARD_PRIVATE_KEY": "smoke-unused",
    "AMULE_EC_PASSWORD": "smoke-unused",
    "SERVER_COUNTRIES": "",
}


def _run(*args: str, files: tuple[Path, ...], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run `docker compose -p <project> -f ... <args>` from the repo root (cwd)."""
    file_flags: list[str] = []
    for path in files:
        file_flags += ["-f", str(path)]
    command = [
        "docker",
        "compose",
        "-p",
        _PROJECT,
        "--project-directory",
        str(_REPO_ROOT),
        *file_flags,
        *args,
    ]
    return subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            **_ENV_STUB,
            **({"IMAGE_TAG": _IMAGE_TAG} if _IMAGE_TAG is not None else {}),
        },
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _down(files: tuple[Path, ...]) -> None:
    """Idempotent tear-down: removes the project's containers + volumes + orphans."""
    _run("down", "-v", "--remove-orphans", files=files, timeout=180)


def _service_state(service: str, files: tuple[Path, ...]) -> tuple[str, int]:
    """(State, ExitCode) of the service via `ps -a --format json` (one JSON object per line)."""
    result = _run("ps", "-a", "--format", "json", service, files=files, timeout=60)
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("Service") == service:
            return str(obj.get("State")), int(obj.get("ExitCode"))
    raise AssertionError(f"service {service!r} not found in `ps`: {result.stdout!r}")


def _wait_state(
    service: str, target: str, files: tuple[Path, ...], *, attempts: int = 30, delay: float = 2.0
) -> tuple[str, int]:
    """Loop until `service` reaches `target` (or fail after attempts)."""
    last: tuple[str, int] = ("<absent>", -1)
    for _ in range(attempts):
        last = _service_state(service, files)
        if last[0] == target:
            return last
        time.sleep(delay)
    raise AssertionError(f"{service} did not reach {target!r} (last state: {last})")


def _wait_webui_health(files: tuple[Path, ...], *, attempts: int = 20, delay: float = 1.5) -> None:
    """Poll the crawler's in-process webui ``/health`` until 200 (readiness probe).

    The webui runs on its OWN thread inside the crawler; the container reaches ``running`` before
    uvicorn has bound its socket, so a one-shot check races startup. On exhaustion, attach the
    crawler logs so a real crash (the webui thread degrading) is diagnosable, not a bare error.
    """
    check = (
        "exec",
        "-T",
        "crawler",
        "python",
        "-c",
        "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/health').status)",
    )
    last = "<never ran>"
    for _ in range(attempts):
        health = _run(*check, files=files, timeout=60)
        if health.returncode == 0 and health.stdout.strip() == "200":
            return
        last = f"rc={health.returncode} out={health.stdout.strip()!r} err={health.stderr.strip()!r}"
        time.sleep(delay)
    logs = _run("logs", "--no-color", "--tail", "60", "crawler", files=files, timeout=60)
    raise AssertionError(
        f"crawler webui /health never returned 200 (last: {last})\n"
        f"--- crawler logs ---\n{logs.stdout}{logs.stderr}"
    )


@pytest.fixture
def project_files() -> Iterator[tuple[Path, ...]]:
    """Standalone smoke compose file + surrounding tear-down."""
    base = (_SMOKE,)
    _down(base)
    try:
        yield base
    finally:
        _down(base)


@pytest.mark.skipif(_USES_PREBUILT, reason="image prebuilt in CI - nothing to build")
def test_build_succeeds(project_files: tuple[Path, ...]) -> None:
    result = _run("build", files=project_files, timeout=900)
    assert result.returncode == 0, result.stderr


def test_crawler_stays_up_and_serves_its_webui(project_files: tuple[Path, ...]) -> None:
    result = _run("up", "-d", *_BUILD_FLAGS, files=project_files, timeout=900)
    assert result.returncode == 0, result.stderr

    assert _wait_state("amuled", "running", project_files)[0] == "running"
    assert _wait_state("crawler", "running", project_files)[0] == "running"
    # The crawler ALSO serves the read-only webui in-process (spec P4). Poll its /health from
    # inside the crawler container (exec; no host port needed): the webui thread binds shortly
    # AFTER the container is `running`, so this is a readiness probe, not a one-shot check. Proves
    # the single process crawls AND serves HTTP without a separate service.
    _wait_webui_health(project_files)


@pytest.mark.parametrize(
    ("label", "path"), _ENTRY_POINTS, ids=[label for label, _ in _ENTRY_POINTS]
)
def test_entrypoint_config_renders(label: str, path: str) -> None:
    """`docker compose -f <stack file> config` renders without error.

    Locks in include + forward-refs + anchors/merge + interpolation (no daemon required; the
    bind-mount sources need not exist for `config`). Also asserts the resulting service set: no
    profile gates anything, so both stacks render crawler + amuled, and the VPN stack adds
    gluetun + docker-proxy.
    """
    command = ["docker", "compose", "-f", path, "config"]
    result = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **_CONFIG_ENV},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr

    rendered = yaml.safe_load(result.stdout)
    assert isinstance(rendered, dict)
    services = set(rendered.get("services", {}))
    assert services >= _ALWAYS_ON_SERVICES, f"{path}: missing always-on services, got {services}"
    expected_gluetun = _GLUETUN_ONLY_SERVICES if label == "gluetun" else frozenset()
    assert _GLUETUN_ONLY_SERVICES & services == expected_gluetun, (
        f"{path}: unexpected VPN-stack services, got {services}"
    )
