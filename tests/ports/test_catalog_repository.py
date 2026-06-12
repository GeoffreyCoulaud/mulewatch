from emule_indexer.domain.matching.engine import Explanation, MatchDecision
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import CatalogRepository


class _StubRepository:
    """Implémentation structurelle minimale : satisfait CatalogRepository SANS l'importer."""

    def __init__(self) -> None:
        self.observations: list[FileObservation] = []
        self.decisions: list[tuple[str, MatchDecision]] = []

    def record_observation(self, observation: FileObservation) -> None:
        self.observations.append(observation)

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None:
        self.decisions.append((ed2k_hash, decision))


def test_protocol_is_satisfied_structurally() -> None:
    stub = _StubRepository()
    repository: CatalogRepository = stub  # mypy prouve la satisfaction structurelle
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
    assert stub.observations == [observation]
    assert stub.decisions == [(observation.ed2k_hash, decision)]
