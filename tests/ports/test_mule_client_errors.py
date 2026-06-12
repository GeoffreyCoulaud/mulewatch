from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcFailureError,
    EcProtocolError,
    EcTimeoutError,
)
from emule_indexer.ports.mule_client import (
    MuleClientError,
    MuleSearchFailedError,
    MuleUnreachableError,
)


def test_unreachable_and_search_failed_are_mule_client_errors() -> None:
    assert issubclass(MuleUnreachableError, MuleClientError)
    assert issubclass(MuleSearchFailedError, MuleClientError)


def test_transport_failures_are_unreachable() -> None:
    for cls in (EcConnectError, EcTimeoutError, EcProtocolError):
        assert issubclass(cls, MuleUnreachableError)


def test_application_failure_is_search_failed_not_unreachable() -> None:
    assert issubclass(EcFailureError, MuleSearchFailedError)
    assert not issubclass(EcFailureError, MuleUnreachableError)


def test_auth_error_is_not_a_loop_error() -> None:
    # L'échec d'auth est un problème de config (fail-fast au démarrage), pas un cas de boucle.
    assert issubclass(EcAuthError, MuleClientError)
    assert not issubclass(EcAuthError, MuleUnreachableError)
    assert not issubclass(EcAuthError, MuleSearchFailedError)
