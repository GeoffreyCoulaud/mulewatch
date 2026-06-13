"""Port ``DecisionSignal`` : le hub de nudge in-process (spec orchestration §3).

Couplage par la donnée + nudge (spec §3) : la boucle persiste la décision (append-only =
fiabilité gratuite, un consommateur absent rejoue depuis la table) PUIS ``signal``e le hub
pour réveiller un consommateur in-process immédiatement. Le polling de repli reste le
filet ; un nudge perdu est inoffensif. Le « sujet » est l'identité de ce qui a changé (en
plan C : le ``ed2k_hash`` dont le verdict a changé) — un consommateur futur (plan D/E)
``await wait(subject)``.

Protocol ASYNC. ``signal`` est synchrone (appelé depuis le pipeline sync post-commit, ne
doit jamais bloquer) ; ``wait`` est async (le consommateur s'endort dessus). Implémenté
côté adapter par un ``asyncio.Event`` par sujet (``adapters/decision_signal_asyncio.py``).
"""

from typing import Protocol


class DecisionSignal(Protocol):
    """Hub de réveil in-process (spec §3). ``signal`` réveille tout ``wait`` du même sujet."""

    def signal(self, subject: str) -> None: ...

    async def wait(self, subject: str) -> None: ...
