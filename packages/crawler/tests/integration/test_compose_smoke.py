"""e2e smoke of the ASSEMBLED docker compose stack, without VPN (single-container design §9).

Dedicated run: ( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
Docker + docker compose v2 required. Brings up the ONE service of tests/smoke/compose.yaml —
the crawler, amuled and amuleweb under s6 in a single container — and asserts the WIRING, NO real
download (amuled has neither an eD2k server nor a VPN; only its EC server is exercised):
  1. `docker compose build` succeeds (the image builds).
  2. the container stays Up, turns `healthy`, supervises its three s6 services, and its
     in-process webui answers /health.
  3. both deployment entry points render with `docker compose config`, as one service each.
Tear-down: `docker compose down -v` plus the throwaway state directory, in a finally.

Mechanics established EMPIRICALLY (compose v5, Docker 29):
  * The compose file's relative paths are resolved against the project-directory. We PIN it
    explicitly to `_REPO_ROOT` via `--project-directory` (cf. `_run`): `./tests/smoke/...` and
    `context: .` resolve deterministically, without depending on the default (cwd vs the `-f`
    file's directory). The `subprocess.run` calls also run `cwd=_REPO_ROOT`.
  * State lives in BIND MOUNTS under `SMOKE_STATE`, like the real stacks — named volumes are
    gone. The test creates the three subdirectories as the invoking user and passes its own
    uid/gid as PUID/PGID, so the smoke exercises the real ownership path: the container's root
    PID 1 chowns those mount points, then every service drops to the `amule` user and writes
    there. A regression on that path shows up as `unable to open database file`.
  * The image hard-requires PUID, PGID, AMULE_EC_PASSWORD and WEBUI_PWD: without them the
    startup one-shot exits 1 and the container dies. They are supplied on every compose call,
    since the file is re-parsed each time (and each has a `:?` guard).
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.compose_integration

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SMOKE = _REPO_ROOT / "tests/smoke/compose.yaml"

_SERVICE = "mulewatch"
_S6_SERVICES = ("amuled", "amuleweb", "mulewatch")

# In CI, the build step pre-builds the image and passes IMAGE_TAG; the smoke then consumes it
# WITHOUT a rebuild. Locally (IMAGE_TAG absent) we rebuild via compose, as before.
_IMAGE_TAG = os.environ.get("IMAGE_TAG")
_USES_PREBUILT = _IMAGE_TAG is not None
_BUILD_FLAGS: tuple[str, ...] = () if _USES_PREBUILT else ("--build",)

# Explicit (label, path) pairs: the two stack files share no naming pattern.
_ENTRY_POINTS: tuple[tuple[str, str], ...] = (
    ("compose", "deploy/compose.yml"),
    ("gluetun", "deploy/gluetun.compose.yml"),
)
# No compose profile anywhere: every service of a stack starts unconditionally.
_ALWAYS_ON_SERVICES = frozenset({_SERVICE})
# VPN-stack-only. The socket proxy that used to sit here is gone with the Docker API: port-sync
# restarts amuled with `s6-svc` inside the container now (design §9).
_GLUETUN_ONLY_SERVICES = frozenset({"gluetun"})
_DELETED_SERVICES = frozenset({"docker-proxy", "crawler", "amuled"})

# Isolated project (unique prefix per run) so we NEVER touch a real stack on the host.
_PROJECT = f"emule_smoke_{uuid.uuid4().hex[:8]}"

# Throwaway bind-mount root, named after the project so two runs never share it. Computed, not
# created, at import time: this module is collected by every gate run, and only the fixture
# below (which runs when the suite is actually selected) touches the filesystem.
_STATE_DIR = Path(tempfile.gettempdir()) / _PROJECT
_STATE_SUBDIRS = ("amule", "data", "downloads")

_EC_PASSWORD = "smoke-ec-password"

# Everything the smoke stack interpolates. PUID/PGID are OURS on purpose: the bind mounts must
# stay readable from the host, which is the whole reason named volumes were dropped.
_SMOKE_ENV = {
    "PUID": str(os.getuid()),
    "PGID": str(os.getgid()),
    "AMULE_EC_PASSWORD": _EC_PASSWORD,
    "WEBUI_PWD": "smoke-webui-password",
    "SMOKE_STATE": str(_STATE_DIR),
}

# `docker compose config` on the deployment entry points interpolates at PARSE time, gluetun's
# variables included: stub them so a missing variable is not what fails.
_CONFIG_ENV = {
    **_SMOKE_ENV,
    "WIREGUARD_PRIVATE_KEY": "x",
    "SERVER_COUNTRIES": "",
    "LISTEN_PORT": "4662",
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
            **_SMOKE_ENV,
            **({"IMAGE_TAG": _IMAGE_TAG} if _IMAGE_TAG is not None else {}),
        },
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _down(files: tuple[Path, ...]) -> None:
    """Idempotent tear-down: removes the project's containers + volumes + orphans."""
    _run("down", "-v", "--remove-orphans", files=files, timeout=180)


def _ps_field(field: str, files: tuple[Path, ...]) -> str:
    """One field of the service's `ps -a --format json` entry (one JSON object per line)."""
    result = _run("ps", "-a", "--format", "json", _SERVICE, files=files, timeout=60)
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("Service") == _SERVICE:
            return str(obj.get(field))
    return f"<absent from `ps`: {result.stdout!r}>"


def _exec(*command: str, files: tuple[Path, ...]) -> str:
    """Run a command in the container; on failure return the error AS the observed value.

    Tolerant on purpose: every caller is a readiness probe polling for an expected output, and a
    container that is not ready yet fails the exec rather than printing something wrong.
    """
    result = _run("exec", "-T", _SERVICE, *command, files=files, timeout=120)
    if result.returncode != 0:
        return f"<rc={result.returncode} {result.stderr.strip()}>"
    return result.stdout.strip()


def _wait_for(
    label: str,
    probe: Callable[[], str],
    target: str,
    files: tuple[Path, ...],
    *,
    attempts: int = 30,
    delay: float = 2.0,
) -> None:
    """Poll `probe` until it returns `target`; on exhaustion attach the container logs.

    Everything here is a readiness probe: the three s6 services start at once and the container
    reports `running` well before amuled listens on EC or uvicorn has bound its socket. With a
    single container, its logs are THE diagnostic, so a failure carries them.
    """
    last = "<never ran>"
    for _ in range(attempts):
        last = probe()
        if last == target:
            return
        time.sleep(delay)
    logs = _run("logs", "--no-color", "--tail", "80", _SERVICE, files=files, timeout=60)
    raise AssertionError(
        f"{label}: expected {target!r}, last was {last!r}\n"
        f"--- {_SERVICE} logs ---\n{logs.stdout}{logs.stderr}"
    )


_WEBUI_HEALTH = (
    "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/health').status)"
)


@pytest.fixture
def project_files() -> Iterator[tuple[Path, ...]]:
    """Standalone smoke compose file, a fresh state directory, and the surrounding tear-down.

    The three subdirectories are created HERE, by the invoking user, rather than left to the
    daemon: compose would create a missing bind source as root, which is not the shape an
    operator's `deploy/` has.
    """
    base = (_SMOKE,)
    _down(base)
    shutil.rmtree(_STATE_DIR, ignore_errors=True)
    for name in _STATE_SUBDIRS:
        (_STATE_DIR / name).mkdir(parents=True)
    try:
        yield base
    finally:
        _down(base)
        shutil.rmtree(_STATE_DIR, ignore_errors=True)


@pytest.mark.skipif(_USES_PREBUILT, reason="image prebuilt in CI - nothing to build")
def test_build_succeeds(project_files: tuple[Path, ...]) -> None:
    result = _run("build", files=project_files, timeout=1800)
    assert result.returncode == 0, result.stderr


def test_one_container_supervises_the_three_services(project_files: tuple[Path, ...]) -> None:
    """The single container runs, turns healthy, and holds amuled + amuleweb + the crawler."""
    result = _run("up", "-d", *_BUILD_FLAGS, files=project_files, timeout=1800)
    assert result.returncode == 0, result.stderr

    _wait_for("state", lambda: _ps_field("State", project_files), "running", project_files)
    # The compose healthcheck tests s6-svstat's OUTPUT, not its exit code (it exits 0 for a
    # stopped service too). `healthy` therefore means amuled really is up under s6.
    _wait_for("health", lambda: _ps_field("Health", project_files), "healthy", project_files)
    # amuleweb is covered by nothing else: it has no port the crawler talks to and no EC surface.
    for service in _S6_SERVICES:
        svstat = partial(
            _exec, "s6-svstat", "-u", f"/etc/services.d/{service}", files=project_files
        )
        _wait_for(f"s6-svstat {service}", svstat, "true", project_files)
    # The crawler ALSO serves the read-only webui in-process (spec P4), on a bind fixed at
    # 0.0.0.0:8080 in code. Polled from inside the container, so no host port is needed.
    _wait_for(
        "webui /health",
        lambda: _exec("python", "-c", _WEBUI_HEALTH, files=project_files),
        "200",
        project_files,
    )


@pytest.mark.parametrize(
    ("label", "path"), _ENTRY_POINTS, ids=[label for label, _ in _ENTRY_POINTS]
)
def test_entrypoint_config_renders(label: str, path: str) -> None:
    """`docker compose -f <stack file> config` renders without error.

    Locks in include + interpolation + the `:?` guards (no daemon required; the bind-mount
    sources need not exist for `config`). Also asserts the resulting topology: one `mulewatch`
    service, the VPN stack adding only gluetun, nothing left of the four-service shape, and NO
    named volume — the operator must be able to `sqlite3 deploy/data/catalog.db` from the host.
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
    expected_gluetun = _GLUETUN_ONLY_SERVICES if label == "gluetun" else frozenset()
    assert services == _ALWAYS_ON_SERVICES | expected_gluetun, f"{path}: got {services}"
    assert not _DELETED_SERVICES & services, f"{path}: deleted service still declared, {services}"
    assert not rendered.get("volumes"), f"{path}: named volumes are gone, got {rendered['volumes']}"


# --- Completion scenario (scope-reduction spec §4) ---------------------------------------------
#
# amuled's IncomingDir, written by the startup one-shot into amule.conf. A file dropped there is
# hashed and shared at the next amuled start, which is how we obtain a REAL ed2k hash without
# ever computing one ourselves.
_INCOMING_DIR = "/downloads/incoming"
_SEEDED_TARGET = "062A"
_SEEDED_SIZE = 65536

# The one amuled is in THIS container, at the address fixed in code (design §6).
_EC_HOST, _EC_PORT = "127.0.0.1", 4712

_SHARED_HASHES = f"""
import asyncio, json
from mulewatch.adapters.mule_ec.client import AmuleEcClient

async def main() -> None:
    client = AmuleEcClient({_EC_HOST!r}, {_EC_PORT}, {_EC_PASSWORD!r})
    await client.connect()
    print(json.dumps(sorted(entry.ed2k_hash for entry in await client.shared_files())))
    await client.close()

asyncio.run(main())
"""

_SEED_ROW = f"""
import datetime, sqlite3, sys
now = datetime.datetime.now(datetime.UTC).isoformat()
conn = sqlite3.connect("/data/local.db", timeout=30)
conn.execute(
    "INSERT INTO downloads"
    " (ed2k_hash, target_id, state, queued_at, size_bytes, last_seen_at)"
    " VALUES (?, {_SEEDED_TARGET!r}, 'downloading', ?, {_SEEDED_SIZE}, ?)",
    (sys.argv[1], now, now),
)
conn.commit()
"""

_READ_ROW = """
import sqlite3, sys
conn = sqlite3.connect("/data/local.db", timeout=30)
row = conn.execute(
    "SELECT state, completed_at IS NOT NULL FROM downloads WHERE ed2k_hash = ?", (sys.argv[1],)
).fetchone()
print(row[0], bool(row[1]))
"""


def _exec_python(script: str, *args: str, files: tuple[Path, ...]) -> str:
    """Run `script` with the container's python and return its stdout (fails loudly)."""
    result = _run("exec", "-T", _SERVICE, "python", "-c", script, *args, files=files, timeout=120)
    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
    return result.stdout.strip()


def _shared_hashes(files: tuple[Path, ...]) -> frozenset[str] | None:
    """Hashes amuled currently shares, read over EC from inside the container.

    `None` means the EC call itself did not complete. That is a READINESS state, not a result:
    `s6-svc -r` tears amuled down while its listening socket is still accepting, so a connection
    opened in that window is accepted and then reset mid-handshake (observed in CI: the peer
    closed during `_authenticate`, right after the salt request). Callers poll on it.
    """
    output = _exec("python", "-c", _SHARED_HASHES, files=files)
    try:
        return frozenset(json.loads(output))
    except json.JSONDecodeError:
        return None


def _wait_new_shared_hash(
    before: frozenset[str], files: tuple[Path, ...], *, attempts: int = 20, delay: float = 2.0
) -> str:
    """Poll until exactly one hash appeared in amuled's shared list, and return it.

    Called right after `s6-svc -r`, so it is the readiness probe for the restart TOO: amuled is
    still shutting down for the first attempts (EC refuses or resets), then comes back and hashes
    the IncomingDir asynchronously. A failed EC call is therefore a poll iteration, not a failure.
    The "exactly one" bound is what identifies OUR file: the smoke amuled shares nothing else at
    that point (its only queue entry has no sources and no bytes, so it is not shared yet).
    """
    appeared: frozenset[str] = frozenset()
    for _ in range(attempts):
        current = _shared_hashes(files)
        if current is not None:
            appeared = current - before
            if len(appeared) == 1:
                return next(iter(appeared))
        time.sleep(delay)
    raise AssertionError(f"expected exactly one new shared hash, got {sorted(appeared)}")


def test_a_file_amuled_shares_is_recorded_completed(project_files: tuple[Path, ...]) -> None:
    """End to end: amuled shares a file that left the queue, the crawler completes and notifies.

    Drives the running container rather than an in-process cycle: the shipped image carries the
    EC adapter, amuled and the migrations, so the whole path (real EC connection over loopback,
    real amuled, real local.db on a bind mount) is exercised without building a parallel harness.
    The ed2k hash is never computed here; amuled computes it and we read it back over EC, which
    is what makes seeding a matching row possible at all.
    """
    result = _run("up", "-d", *_BUILD_FLAGS, files=project_files, timeout=1800)
    assert result.returncode == 0, result.stderr
    _wait_for("health", lambda: _ps_field("Health", project_files), "healthy", project_files)
    _wait_for(
        "webui /health",
        lambda: _exec("python", "-c", _WEBUI_HEALTH, files=project_files),
        "200",
        project_files,
    )

    before = _shared_hashes(project_files)
    assert before is not None, "amuled's EC server must answer before the restart"
    drop = _run(
        "exec",
        "-T",
        _SERVICE,
        "sh",
        "-c",
        f"head -c {_SEEDED_SIZE} /dev/urandom > {_INCOMING_DIR}/mulewatch-smoke.bin",
        files=project_files,
        timeout=120,
    )
    assert drop.returncode == 0, f"{drop.stdout}{drop.stderr}"
    # amuled only scans its IncomingDir at startup. It is an s6 service now, so the rescan is a
    # process restart inside the container — the same `s6-svc -r` the port-sync restarter runs,
    # and the container itself never goes down.
    restart = _run(
        "exec",
        "-T",
        _SERVICE,
        "s6-svc",
        "-r",
        "/etc/services.d/amuled",
        files=project_files,
        timeout=60,
    )
    assert restart.returncode == 0, f"{restart.stdout}{restart.stderr}"
    ed2k_hash = _wait_new_shared_hash(before, project_files)

    # The file is shared and was never in the download queue: the completion signal is complete.
    _exec_python(_SEED_ROW, ed2k_hash, files=project_files)
    _wait_for(
        f"download {ed2k_hash}",
        lambda: _exec("python", "-c", _READ_ROW, ed2k_hash, files=project_files),
        "completed True",
        project_files,
    )

    # The completion also reached the observability pipeline (the notification, target-labelled).
    logs = _run("logs", "--no-color", _SERVICE, files=project_files, timeout=60)
    assert f"download completed: {_SEEDED_TARGET}" in logs.stdout, logs.stdout[-4000:]
