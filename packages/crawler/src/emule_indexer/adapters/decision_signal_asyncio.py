"""Adapter réel du hub de nudge : un ``asyncio.Event`` par sujet (spec orchestration §3/§4).

Implémente STRUCTURELLEMENT le port ``DecisionSignal``. ``signal(subject)`` réveille tout
``wait(subject)`` en cours puis re-arme l'événement (``set`` suivi du ``clear`` par le
waiter réveillé) : un consommateur qui dort sur le sujet repart immédiatement, puis se
rendort sur le prochain nudge. Un ``signal`` SANS waiter est inoffensif (l'événement reste
armé jusqu'au prochain ``wait``, qui le consomme aussitôt) — cohérent avec « un nudge perdu
est inoffensif, le polling de repli est le filet » (spec §3).

Mono-thread/event-loop : tous les accès passent par l'event loop (les repos sont appelés
sync sur cette même boucle, aucune course). Pas de verrou nécessaire.
"""

import asyncio


class AsyncioDecisionSignal:
    """Hub de nudge in-process (un ``asyncio.Event`` par sujet, créé à la demande)."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def _event(self, subject: str) -> asyncio.Event:
        return self._events.setdefault(subject, asyncio.Event())

    def signal(self, subject: str) -> None:
        """Réveille les ``wait`` du sujet (synchrone : appelé post-commit, ne bloque pas)."""
        self._event(subject).set()

    async def wait(self, subject: str) -> None:
        """Dort jusqu'au prochain ``signal`` du sujet, puis re-arme pour le suivant."""
        event = self._event(subject)
        await event.wait()
        event.clear()
