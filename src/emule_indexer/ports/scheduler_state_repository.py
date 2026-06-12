"""Port ``SchedulerStateRepository`` : l'état durable de l'ordonnanceur (spec §4/§7).

Couche PORTS, Protocol SYNCHRONE (même principe que ``LocalStateRepository`` : sub-ms,
pas de ``to_thread`` en MVP). Persiste ce que la reprise après crash relit (spec §7) :
l'INDEX de cycle (n'avance qu'en FIN de cycle → un kill au milieu rejoue les mots-clés
restants), l'horodatage du dernier cycle complet, ET le BACKOFF par (instance, canal)
(spec §3/§7 : il doit survivre à un redémarrage). Tout est stocké en KV dans la table
``scheduler_state`` de ``local.db`` (jamais fusionné, invariant §11).

Le backoff est sérialisé en JSON sous UNE clé (``channel_backoff``) : une map
``{ "amule-1:kad": {attempts, retry_after}, "amule-1": {...} }`` — la clé est soit
``instance:canal`` (échec d'un canal), soit ``instance`` seule (reconnexion). ``retry_after``
est un ISO-8601 UTC à largeur fixe (comparaison lexicographique == chronologique).
``read_cycle_index`` rend ``0`` si jamais écrit (premier démarrage) ; ``load_channel_backoff``
rend un dict vide.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ChannelBackoff:
    """État de backoff d'une clé (instance, ou instance:canal) : compteur + échéance.

    ``attempts`` = nombre d'échecs CONSÉCUTIFS (sert au calcul exponentiel). ``retry_after``
    = ISO-8601 UTC à largeur fixe : tant que ``now < retry_after``, la clé est SAUTÉE. Gelé
    et JSON-friendly (deux champs scalaires) → sérialisation triviale.
    """

    attempts: int
    retry_after: str


class SchedulerStateRepository(Protocol):
    """Contrat sync de l'état d'ordonnancement (index de cycle + dernier cycle + backoff).

    ``write_cycle_state`` reçoit un ``datetime`` AWARE (l'application passe ``clock.now()``,
    qui ne dépend d'aucun adapter) ; le formatage ISO-8601 est interne à l'adapter SQLite.
    ``save_channel_backoff`` remplace ENTIÈREMENT la map persistée (snapshot du registre).
    """

    def read_cycle_index(self) -> int: ...

    def write_cycle_state(self, cycle_index: int, last_full_cycle_at: datetime) -> None: ...

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]: ...

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None: ...
