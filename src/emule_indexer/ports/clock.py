"""Ports ``Clock`` et ``Rng`` : le temps et le hasard, injectables (spec orchestration §3).

Déterminisme TOTAL (spec §3) : l'application ne lit jamais l'horloge système ni un
``random`` global directement — elle passe par ces ports, que les tests remplacent par des
fausses implémentations avançables/seedées (zéro flakiness, tout cycle rejouable).

``Clock`` porte un ``now()`` AWARE (UTC) ET un ``sleep`` ASYNC (le cycle dort entre deux
itérations) : les deux faces du temps dont l'orchestration a besoin. Le ``sleep`` est sur
le port pour qu'un faux puisse l'avancer SANS attente réelle.

``Rng`` est le mélangeur déterministe consommé par ``domain/search/cycle.py`` ; il est
RÉ-EXPORTÉ ici depuis le domaine (la définition canonique du Protocol vit dans le domaine,
là où il est consommé — règle de dépendance : le domaine n'importe jamais un port). Ce
ré-export donne aux adapters/composition un point d'import unique « les ports du temps ».
"""

from datetime import datetime
from typing import Protocol

from emule_indexer.domain.search.cycle import Rng

__all__ = ["Clock", "Rng"]


class Clock(Protocol):
    """Le temps, injectable : ``now()`` aware (UTC) + ``sleep`` async (spec §3).

    Implémenté côté adapter par ``datetime.now(UTC)`` + ``asyncio.sleep`` ; remplacé en
    test par une fausse horloge avançable (le ``sleep`` avance le ``now`` sans attente).
    """

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...
