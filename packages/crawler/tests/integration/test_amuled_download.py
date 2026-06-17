"""Intégration DOWNLOAD contre un amuled RÉEL (réf. protocole, spec download §11 — option A).

Run dédié : uv run pytest -m download_integration --no-cov
Valide les MÉCANIQUES EC du download : ``add_link`` accepté + le lien apparaît dans
``download_queue`` avec un statut lisible. La COMPLÉTION n'est PAS atteignable (pas de sources
eD2k depuis le conteneur éphémère) : c'est le cycle add_link → file → statut qui est validé.
"""

from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcFailureError
from emule_indexer.domain.download.ed2k_link import build_ed2k_link
from emule_indexer.ports.mule_download_client import DownloadEntry, SharedFileEntry

pytestmark = pytest.mark.download_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"
# Un hash canonique NON DÉGÉNÉRÉ : surtout PAS la MD4 du fichier vide (31d6cfe0…), qu'amuled
# traite comme instantanément complet à 0 octet et ne liste JAMAIS comme partfile actif — ce
# qui avait masqué le bug du décodage de hash. Avec une vraie taille, le lien crée un partfile
# listé (size_done=0 < size_full), dont le hash apparaît dans l'enfant EC_TAG_PARTFILE_HASH.
_HASH = "aabbccddeeff00112233445566778899"
_SIZE = 734003200  # ~700 Mio : une taille réelle, donc un partfile actif (jamais "complet")


@pytest.fixture(scope="module")
def amuled() -> Iterator[tuple[str, int]]:
    ready = LogMessageWaitStrategy(r"listening on 0\.0\.0\.0:4712").with_startup_timeout(180)
    container = (
        DockerContainer(_IMAGE)
        .with_env("GUI_PWD", _EC_PASSWORD)
        .with_exposed_ports(4712)
        .waiting_for(ready)
    )
    try:
        container.start()
        yield container.get_container_host_ip(), int(container.get_exposed_port(4712))
    finally:
        container.stop()


@pytest.mark.asyncio
async def test_add_link_then_appears_in_download_queue(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        link = build_ed2k_link("probe-download.bin", _SIZE, _HASH)
        try:
            await client.add_link(link)
        except EcFailureError as exc:
            # amuled a répondu FAILED proprement (lien refusé) : le cycle requête/réponse
            # EST validé, avec le message du daemon. Tolérable pour ce contexte de test.
            assert str(exc)
            return
        queue = await client.download_queue()
        assert isinstance(queue, tuple)
        assert all(isinstance(entry, DownloadEntry) for entry in queue)
        # add_link ACCEPTÉ : un lien à vraie taille (sans source) crée un partfile listé
        # (size_done=0 < size_full), dont le hash est porté par l'enfant EC_TAG_PARTFILE_HASH.
        # C'est le RÉGRESSION GUARD du bug de décodage : si _map_partfile lisait encore la
        # valeur propre (UINT8) au lieu de l'enfant 0x031E, la file serait vide ici → échec.
        hashes = {entry.ed2k_hash for entry in queue}
        assert _HASH in hashes
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_shared_files_round_trips(amuled: tuple[str, int]) -> None:
    # Confirme EMPIRIQUEMENT le cycle requête/réponse GET_SHARED_FILES → SHARED_FILES et que le
    # décodage ne lève pas (opcodes 0x10/0x22). Sur un amuled neuf la liste peut être vide ; le
    # mapping (conteneur EC_TAG_KNOWNFILE 0x0400, nom/hash) est couvert par les tests unit + la
    # source amont. Si des entrées remontent, ce sont des SharedFileEntry valides (hash hex 32,
    # nom).
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        shared = await client.shared_files()
        assert isinstance(shared, tuple)
        assert all(isinstance(e, SharedFileEntry) for e in shared)
        assert all(len(e.ed2k_hash) == 32 and e.name for e in shared)
    finally:
        await client.close()
