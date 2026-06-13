"""Hiérarchie d'erreurs de l'adapter persistence (spec data-model §7 ; orchestration §7).

L'adapter SIGNALE, il ne décide pas (même philosophie que l'adapter EC) : toute
``sqlite3.Error`` inattendue sort enveloppée en ``PersistenceError``, jamais nue.
Un trigger append-only qui se déclenche est un BUG du code appelant, pas un cas
métier → la même ``PersistenceError``. ``wrap_sqlite_errors`` est l'enveloppe
UNIQUE partagée par la connexion et les deux repositories (cause chaînée gardée).

``PersistenceError`` HÉRITE du contrat de port ``RepositoryError`` (``ports/
repository_errors.py``) : l'application catch ``RepositoryError`` (spec orchestration §7,
« une obs en échec est loggée, le cycle continue »), jamais cette classe d'adapter — règle
de dépendance §4. Dépendance adapter→port, licite.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from emule_indexer.ports.repository_errors import RepositoryError


class PersistenceError(RepositoryError):
    """Base de toutes les erreurs de l'adapter persistence (sous le contrat de port)."""


class MigrationError(PersistenceError):
    """Base plus récente que le code, ou script qui échoue (fail-fast, spec MVP §14)."""


@contextmanager
def wrap_sqlite_errors() -> Iterator[None]:
    """Enveloppe toute ``sqlite3.Error`` en ``PersistenceError`` (cause chaînée)."""
    try:
        yield
    except sqlite3.Error as error:
        raise PersistenceError(str(error)) from error
