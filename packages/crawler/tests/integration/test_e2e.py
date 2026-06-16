"""Suite e2e (spec e2e) : download → quarantaine → verify RÉEL de bout en bout.

Run dédié : ( cd packages/crawler && uv run pytest -m e2e_integration --no-cov )
Docker + docker compose v2 requis ; LANCÉ PAR GEOFFREY (le sandbox n'a pas de réseau Docker
complet — mémoire « integration-tests-need-real-shell »). L'agent ÉCRIT ce test, ne le lance pas.

Contrairement au smoke (`compose_integration`, qui ne valide que le câblage sans aucun octet
transféré), cette suite assemble un VRAI serveur eD2k (ed2kd vendoré) + un amuled seeder qui
partage le fichier planté en HighID + l'amuled leecher du crawler. Le crawler observe, décide
`download`, télécharge LES OCTETS RÉELS, et au partfile complété déclenche `resolve_staging_path`
(DV10, JAMAIS exercé ailleurs) → `os.replace` depuis le vrai staging amuled → quarantaine par
hash → le verifier analyse → verdict `clean` + `real_meta` non vide.

Ce module N'IMPORTE AUCUN module `emule_indexer` (il pilote la stack par subprocess docker
compose, exactement comme `test_compose_smoke.py`) → le 100 % branch du paquet est préservé.

Sous-test port-sync (spec e2e §5.5) : skippable indépendamment (la boucle port-sync peut ne pas
encore être intégrée). Quand elle l'est : stub `/v1/portforward` → SetPort(N) + restart amuled →
HighID observé ⇔ port-sync correct. Le download → verify réel reste la valeur centrale.
"""

import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e_integration

_REPO_ROOT = Path(__file__).resolve().parents[4]
_COMPOSE = _REPO_ROOT / "compose.yaml"
_E2E = _REPO_ROOT / "compose.e2e.yaml"
_PLANTED_ED2K_HASH = "7d3ce5e6b6243999b4fed38bb7ae1c05"
_PLANTED_TARGET_ID = "S2E062A"

# Projet isolé (préfixe unique par run) pour ne JAMAIS toucher une stack réelle de l'hôte.
_PROJECT = f"emule_e2e_{uuid.uuid4().hex[:8]}"

# La base compose interpole des variables gluetun au PARSE (même désactivé) : on les stube.
_ENV_STUB = {
    "WIREGUARD_PRIVATE_KEY": "e2e-unused",
    "AMULE_EC_PASSWORD": "e2e-ec-password",
    "SERVER_COUNTRIES": "",
}


def _run(*args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """Lance `docker compose -p <projet> -f compose.yaml -f compose.e2e.yaml <args>` (cwd=repo)."""
    command = [
        "docker",
        "compose",
        "-p",
        _PROJECT,
        "-f",
        str(_COMPOSE),
        "-f",
        str(_E2E),
        *args,
    ]
    return subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **_ENV_STUB},
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _down() -> None:
    """Tear-down idempotent : retire conteneurs + volumes + orphelins du projet e2e."""
    _run("--profile", "e2e", "down", "-v", "--remove-orphans", timeout=180)


def _service_state(service: str) -> tuple[str, int]:
    """(State, ExitCode) du service via `ps -a --format json` (un objet JSON par ligne)."""
    result = _run("ps", "-a", "--format", "json", service, timeout=60)
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("Service") == service:
            return str(obj.get("State")), int(obj.get("ExitCode"))
    raise AssertionError(f"service {service!r} introuvable dans `ps` : {result.stdout!r}")


def _wait_state(
    service: str, target: str, *, attempts: int = 30, delay: float = 2.0
) -> tuple[str, int]:
    """Boucle jusqu'à ce que `service` atteigne `target` (ou échec après attempts)."""
    last: tuple[str, int] = ("<absent>", -1)
    for _ in range(attempts):
        last = _service_state(service)
        if last[0] == target:
            return last
        time.sleep(delay)
    raise AssertionError(f"{service} n'a pas atteint {target!r} (dernier état : {last})")


def _exec_python(
    service: str, snippet: str, *, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    """Exécute un snippet Python DANS un conteneur (stdlib seule ; pas d'I/O réseau)."""
    return _run("exec", "-T", service, "python", "-c", snippet, timeout=timeout)


def _query_verification() -> tuple[str, str] | None:
    """(verdict, real_meta) de la DERNIÈRE vérification du hash planté, via le crawler (sqlite3).

    Le crawler image a Python + sqlite3 (stdlib) et monte catalog.db en RW ; on l'interroge sans
    octet réseau. Renvoie None si aucune ligne `file_verifications` n'existe encore pour le hash.
    """
    snippet = (
        "import sqlite3, json;"
        "c = sqlite3.connect('/data/catalog/catalog.db');"
        "r = c.execute("
        "  'SELECT verdict, real_meta FROM file_verifications "
        f"   WHERE ed2k_hash = ? ORDER BY id DESC LIMIT 1', ('{_PLANTED_ED2K_HASH}',)"
        ").fetchone();"
        "print(json.dumps(r) if r else json.dumps(None))"
    )
    result = _exec_python("crawler", snippet)
    if result.returncode != 0:
        raise AssertionError(f"requête vérification échouée : {result.stderr!r}")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload is None:
        return None
    return str(payload[0]), str(payload[1])


def _wait_verification(*, attempts: int = 60, delay: float = 5.0) -> tuple[str, str]:
    """Boucle jusqu'à l'apparition d'une vérification du hash planté (download → verify abouti)."""
    last: tuple[str, str] | None = None
    for _ in range(attempts):
        last = _query_verification()
        if last is not None:
            return last
        time.sleep(delay)
    raise AssertionError(f"aucune vérification du hash planté après {attempts} essais ({last})")


def _network_status() -> str:
    """État réseau EC du leecher (HighID/LowID) via un exec EC probe dans le crawler.

    Utilise l'outil interne `download_probe` (présent dans l'image crawler) pour lire le statut
    réseau de l'amuled leecher — ne transfère pas d'octet de contenu. Renvoie une chaîne lisible.
    """
    snippet = (
        "import asyncio\n"
        "from emule_indexer.adapters.mule_ec.client import AmuleEcClient\n"
        "async def m():\n"
        " c = AmuleEcClient('amuled', 4712, 'e2e-ec-password')\n"
        " await c.connect()\n"
        " s = await c.network_status()\n"
        " print('HighID' if s.ed2k_high else 'LowID')\n"
        " await c.close()\n"
        "asyncio.run(m())"
    )
    result = _exec_python("crawler", snippet)
    if result.returncode != 0:
        raise AssertionError(f"lecture network_status échouée : {result.stderr!r}")
    return result.stdout.strip().splitlines()[-1]


@pytest.fixture
def e2e_stack() -> Iterator[None]:
    """Tear-down encadrant : `down -v` avant (résidus) et après (nettoyage) chaque test."""
    _down()
    try:
        yield None
    finally:
        _down()


def test_build_succeeds(e2e_stack: None) -> None:
    """Les images e2e (ed2kd vendoré + crawler + verifier) se construisent (R2 : build ed2kd)."""
    result = _run("--profile", "e2e", "build", timeout=1800)
    assert result.returncode == 0, result.stderr


def test_download_verify_real_end_to_end(e2e_stack: None) -> None:
    """Couche B : download → quarantaine (DV10) → verdict `clean` + `real_meta` non vide.

    C'est le cœur du dérisquage : la PREMIÈRE et SEULE exécution réelle de `resolve_staging_path`
    (DV10). On laisse la stack tourner jusqu'à ce que le partfile complète, que le crawler fasse
    `os.replace` du vrai staging amuled vers la quarantaine par hash, et que le verifier analyse.
    """
    result = _run("--profile", "e2e", "up", "-d", "--build", timeout=1800)
    assert result.returncode == 0, result.stderr

    # Le verifier devient sain, le crawler reste Up (mode full : il a fail-fast si verifier absent).
    assert _wait_state("verifier", "running")[0] == "running"
    assert _wait_state("crawler", "running")[0] == "running"

    # download → verify abouti : une vérification du hash planté apparaît avec verdict clean.
    verdict, real_meta = _wait_verification()
    assert verdict == "clean", f"verdict inattendu : {verdict!r}"

    # real_meta non vide : ffprobe a lu le média (durée/codec/conteneur) → DV10 a réellement
    # promu le fichier complet en quarantaine (sinon le verifier n'aurait rien à analyser).
    assert real_meta and real_meta not in ("null", "{}"), f"real_meta vide : {real_meta!r}"
    meta = json.loads(real_meta)
    assert meta, "real_meta JSON vide — ffprobe n'a pas lu le média"

    # Le fichier est bien apparu en quarantaine PAR HASH (cible du os.replace de DV10).
    listing = _exec_python(
        "crawler",
        "import os; print('\\n'.join(os.listdir('/data/quarantine')))",
    )
    assert listing.returncode == 0, listing.stderr
    assert _PLANTED_ED2K_HASH in listing.stdout, (
        f"hash planté absent de la quarantaine : {listing.stdout!r}"
    )


def test_decision_is_download_for_planted_target(e2e_stack: None) -> None:
    """Le crawler observe le résultat planté et décide `download` sur la cible S2E062A.

    Valide le chemin observation → décision (le moteur de matching sur le nom planté), prérequis
    du download. La décision est persistée dans match_decisions (catalog.db).
    """
    result = _run("--profile", "e2e", "up", "-d", "--build", timeout=1800)
    assert result.returncode == 0, result.stderr
    assert _wait_state("crawler", "running")[0] == "running"

    snippet = (
        "import sqlite3, json;"
        "c = sqlite3.connect('/data/catalog/catalog.db');"
        "r = c.execute("
        "  'SELECT target_id, tier FROM match_decisions "
        f"   WHERE ed2k_hash = ? ORDER BY id DESC LIMIT 1', ('{_PLANTED_ED2K_HASH}',)"
        ").fetchone();"
        "print(json.dumps(r) if r else json.dumps(None))"
    )

    decision: list[str] | None = None
    for _ in range(60):
        out = _exec_python("crawler", snippet)
        assert out.returncode == 0, out.stderr
        decision = json.loads(out.stdout.strip().splitlines()[-1])
        if decision is not None:
            break
        time.sleep(5)
    assert decision is not None, "aucune décision de match sur le hash planté"
    assert decision[0] == _PLANTED_TARGET_ID
    assert decision[1] == "download"


@pytest.mark.skipif(
    os.environ.get("E2E_PORTSYNC") != "1",
    reason="sous-test port-sync : nécessite la boucle port-sync intégrée (E2E_PORTSYNC=1)",
)
def test_portsync_highid_after_setport(e2e_stack: None) -> None:
    """Sous-test port-sync (spec e2e §5.5, skippable) : SetPort(N) + restart → HighID observé.

    Le stub `/v1/portforward` annonce N ; la boucle port-sync fait EC SetPort(N) + restart amuled ;
    après le restart, ed2kd port-checke amuled sur N et accorde le HighID SSI amuled écoute ET est
    joignable sur N. Donc HighID observé ⇔ port-sync correct. Sans la boucle intégrée, ce test est
    skippé (le download → verify réel ci-dessus reste la valeur centrale).
    """
    result = _run("--profile", "e2e", "up", "-d", "--build", timeout=1800)
    assert result.returncode == 0, result.stderr
    assert _wait_state("crawler", "running")[0] == "running"

    # Laisser la boucle port-sync lire N + SetPort + restart, puis observer le HighID.
    status = ""
    for _ in range(30):
        status = _network_status()
        if status == "HighID":
            break
        time.sleep(5)
    assert status == "HighID", f"HighID non observé après port-sync : {status!r}"
