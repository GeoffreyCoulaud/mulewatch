from catalog_matching.engine import (
    DecisionRecord,
    Explanation,
    MatchDecision,
    to_record,
)


def _decision(tier: str = "download") -> MatchDecision:
    return MatchDecision(
        target_id="062A",
        rule_name="id_segment_exact",
        tier=tier,
        explanation=Explanation(
            target_id="062A",
            rules_fired=("id_segment_exact",),
            tokens_matched=(),
            coverage_values=(),
        ),
    )


def test_to_record_projects_the_three_comparable_fields() -> None:
    record = to_record(_decision())
    assert record == DecisionRecord(target_id="062A", rule_name="id_segment_exact", tier="download")


def test_decision_record_is_frozen_and_equal_by_value() -> None:
    a = DecisionRecord(target_id="062A", rule_name="r", tier="catalog")
    b = DecisionRecord(target_id="062A", rule_name="r", tier="catalog")
    assert a == b
    assert hash(a) == hash(b)


def test_records_differ_when_any_field_differs() -> None:
    base = to_record(_decision(tier="download"))
    assert base != to_record(_decision(tier="notify"))
