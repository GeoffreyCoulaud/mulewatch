"""Hiérarchie d'erreurs de l'adapter EC (cf. spec EC-adapter §6 ; orchestration §7).

L'adapter SIGNALE, il ne décide pas : pas de retry caché, pas de crash silencieux. Cette
hiérarchie permet à l'appelant (plan C) de distinguer « amuled est down » (EcConnectError)
de « ma config est fausse » (EcAuthError), une trame illisible (EcProtocolError) d'un échec
applicatif proprement signalé par le daemon (EcFailureError).

Le CONTRAT d'erreur consommé par l'application vit dans le PORT (``ports/mule_client.py`` :
``MuleUnreachableError``/``MuleSearchFailedError``) ; les classes EC ci-dessous en HÉRITENT
(dépendance adapter→port, licite) pour que l'application ne dépende JAMAIS de cet adapter
(règle de dépendance, spec orchestration §4). Le mapping : flux mort (connexion/timeout/
trame illisible) → ``MuleUnreachableError`` ; ``EC_OP_FAILED`` → ``MuleSearchFailedError`` ;
l'échec d'AUTH reste hors contrat de boucle (problème de config, fail-fast au démarrage).
"""

from emule_indexer.ports.mule_client import (
    MuleClientError,
    MuleSearchFailedError,
    MuleUnreachableError,
)


class EcError(MuleClientError):
    """Base de toutes les erreurs de l'adapter EC (sous le contrat de port)."""


class EcConnectError(EcError, MuleUnreachableError):
    """TCP refusé, connexion perdue, ou opération sans connexion établie."""


class EcAuthError(EcError):
    """Authentification refusée (mot de passe ou version de protocole) — pas un cas de boucle."""


class EcProtocolError(EcError, MuleUnreachableError):
    """Trame malformée ou réponse inattendue (l'entrée réseau est non fiable) → flux mort."""


class EcTimeoutError(EcError, MuleUnreachableError):
    """Délai dépassé (lecture réseau ou établissement de connexion) → flux mort."""


class EcFailureError(EcError, MuleSearchFailedError):
    """Échec applicatif signalé par le daemon (EC_OP_FAILED) ; porte son message."""
