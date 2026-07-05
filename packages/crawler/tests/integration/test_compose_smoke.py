"""e2e smoke of the ASSEMBLED docker compose stack, without VPN (packaging spec §5 — F-D1).

Dedicated run: ( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
Docker + docker compose v2 required. Brings up verifier + crawler + amuled (gluetun removed via
tests/smoke/compose.yaml) and asserts the WIRING — NO real download (amuled has neither an eD2k
server nor a VPN; only its EC server is exercised):
  1. `docker compose build` succeeds (the 2 images build).
  2. download: verifier becomes healthy (/health 200) AND the crawler stays Up.
  3. observer: crawler starts WITHOUT verifier and stays Up.
  4. download fail-fast: download crawler with verifier_url but verifier ABSENT => exit != 0.
Ephemeral volumes: each scenario runs `docker compose down -v` in a finally.

Mechanics established EMPIRICALLY (compose v5, Docker 29):
  * The compose files' relative paths are resolved against the project-directory. We PIN it
    explicitly to `_REPO_ROOT` via `--project-directory` (cf. `_run`): `./tests/smoke/...`,
    `context: .` and `./deploy/config/verifier.yml` resolve deterministically, without depending
    on the default (cwd vs the `-f` file's directory). The `subprocess.run` calls also run
    `cwd=_REPO_ROOT`.
  * The DBs are written by the crawler (uid 999, ``read_only: true``) into the REAL named
    volumes ``catalog-db``/``local-db`` (mounted ``/data/catalog`` + ``/data/local``). The
    Dockerfile creates these mount points owned by ``nonroot`` => an EMPTY named volume inherits
    999:999 ownership at first mount, so the non-root crawler can create its SQLite files there.
    The smoke DELIBERATELY exercises this real persistence path to catch any perms regression
    (root-owned named volume => ``unable to open database file``).
  * Download: an override re-adds ``depends_on: { verifier: service_healthy }`` (absent from the
    smoke base so the ``observer`` profile is valid) => DETERMINISTIC startup after the verifier
    is healthy.
  * Observer: an override re-mounts ``crawler.observer.yml`` (without a download section) and we
    bring up the ``observer`` profile (the verifier service does not exist there).
  * Fail-fast: an override forces ``restart: "no"`` (otherwise ``unless-stopped`` loops forever);
    we bring up amuled+crawler WITHOUT a profile (=> verifier ABSENT); the download crawler
    health-checks the verifier at startup, fails, and FREEZES in ``exited`` with a code != 0.
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

# In CI, the build step pre-builds the images and passes IMAGE_TAG; the smoke then consumes them
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
# `download` is the only remaining compose profile in the deploy stacks (monitoring/webui are gone —
# webui/prometheus/grafana are now in the DEFAULT service set, no profile needed).
_PROFILE_CASES: tuple[tuple[str, ...], ...] = ((), ("download",))
_CONFIG_CASES: tuple[tuple[tuple[str, str], tuple[str, ...]], ...] = tuple(
    (entry, profiles) for entry in _ENTRY_POINTS for profiles in _PROFILE_CASES
)
_CONFIG_CASE_IDS = [
    f"{label}-{'+'.join(profiles) if profiles else 'none'}"
    for (label, _path), profiles in _CONFIG_CASES
]

# Always rendered, with or without --profile download (royal-road: webui/prometheus/grafana are
# always-on since deploy/compose.yaml + deploy/gluetun.compose.yml stopped gating them behind a
# profile).
_ALWAYS_ON_SERVICES = frozenset({"crawler", "amuled", "webui", "prometheus", "grafana"})
# Gated behind --profile download in base.compose.yml (docker-proxy is gluetun-stack-only and
# asserted in test_entrypoint_config_renders).
_DOWNLOAD_ONLY_SERVICES = frozenset({"verifier", "freshclam"})

_CONFIG_ENV = {
    "WIREGUARD_PRIVATE_KEY": "x",
    "AMULE_EC_PASSWORD": "x",
    "GRAFANA_PWD": "x",
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

# Volume lists for the overrides: we mount the smoke configs + the REAL named volumes
# (catalog-db/local-db/quarantine). The non-root crawler (uid 999) creates its SQLite DBs there —
# the Dockerfile owns the mount points as nonroot so that empty volumes inherit
# 999:999. The bind paths stay relative to the project-directory (pinned to _REPO_ROOT).
_DOWNLOAD_LOCAL_VOLUMES = [
    "./tests/smoke/crawler.yml:/app/config/crawler.yml:ro",
    "./tests/smoke/targets.yml:/app/config/targets.yml:ro",
    "./deploy/config/crawler/matcher.yml:/app/config/matcher.yml:ro",
    "quarantine:/data/quarantine",
    "catalog-db:/data/catalog",
    "local-db:/data/local",
]
_OBSERVER_LOCAL_VOLUMES = [
    "./tests/smoke/crawler.observer.yml:/app/config/crawler.yml:ro",
    "./tests/smoke/targets.yml:/app/config/targets.yml:ro",
    "./deploy/config/crawler/matcher.yml:/app/config/matcher.yml:ro",
    "quarantine:/data/quarantine",
    "catalog-db:/data/catalog",
    "local-db:/data/local",
]


def _write_override(tmp_path: Path, name: str, crawler_body: str) -> Path:
    """Write a scenario override file (YAML) under tmp_path and return its path."""
    path = tmp_path / name
    path.write_text(crawler_body)
    return path


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
    """Idempotent tear-down: removes the project's containers + volumes + orphans.

    ``--profile download`` is MANDATORY: with no active profile, ``down`` ignores
    profile-gated services (compose v5) and would leave a verifier from a previous scenario
    running (the verifier service is only defined in the ``download`` profile). The ``download``
    profile is a superset (amuled+crawler+verifier), so it also cleans up the
    observer/fail-fast scenarios.
    """
    _run("--profile", "download", "down", "-v", "--remove-orphans", files=files, timeout=180)


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


@pytest.fixture
def project_files() -> Iterator[tuple[Path, ...]]:
    """Standalone smoke compose file + surrounding tear-down."""
    base = (_SMOKE,)
    _down(base)
    try:
        yield base
    finally:
        _down(base)


@pytest.mark.skipif(_USES_PREBUILT, reason="images prebuilt in CI — nothing to build")
def test_build_succeeds(project_files: tuple[Path, ...]) -> None:
    result = _run("--profile", "download", "build", files=project_files, timeout=900)
    assert result.returncode == 0, result.stderr


def test_download_verifier_healthy_and_crawler_up(
    project_files: tuple[Path, ...], tmp_path: Path
) -> None:
    override = _write_override(
        tmp_path,
        "download.override.yaml",
        _yaml_crawler(
            depends_on=(
                "    depends_on:\n"
                "      amuled:\n"
                "        condition: service_started\n"
                "      verifier:\n"
                "        condition: service_healthy\n"
            ),
            volumes=_DOWNLOAD_LOCAL_VOLUMES,
        ),
    )
    files = (*project_files, override)
    result = _run("--profile", "download", "up", "-d", *_BUILD_FLAGS, files=files, timeout=900)
    assert result.returncode == 0, result.stderr

    # depends_on: service_healthy => the verifier is already healthy when the crawler starts.
    assert _service_state("verifier", files)[0] == "running"
    assert _wait_state("crawler", "running", files)[0] == "running"

    # /health via exec in the verifier (the verify-internal network is internal, no Internet).
    health = _run(
        "exec",
        "-T",
        "verifier",
        "python",
        "-c",
        "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/health').status)",
        files=files,
        timeout=60,
    )
    assert health.returncode == 0, health.stderr
    assert health.stdout.strip() == "200"


def test_observer_starts_without_verifier(project_files: tuple[Path, ...], tmp_path: Path) -> None:
    override = _write_override(
        tmp_path,
        "observer.override.yaml",
        _yaml_crawler(
            depends_on=None,
            volumes=_OBSERVER_LOCAL_VOLUMES,
        ),
    )
    files = (*project_files, override)
    result = _run("--profile", "observer", "up", "-d", *_BUILD_FLAGS, files=files, timeout=900)
    assert result.returncode == 0, result.stderr

    # The observer profile does NOT define the verifier; the crawler starts anyway and stays Up.
    assert _wait_state("crawler", "running", files)[0] == "running"


def test_download_without_verifier_fails_fast(
    project_files: tuple[Path, ...], tmp_path: Path
) -> None:
    override = _write_override(
        tmp_path,
        "failfast.override.yaml",
        _yaml_crawler(
            depends_on=None,
            volumes=_DOWNLOAD_LOCAL_VOLUMES,
            restart_no=True,
        ),
    )
    files = (*project_files, override)
    # We bring up ONLY amuled + crawler (without `--profile download`) => the verifier is ABSENT.
    # Download config (verifier_url present) => the crawler health-checks the verifier at startup,
    # fails, and with restart: "no" FREEZES in exited (no restart loop).
    result = _run("up", "-d", *_BUILD_FLAGS, "amuled", "crawler", files=files, timeout=900)
    assert result.returncode == 0, result.stderr

    state, exit_code = _wait_state("crawler", "exited", files)
    assert state == "exited"
    assert exit_code != 0


@pytest.mark.parametrize("entry,profiles", _CONFIG_CASES, ids=_CONFIG_CASE_IDS)
def test_entrypoint_config_renders(entry: tuple[str, str], profiles: tuple[str, ...]) -> None:
    """`docker compose -f <stack file> [--profile download] config` renders without error.

    Locks in include + forward-refs + anchors/merge + interpolation (no daemon required; the
    bind-mount sources need not exist for `config`). Also asserts the resulting service set: webui/
    prometheus/grafana render in the DEFAULT set (no profile needed), and `--profile download` is
    the only lever that adds verifier/freshclam (docker-proxy too, in the gluetun stack).
    """
    label, path = entry
    profile_flags: list[str] = []
    for profile in profiles:
        profile_flags += ["--profile", profile]
    command = [
        "docker",
        "compose",
        "-f",
        path,
        *profile_flags,
        "config",
    ]
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
    if "download" in profiles:
        assert services >= _DOWNLOAD_ONLY_SERVICES, (
            f"{path}: missing download services, got {services}"
        )
    else:
        assert not (_DOWNLOAD_ONLY_SERVICES & services), (
            f"{path}: download-only services present without --profile download: {services}"
        )
    if label == "gluetun":
        assert ("docker-proxy" in services) == ("download" in profiles), (
            f"{path}: docker-proxy must render iff --profile download, got {services}"
        )


def _yaml_crawler(
    *,
    depends_on: str | None,
    volumes: list[str],
    restart_no: bool = False,
) -> str:
    """Compose a `services.crawler` override (volumes !override; tmpfs /tmp inherited from base)."""
    lines = ["services:", "  crawler:"]
    if restart_no:
        lines.append('    restart: !override "no"')
    if depends_on is not None:
        lines.append(depends_on.rstrip("\n"))
    lines.append("    volumes: !override")
    lines += [f"      - {volume}" for volume in volumes]
    return "\n".join(lines) + "\n"
