from emule_indexer.ports.decision_signal import DecisionSignal


class _StubSignal:
    """Satisfies DecisionSignal structurally (without importing it)."""

    def __init__(self) -> None:
        self.signalled: list[str] = []

    def signal(self, subject: str) -> None:
        self.signalled.append(subject)

    async def wait(self, subject: str) -> None:
        return None


def test_protocol_is_satisfied_structurally() -> None:
    hub: DecisionSignal = _StubSignal()
    hub.signal("S2E062A")
    assert isinstance(hub, _StubSignal)
    assert hub.signalled == ["S2E062A"]
