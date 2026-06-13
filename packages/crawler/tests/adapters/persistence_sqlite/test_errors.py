from emule_indexer.adapters.persistence_sqlite.errors import MigrationError, PersistenceError
from emule_indexer.ports.repository_errors import RepositoryError


def test_persistence_error_satisfies_repository_error_contract() -> None:
    assert issubclass(PersistenceError, RepositoryError)
    assert issubclass(MigrationError, RepositoryError)  # transitive via PersistenceError
