"""Notifier apprise : add(url, tag) au montage, préfixe node_id, route par tag, mappe NotifyType."""

import apprise
import pytest

from emule_indexer.adapters.observability.apprise_notifier import AppriseNotifier
from emule_indexer.domain.observability.policy import Audience, Severity


class _FakeApprise:
    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []
        self.sent: list[dict[str, object]] = []

    def add(self, url: str, tag: str) -> bool:
        self.added.append((url, tag))
        return True

    async def async_notify(self, **kwargs: object) -> bool:
        self.sent.append(kwargs)
        return True


def _notifier(fake: _FakeApprise, node_id: str = "titar-node-1") -> AppriseNotifier:
    targets = (("discord://x", Audience.COMMUNITY), ("discord://y", Audience.OPERATIONS))
    return AppriseNotifier(targets, node_id=node_id, apprise_obj=fake)


def test_targets_added_with_tags() -> None:
    fake = _FakeApprise()
    _notifier(fake)
    assert fake.added == [("discord://x", "community"), ("discord://y", "operations")]


@pytest.mark.asyncio
async def test_notify_prefixes_node_id_and_routes_tag() -> None:
    fake = _FakeApprise()
    await _notifier(fake).notify(Audience.COMMUNITY, "épisode trouvé", Severity.INFO)
    call = fake.sent[-1]
    assert call["tag"] == "community"
    assert call["body"] == "[titar-node-1] épisode trouvé"
    assert call["notify_type"] == apprise.NotifyType.INFO


@pytest.mark.asyncio
async def test_severity_maps_to_failure() -> None:
    fake = _FakeApprise()
    await _notifier(fake).notify(Audience.OPERATIONS, "panne", Severity.ERROR)
    assert fake.sent[-1]["notify_type"] == apprise.NotifyType.FAILURE


@pytest.mark.asyncio
async def test_default_apprise_obj_is_built_from_targets() -> None:
    # Sans apprise_obj injecté, le notifier construit un vrai Apprise (aucune URL → no-op safe).
    notifier = AppriseNotifier((), node_id="n")
    await notifier.notify(Audience.COMMUNITY, "x", Severity.INFO)  # ne lève pas
