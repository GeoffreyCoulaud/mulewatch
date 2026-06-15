"""Notifier apprise : route une notification par AUDIENCE via les tags apprise (E-D7).

Couche ADAPTER (implémente ``Notifier``). Au montage : ``add(url, tag=audience)`` pour chaque
cible. ``notify`` PRÉFIXE le corps du ``node_id`` (ID d'instance — indispensable côté COMMUNITY,
réseau distribué) et appelle ``async_notify(body, notify_type, tag)``. Aucune URL → no-op naturel
(apprise sans service rend ``None``). ``apprise_obj`` injectable pour le test (défaut : vrai
``apprise.Apprise``). Le timeout/l'absorption d'erreur sont dans le dispatcher (E-D13).

Pas de stubs apprise → ``# type: ignore`` ciblés (override mypy, Task 9)."""

from collections.abc import Sequence

import apprise

from emule_indexer.domain.observability.policy import Audience, Severity

# tuple (url, audience) — la config (Task 7) produit ces paires depuis ``local.yaml``.
NotificationTargets = Sequence[tuple[str, Audience]]

_NOTIFY_TYPES: dict[Severity, object] = {
    Severity.DEBUG: apprise.NotifyType.INFO,
    Severity.INFO: apprise.NotifyType.INFO,
    Severity.WARNING: apprise.NotifyType.WARNING,
    Severity.ERROR: apprise.NotifyType.FAILURE,
}


class AppriseNotifier:
    """Adapter ``Notifier`` : un canal apprise par audience (tag), corps préfixé du node_id."""

    def __init__(
        self,
        targets: NotificationTargets,
        *,
        node_id: str,
        apprise_obj: object | None = None,
    ) -> None:
        # Typé ``object`` à dessein : l'adapter ne dépend pas de la surface (non typée)
        # d'apprise ; ``.add``/``.async_notify`` portent un ``# type: ignore[attr-defined]``.
        self._apprise: object = apprise.Apprise() if apprise_obj is None else apprise_obj
        for url, audience in targets:
            self._apprise.add(url, tag=audience.value)  # type: ignore[attr-defined]
        self._node_id = node_id

    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        await self._apprise.async_notify(  # type: ignore[attr-defined]
            body=f"[{self._node_id}] {body}",
            notify_type=_NOTIFY_TYPES[severity],
            tag=audience.value,
        )
