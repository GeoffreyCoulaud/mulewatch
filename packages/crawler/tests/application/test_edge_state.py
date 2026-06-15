"""EdgeState : first_occurrence = transition vers actif ; leave réarme."""

from emule_indexer.application.edge_state import EdgeState


def test_enter_is_true_only_on_transition() -> None:
    state = EdgeState()
    assert state.enter("verifier_unavailable") is True  # 1re fois → transition
    assert state.enter("verifier_unavailable") is False  # déjà actif → pas de re-notif


def test_leave_rearms() -> None:
    state = EdgeState()
    state.enter("blind")
    assert state.leave("blind") is True  # était actif → transition de sortie
    assert state.leave("blind") is False  # déjà inactif
    assert state.enter("blind") is True  # réarmé → re-transition


def test_conditions_are_independent() -> None:
    state = EdgeState()
    assert state.enter("a") is True
    assert state.enter("b") is True
    assert state.enter("a") is False
