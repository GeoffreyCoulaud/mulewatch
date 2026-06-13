"""Contrat d'erreur des repositories (spec orchestration §4/§7).

Couche PORTS : le CONTRAT d'erreur que l'application catch (« une ``RepositoryError`` sur
une obs est loggée, le cycle continue », spec §7) vit au niveau du port, JAMAIS d'un
adapter — sinon l'application dépendrait d'un adapter (règle de dépendance §4). L'adapter
SQLite fait hériter sa ``PersistenceError`` de ``RepositoryError`` (dépendance adapter→port,
licite). L'application ne connaît que ``RepositoryError``.
"""


class RepositoryError(Exception):
    """Échec de persistance signalé par un repository (l'adapter signale, il ne décide pas)."""
