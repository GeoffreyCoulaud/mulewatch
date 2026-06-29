"""Smoke e2e de la stack docker compose ASSEMBLÉE, sans VPN (spec packaging §5 — F-D1).

Run dédié : ( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
Docker + docker compose v2 requis. Monte verifier + crawler + amuled (gluetun retiré via
tests/smoke/compose.yaml) et asserte le CÂBLAGE — AUCUN téléchargement réel (amuled n'a ni serveur
eD2k ni VPN ; seul son serveur EC est sollicité) :
  1. `docker compose build` réussit (les 2 images se construisent).
  2. download : verifier devient healthy (/health 200) ET le crawler reste Up.
  3. observer : crawler démarre SANS verifier et reste Up.
  4. download fail-fast : crawler download avec verifier_url mais verifier ABSENT => exit != 0.
Volumes éphémères : chaque scénario fait `docker compose down -v` dans un finally.

Mécaniques arrêtées EMPIRIQUEMENT (compose v5, Docker 29) :
  * Les chemins relatifs des fichiers compose sont résolus contre le project-directory. On le FIXE
    explicitement à `_REPO_ROOT` via `--project-directory` (cf. `_run`) : `./tests/smoke/...`,
    `context: .` et `./deploy/config/verifier.yml` résolvent de façon déterministe, sans dépendre
    du défaut (cwd vs dossier du `-f`). Les `subprocess.run` tournent aussi `cwd=_REPO_ROOT`.
  * Les DB sont écrites par le crawler (uid 999, ``read_only: true``) dans les VRAIS volumes
    nommés ``catalog-db``/``local-db`` (montés ``/data/catalog`` + ``/data/local``). Le Dockerfile
    crée ces points de montage possédés par ``nonroot`` => un volume nommé VIDE hérite de la
    propriété 999:999 au premier mount, donc le crawler non-root peut y créer ses fichiers SQLite.
    Le smoke exerce DÉLIBÉRÉMENT ce chemin de persistance réel pour attraper toute régression de
    perms (volume nommé root-owned => ``unable to open database file``).
  * Download : un override ré-ajoute ``depends_on: { verifier: service_healthy }`` (absent de la
    base smoke pour que le profil ``observer`` valide) => démarrage DÉTERMINISTE après le verifier
    sain.
  * Observer : un override re-monte ``crawler.observer.yml`` (sans section download) et on lève
    le profil ``observer`` (le service verifier n'y existe pas).
  * Fail-fast : un override force ``restart: "no"`` (sinon ``unless-stopped`` boucle à l'infini) ;
    on lève amuled+crawler SANS profil (=> verifier ABSENT) ; le crawler download health-check le
    verifier au démarrage, échoue, et SE FIGE en ``exited`` avec un code != 0.
"""

import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.compose_integration

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SMOKE = _REPO_ROOT / "tests/smoke/compose.yaml"

# En CI, l'étape build pré-construit les images et passe IMAGE_TAG ; le smoke les consomme alors
# SANS rebuild. En local (IMAGE_TAG absent) on rebuild via compose, comme avant.
_IMAGE_TAG = os.environ.get("IMAGE_TAG")
_USES_PREBUILT = _IMAGE_TAG is not None
_BUILD_FLAGS: tuple[str, ...] = () if _USES_PREBUILT else ("--build",)

_ENTRY_POINTS = ("gluetun", "sans-vpn-lowid", "sans-vpn-highid")
_CONFIG_CASES: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (entry, profiles)
    for entry in _ENTRY_POINTS
    for profiles in (("observer",), ("download",), ("download", "monitoring"))
)
_CONFIG_ENV = {
    "WIREGUARD_PRIVATE_KEY": "x",
    "AMULE_EC_PASSWORD": "x",
    "GRAFANA_PWD": "x",
    "SERVER_COUNTRIES": "",
    "LISTEN_PORT": "4662",
    "LISTEN_PORT_UDP": "4672",
}

# Projet isolé (préfixe unique par run) pour ne JAMAIS toucher une stack réelle de l'hôte.
_PROJECT = f"emule_smoke_{uuid.uuid4().hex[:8]}"

# gluetun est désactivé dans le smoke, mais compose interpole ses variables au PARSE : on les
# stube pour que `config`/`build`/`up` n'échouent pas sur des variables manquantes.
_ENV_STUB = {
    "WIREGUARD_PRIVATE_KEY": "smoke-unused",
    "AMULE_EC_PASSWORD": "smoke-unused",
    "SERVER_COUNTRIES": "",
}

# Listes de volumes des overrides : on monte les configs smoke + les VRAIS volumes nommés
# (catalog-db/local-db/quarantine). Le crawler non-root (uid 999) y crée ses bases SQLite —
# le Dockerfile possède les points de montage en nonroot pour que les volumes vides héritent
# de 999:999. Les chemins de bind restent relatifs au project-directory (fixé à _REPO_ROOT).
_DOWNLOAD_LOCAL_VOLUMES = [
    "./tests/smoke/crawler.yml:/app/config/crawler.yml:ro",
    "./tests/smoke/targets.yml:/app/config/targets.yml:ro",
    "./tests/smoke/matcher.yml:/app/config/matcher.yml:ro",
    "quarantine:/data/quarantine",
    "catalog-db:/data/catalog",
    "local-db:/data/local",
]
_OBSERVER_LOCAL_VOLUMES = [
    "./tests/smoke/crawler.observer.yml:/app/config/crawler.yml:ro",
    "./tests/smoke/targets.yml:/app/config/targets.yml:ro",
    "./tests/smoke/matcher.yml:/app/config/matcher.yml:ro",
    "quarantine:/data/quarantine",
    "catalog-db:/data/catalog",
    "local-db:/data/local",
]


def _write_override(tmp_path: Path, name: str, crawler_body: str) -> Path:
    """Écrit un fichier d'override de scénario (YAML) sous tmp_path et renvoie son chemin."""
    path = tmp_path / name
    path.write_text(crawler_body)
    return path


def _run(*args: str, files: tuple[Path, ...], timeout: float) -> subprocess.CompletedProcess[str]:
    """Lance `docker compose -p <projet> -f ... <args>` depuis le repo root (cwd)."""
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
    """Tear-down idempotent : retire conteneurs + volumes + orphelins du projet.

    ``--profile download`` est OBLIGATOIRE : sans profil actif, ``down`` ignore les services
    profile-gated (compose v5) et laisserait tourner un verifier d'un scénario précédent
    (le service verifier n'est défini que dans le profil ``download``). Le profil ``download`` est
    un sur-ensemble (amuled+crawler+verifier), donc il nettoie aussi les scénarios
    observer/fail-fast.
    """
    _run("--profile", "download", "down", "-v", "--remove-orphans", files=files, timeout=180)


def _service_state(service: str, files: tuple[Path, ...]) -> tuple[str, int]:
    """(State, ExitCode) du service via `ps -a --format json` (un objet JSON par ligne)."""
    result = _run("ps", "-a", "--format", "json", service, files=files, timeout=60)
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("Service") == service:
            return str(obj.get("State")), int(obj.get("ExitCode"))
    raise AssertionError(f"service {service!r} introuvable dans `ps` : {result.stdout!r}")


def _wait_state(
    service: str, target: str, files: tuple[Path, ...], *, attempts: int = 30, delay: float = 2.0
) -> tuple[str, int]:
    """Boucle jusqu'à ce que `service` atteigne `target` (ou échec après attempts)."""
    last: tuple[str, int] = ("<absent>", -1)
    for _ in range(attempts):
        last = _service_state(service, files)
        if last[0] == target:
            return last
        time.sleep(delay)
    raise AssertionError(f"{service} n'a pas atteint {target!r} (dernier état : {last})")


@pytest.fixture
def project_files() -> Iterator[tuple[Path, ...]]:
    """Fichier compose smoke autonome + tear-down encadrant."""
    base = (_SMOKE,)
    _down(base)
    try:
        yield base
    finally:
        _down(base)


@pytest.mark.skipif(_USES_PREBUILT, reason="images pré-buildées en CI — rien à builder")
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

    # depends_on: service_healthy => le verifier est déjà sain quand le crawler démarre.
    assert _service_state("verifier", files)[0] == "running"
    assert _wait_state("crawler", "running", files)[0] == "running"

    # /health via exec dans le verifier (le réseau verify-internal est interne, sans Internet).
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

    # Le profil observer ne définit PAS le verifier ; le crawler démarre quand même et reste Up.
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
    # On lève UNIQUEMENT amuled + crawler (sans `--profile download`) => le verifier est ABSENT.
    # Config download (verifier_url présent) => le crawler health-check le verifier au démarrage,
    # échoue, et avec restart: "no" SE FIGE en exited (pas de boucle de redémarrage).
    result = _run("up", "-d", *_BUILD_FLAGS, "amuled", "crawler", files=files, timeout=900)
    assert result.returncode == 0, result.stderr

    state, exit_code = _wait_state("crawler", "exited", files)
    assert state == "exited"
    assert exit_code != 0


@pytest.mark.parametrize("entry,profiles", _CONFIG_CASES)
def test_entrypoint_config_renders(entry: str, profiles: tuple[str, ...]) -> None:
    """`docker compose -f deploy/examples/<entry>.yaml --profile … config` rend sans erreur.

    Verrouille include + forward-refs + ancres/merge + interpolation (pas de daemon requis ;
    les sources de bind-mount n'ont pas besoin d'exister pour `config`).
    """
    profile_flags: list[str] = []
    for profile in profiles:
        profile_flags += ["--profile", profile]
    command = [
        "docker",
        "compose",
        "-f",
        f"deploy/examples/{entry}.yaml",
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


def _yaml_crawler(
    *,
    depends_on: str | None,
    volumes: list[str],
    restart_no: bool = False,
) -> str:
    """Compose un override `services.crawler` (volumes !override ; tmpfs /tmp hérité de la base)."""
    lines = ["services:", "  crawler:"]
    if restart_no:
        lines.append('    restart: !override "no"')
    if depends_on is not None:
        lines.append(depends_on.rstrip("\n"))
    lines.append("    volumes: !override")
    lines += [f"      - {volume}" for volume in volumes]
    return "\n".join(lines) + "\n"
