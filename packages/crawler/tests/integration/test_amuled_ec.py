"""Intégration contre un amuled RÉEL (image ngosang/amule, réf. protocole §8).

Run dédié : uv run pytest -m ec_integration --no-cov
Valide : auth réelle (formule du hash §4 contre le vrai daemon), échec d'auth, statut
réseau, et le CYCLE complet start/fetch/stop — les résultats peuvent être vides sans
accès réseau eD2k : c'est le cycle qui est validé (spec §7.3), la richesse des champs
réels vient du probe (rapport livrable 5).
"""

from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcAuthError, EcFailureError
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel

pytestmark = pytest.mark.ec_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"  # DÉCISION 10 : image Docker Hub du dépôt ngosang/docker-amule


@pytest.fixture(scope="module")
def amuled() -> Iterator[tuple[str, int]]:
    # Readiness (réf. §8) : « *** TCP socket (ECServer) listening on 0.0.0.0:4712 »
    # (ExternalConn.cpp:333). Motif regex SANS les parenthèses littérales. La stratégie
    # détecte aussi un conteneur mort avant la ligne attendue (RuntimeError immédiate).
    ready = LogMessageWaitStrategy(r"listening on 0\.0\.0\.0:4712").with_startup_timeout(180)
    container = (
        DockerContainer(_IMAGE)
        .with_env("GUI_PWD", _EC_PASSWORD)
        .with_exposed_ports(4712)
        .waiting_for(ready)
    )
    try:
        container.start()  # la readiness est attendue PENDANT start() (stratégie ci-dessus)
        yield container.get_container_host_ip(), int(container.get_exposed_port(4712))
    finally:
        container.stop()  # no-op sûr si le conteneur n'a jamais été créé


@pytest.mark.asyncio
async def test_real_auth_succeeds(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()  # formule du hash §4 validée contre le VRAI daemon
    await client.close()


@pytest.mark.asyncio
async def test_real_auth_fails_with_wrong_password(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, "mauvais-mot-de-passe", timeout=30.0)
    with pytest.raises(EcAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_real_network_status(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        status = await client.network_status()
        assert isinstance(status, NetworkStatus)
        assert status.kad_status in set(KadStatus)  # état réel quelconque, mais DÉCODÉ
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_search_cycle(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        try:
            await client.start_search("keroro", SearchChannel.GLOBAL)
        except EcFailureError as exc:
            # amuled a répondu EC_OP_FAILED proprement (pas de serveur eD2k joignable
            # depuis le conteneur) : le cycle requête/réponse applicatif EST validé,
            # avec le message du daemon transmis (spec §6, DÉCISION 10).
            assert str(exc)
            return
        progress = await client.search_progress()
        assert progress is None or 0 <= progress <= 100
        results = await client.fetch_results()  # possiblement vide : le CYCLE compte
        assert isinstance(results, tuple)
        await client.stop_search()
    finally:
        await client.close()
