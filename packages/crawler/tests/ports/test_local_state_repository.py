import dataclasses

import pytest

from emule_indexer.ports.local_state_repository import ClaimedTask, LocalStateRepository


def test_claimed_task_is_frozen_and_holds_fields() -> None:
    task = ClaimedTask(task_id=7, ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0", attempts=1)
    assert task.task_id == 7
    assert task.ed2k_hash == "31d6cfe0d16ae931b73c59d7e0c089c0"
    assert task.attempts == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.attempts = 2  # type: ignore[misc]


class _StubRepository:
    """Minimal structural implementation: satisfies LocalStateRepository WITHOUT importing it."""

    def node_id(self) -> str:
        return "00000000-0000-0000-0000-000000000000"

    def enqueue_verification(self, ed2k_hash: str) -> bool:
        return True

    def claim_verification(self) -> ClaimedTask | None:
        return None

    def complete_verification(self, task_id: int) -> None:
        return None

    def fail_verification(self, task_id: int) -> None:
        return None

    def reclaim_expired(self) -> int:
        return 0

    def count_pending_verifications(self) -> int:
        return 0


def test_protocol_is_satisfied_structurally() -> None:
    repository: LocalStateRepository = _StubRepository()  # mypy proves the satisfaction
    assert repository.node_id() == "00000000-0000-0000-0000-000000000000"
    assert repository.enqueue_verification("31d6cfe0d16ae931b73c59d7e0c089c0") is True
    assert repository.claim_verification() is None
    repository.complete_verification(1)
    repository.fail_verification(1)
    assert repository.reclaim_expired() == 0
    assert repository.count_pending_verifications() == 0
