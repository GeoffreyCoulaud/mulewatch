from pathlib import Path

from mulewatch.ports.quarantine import Quarantine


class _StubQuarantine:
    """Satisfies Quarantine structurally (without importing it)."""

    def __init__(self) -> None:
        self.promoted: list[tuple[Path, str]] = []

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        self.promoted.append((staging_path, ed2k_hash))


def test_protocol_is_satisfied_structurally() -> None:
    quarantine: Quarantine = _StubQuarantine()
    quarantine.promote(Path("/staging/x.part"), "a" * 32)
    assert isinstance(quarantine, _StubQuarantine)
    assert quarantine.promoted == [(Path("/staging/x.part"), "a" * 32)]
