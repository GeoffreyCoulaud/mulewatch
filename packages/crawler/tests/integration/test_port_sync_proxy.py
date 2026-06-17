"""Smoke de la surface MINIMALE du `docker-proxy` (wollomatic) du port-sync (design §5.3).

Run dédié : ( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
Docker + docker compose v2 requis, ET accès au socket Docker (le proxy le monte en RO).

Ce que ça valide — NOTRE frontière de sécurité, pas le comportement d'un tiers :
  * la VRAIE allowlist de `compose.yaml` (`-allowPOST=/v1\\..{1,2}/containers/amuled/restart`,
    montée telle quelle — aucun duplicata, donc zéro drift) AUTORISE le restart du conteneur
    nommé exactement `amuled` (→ 204, ce qui PROUVE aussi `container_name: amuled` : sans lui le
    restart ferait 404) ;
  * elle REFUSE (403) tout le reste : stop/kill, lecture (`GET /containers/json`), un AUTRE
    conteneur, et la mauvaise méthode (GET) sur le bon endpoint.

Le `HttpMuleRestarter` lui-même est couvert en unit (httpx MockTransport) ; ici c'est la
config compose (allowlist + nom de conteneur) qui est exercée contre le VRAI proxy.

Note (à confirmer au 1er run) : wollomatic renvoie `403` sur un refus ; si une version rend un
autre code, ajuster `_DENIED`. Le `204` du restart suppose `DOCKER_GID` correct — calculé ici
depuis le gid du socket (sinon le proxy ne peut pas lire `/var/run/docker.sock`).
"""

import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.compose_integration

_REPO_ROOT = Path(__file__).resolve().parents[4]
_COMPOSE = _REPO_ROOT / "compose.yaml"
_DOCKER_SOCK = "/var/run/docker.sock"
_PROJECT = f"emule_portsync_{uuid.uuid4().hex[:8]}"
_PROXY_HOST_PORT = 12375  # port hôte (loopback) où on publie le 2375 interne du proxy
_API = "/v1.43"  # version d'API Docker stable (cf. HttpMuleRestarter)
_DENIED = 403  # wollomatic : code sur un refus d'allowlist

# gluetun est hors-jeu ici mais compose interpole ses variables au PARSE → on les stube.
_ENV_STUB = {
    "WIREGUARD_PRIVATE_KEY": "proxy-smoke-unused",
    "AMULE_EC_PASSWORD": "proxy-smoke-unused",
    "SERVER_COUNTRIES": "",
}


def _env() -> dict[str, str]:
    """Env des `docker compose` : PATH + stubs gluetun + DOCKER_GID = gid du socket Docker.

    Le proxy tourne en `65534:${DOCKER_GID}` ; pour lire `/var/run/docker.sock` (root:docker 660)
    son groupe DOIT être celui du socket. On le calcule pour que le test soit autonome.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "DOCKER_GID": str(os.stat(_DOCKER_SOCK).st_gid),
        **_ENV_STUB,
    }


def _compose(*args: str, override: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    """`docker compose -p <projet> -f compose.yaml -f <override> --profile full <args>`."""
    command = [
        "docker",
        "compose",
        "-p",
        _PROJECT,
        "-f",
        str(_COMPOSE),
        "-f",
        str(override),
        "--profile",
        "full",
        *args,
    ]
    return subprocess.run(
        command, cwd=_REPO_ROOT, env=_env(), capture_output=True, text=True, timeout=timeout
    )


@pytest.fixture
def proxy() -> Iterator[str]:
    """Monte le VRAI service `docker-proxy` (port publié) + un conteneur jetable `amuled`.

    L'override ne fait QU'ajouter une publication de port au proxy (l'allowlist reste celle de
    `compose.yaml`). Tear-down : `down -v` + suppression du conteneur `amuled`.
    """
    if not Path(_DOCKER_SOCK).exists():
        pytest.skip("socket Docker absent — test de surface du proxy non applicable")
    override = _REPO_ROOT / f".{_PROJECT}.proxy-override.yaml"
    override.write_text(
        f'services:\n  docker-proxy:\n    ports:\n      - "127.0.0.1:{_PROXY_HOST_PORT}:2375"\n'
    )
    # Conteneur jetable nommé EXACTEMENT `amuled` (l'allowlist le cible) — refuse de clobberer un
    # `amuled` préexistant (un vrai déploiement).
    exists = subprocess.run(
        ["docker", "ps", "-aq", "-f", "name=^amuled$"],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if exists.stdout.strip():
        override.unlink(missing_ok=True)
        pytest.skip("un conteneur `amuled` existe déjà sur l'hôte — test sauté (ne pas clobberer)")
    subprocess.run(
        ["docker", "run", "-d", "--name", "amuled", "alpine:3", "sleep", "600"],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    try:
        up = _compose("up", "-d", "docker-proxy", override=override, timeout=180)
        assert up.returncode == 0, up.stderr
        base = f"http://127.0.0.1:{_PROXY_HOST_PORT}"
        _wait_ready(base)
        yield base
    finally:
        _compose("down", "-v", "--remove-orphans", override=override, timeout=120)
        subprocess.run(
            ["docker", "rm", "-f", "amuled"], env=_env(), capture_output=True, timeout=60
        )
        override.unlink(missing_ok=True)


def _wait_ready(base: str, *, attempts: int = 30, delay: float = 1.0) -> None:
    """Attend que le proxy réponde (un refus 403 sur un endpoint interdit suffit à prouver qu'il
    écoute et applique l'allowlist)."""
    last: Exception | int | None = None
    for _ in range(attempts):
        try:
            resp = httpx.get(f"{base}{_API}/containers/json", timeout=2.0)
            if resp.status_code == _DENIED:
                return
            last = resp.status_code
        except httpx.HTTPError as error:
            last = error
        time.sleep(delay)
    raise AssertionError(f"docker-proxy pas prêt (dernier : {last})")


def test_allowlist_permits_restart_of_amuled_only(proxy: str) -> None:
    # AUTORISÉ : restart du conteneur `amuled` → forwardé à Docker → 204 (No Content). Prouve à la
    # fois l'allowlist ET `container_name: amuled` (sans lui : 404).
    restart = httpx.post(f"{proxy}{_API}/containers/amuled/restart", timeout=30.0)
    assert restart.status_code == 204, f"restart amuled refusé/échoué : {restart.status_code}"


@pytest.mark.parametrize(
    "method, path",
    [
        ("POST", f"{_API}/containers/amuled/stop"),  # stop interdit (seul restart autorisé)
        ("POST", f"{_API}/containers/amuled/kill"),  # kill interdit
        ("GET", f"{_API}/containers/json"),  # lecture de la liste interdite
        ("POST", f"{_API}/containers/other/restart"),  # un AUTRE conteneur interdit
        ("GET", f"{_API}/containers/amuled/restart"),  # bon endpoint, MAUVAISE méthode (GET)
    ],
)
def test_allowlist_denies_everything_else(proxy: str, method: str, path: str) -> None:
    resp = httpx.request(method, f"{proxy}{path}", timeout=10.0)
    assert resp.status_code == _DENIED, (
        f"{method} {path} aurait dû être REFUSÉ (403) par l'allowlist, reçu {resp.status_code}"
    )
