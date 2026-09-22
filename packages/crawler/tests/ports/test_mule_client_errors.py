from mulewatch.adapters.mule_api.errors import (
    ApiAuthError,
    ApiRejectedError,
    ApiUnreachableError,
)
from mulewatch.ports.mule_client import (
    MuleClientError,
    MuleSearchFailedError,
    MuleUnreachableError,
)


def test_unreachable_and_search_failed_are_mule_client_errors() -> None:
    assert issubclass(MuleUnreachableError, MuleClientError)
    assert issubclass(MuleSearchFailedError, MuleClientError)


def test_transport_failures_are_unreachable() -> None:
    assert issubclass(ApiUnreachableError, MuleUnreachableError)


def test_application_failure_is_search_failed_not_unreachable() -> None:
    assert issubclass(ApiRejectedError, MuleSearchFailedError)
    assert not issubclass(ApiRejectedError, MuleUnreachableError)


def test_auth_error_is_not_a_loop_error() -> None:
    # An auth failure is a config problem (fail-fast at startup), not a loop case.
    assert issubclass(ApiAuthError, MuleClientError)
    assert not issubclass(ApiAuthError, MuleUnreachableError)
    assert not issubclass(ApiAuthError, MuleSearchFailedError)
