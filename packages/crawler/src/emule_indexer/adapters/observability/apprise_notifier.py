"""Apprise notifier: routes a notification per AUDIENCE via apprise tags (E-D7).

ADAPTER layer (implements ``Notifier``). At wiring time: ``add(url, tag=audience)`` for each
target. ``notify`` PREFIXES the body with the ``node_id`` (instance ID — essential COMMUNITY-side,
distributed network) and calls ``async_notify(body, notify_type, tag)``. No URL → natural no-op
(apprise with no service returns ``None``). ``apprise_obj`` injectable for testing (default: a real
``apprise.Apprise``). The timeout/error absorption live in the dispatcher (E-D13).

DECISION (audit 2026-06-23 / security-network#2): the apprise egress (Slack, Discord, SMTP,
etc. webhooks) traverses the crawler's HOST network — not the VPN. The packaging spec accepts this
tradeoff: the P2P kill-switch stays effective (eD2k blocked outside the VPN, anonymity preserved);
only the notification IP↔webhook CORRELATION remains (an operator who notifies on Slack exposes
their host's IP to Slack, not their P2P traffic). This is a DELIBERATE choice, not a flaw.

No apprise stubs → targeted ``# type: ignore`` (mypy override, Task 9)."""

from collections.abc import Sequence

import apprise

from emule_indexer.domain.observability.policy import Audience, Severity

# tuple (url, audience) — the config (Task 7) produces these pairs from ``local.yaml``.
NotificationTargets = Sequence[tuple[str, Audience]]

_NOTIFY_TYPES: dict[Severity, object] = {
    Severity.DEBUG: apprise.NotifyType.INFO,
    Severity.INFO: apprise.NotifyType.INFO,
    Severity.WARNING: apprise.NotifyType.WARNING,
    Severity.ERROR: apprise.NotifyType.FAILURE,
}


class AppriseNotifier:
    """``Notifier`` adapter: one apprise channel per audience (tag), body prefixed with node_id."""

    def __init__(
        self,
        targets: NotificationTargets,
        *,
        node_id: str,
        apprise_obj: object | None = None,
    ) -> None:
        # Typed ``object`` on purpose: the adapter does not depend on apprise's (untyped)
        # surface; ``.add``/``.async_notify`` carry a ``# type: ignore[attr-defined]``.
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
