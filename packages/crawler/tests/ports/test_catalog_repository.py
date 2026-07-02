from collections.abc import Mapping, Sequence

from catalog_matching.engine import (
    DecisionRecord,
    DownloadCandidate,
    Explanation,
    MatchDecision,
)
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import CatalogRepository, ObservedFile


class _StubRepository:
    """Minimal structural implementation: satisfies CatalogRepository WITHOUT importing it."""

    def __init__(self) -> None:
        self.observations: list[FileObservation] = []
        self.decisions: list[tuple[str, MatchDecision]] = []
        self.verifications: list[tuple[str, str, dict[str, object], list[object]]] = []

    def record_observation(self, observation: FileObservation) -> None:
        self.observations.append(observation)

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None:
        self.decisions.append((ed2k_hash, decision))

    def last_decision(self, ed2k_hash: str) -> DecisionRecord | None:
        return None

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return ()

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return None

    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: Sequence[object],
    ) -> None:
        self.verifications.append((ed2k_hash, verdict, dict(real_meta), list(checks)))


def test_protocol_is_satisfied_structurally() -> None:
    stub = _StubRepository()
    repository: CatalogRepository = stub  # mypy proves the structural satisfaction
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )
    decision = MatchDecision(
        target_id="S2E062A",
        rule_name="exact",
        tier="download",
        explanation=Explanation(
            target_id="S2E062A", rules_fired=("exact",), tokens_matched=(), coverage_values=()
        ),
    )
    repository.record_observation(observation)
    repository.record_decision(observation.ed2k_hash, decision)
    assert repository.last_decision(observation.ed2k_hash) is None
    assert repository.download_decisions() == ()
    assert repository.last_observation(observation.ed2k_hash) is None
    repository.record_verification(observation.ed2k_hash, "unverified", {"k": 1}, ["c"])
    assert stub.observations == [observation]
    assert stub.decisions == [(observation.ed2k_hash, decision)]
    assert stub.verifications == [(observation.ed2k_hash, "unverified", {"k": 1}, ["c"])]
