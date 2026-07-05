from mulewatch.domain.search.coverage import Coverage, effective_coverage


def test_no_instances_is_blind() -> None:
    assert effective_coverage([]) is Coverage.BLIND


def test_all_incapable_is_blind() -> None:
    assert effective_coverage([False, False]) is Coverage.BLIND


def test_all_capable_is_healthy() -> None:
    assert effective_coverage([True, True, True]) is Coverage.HEALTHY


def test_single_capable_is_healthy() -> None:
    assert effective_coverage([True]) is Coverage.HEALTHY


def test_mixed_is_degraded() -> None:
    assert effective_coverage([True, False]) is Coverage.DEGRADED


def test_coverage_is_a_closed_enum() -> None:
    assert set(Coverage) == {Coverage.HEALTHY, Coverage.DEGRADED, Coverage.BLIND}
