"""Ordre de parcours d'un cycle, seedé par nœud (PUR, spec orchestration §3/§4).

Domaine PUR : aucune I/O, aucune horloge, aucun ``random`` GLOBAL. Le shuffle est confié
à un port ``Rng`` injecté (``ports/clock.py``) pour rester déterministe et testable ; ce
module ne fait QUE construire le SEED (``node_id`` + index de cycle) et appliquer le
mélange. Propriété recherchée (spec MVP §6) : deux nœuds DIFFÉRENTS divergent (angles
morts temporels supprimés), un même nœud au même cycle REJOUE le même ordre.
"""

from collections.abc import Sequence
from typing import Protocol


class Rng(Protocol):
    """Port du hasard injectable. ``shuffled`` rend une PERMUTATION de ``items`` dérivée
    UNIQUEMENT du ``seed`` (même seed → même ordre). ``jitter`` rend un flottant dans
    ``[0, span)`` (anti-thundering-herd du backoff, spec §3 « + jitter »). Implémenté côté
    adapter par ``random.Random`` (``adapters/clock_asyncio.py``) ; remplacé en test par un
    faux DÉTERMINISTE (zéro flakiness)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]: ...

    def jitter(self, span: float) -> float: ...


def cycle_seed(node_id: str, cycle_index: int) -> str:
    """Seed du cycle : ``node_id`` + index, séparés par ``:`` (spec §3).

    Le ``node_id`` fait diverger les nœuds ; l'``cycle_index`` fait varier l'ordre d'un
    cycle au suivant SUR le même nœud (sinon l'ordre serait figé à vie).
    """
    return f"{node_id}:{cycle_index}"


def shuffle_for_cycle(
    items: Sequence[str], rng: Rng, node_id: str, cycle_index: int
) -> tuple[str, ...]:
    """Permutation déterministe de ``items`` pour ce ``(node_id, cycle_index)`` (spec §4).

    L'ordre d'entrée est sans importance pour le résultat (le seed le détermine
    entièrement) ; on passe par un tuple pour ne JAMAIS muter la séquence de l'appelant.
    """
    return rng.shuffled(tuple(items), cycle_seed(node_id, cycle_index))
