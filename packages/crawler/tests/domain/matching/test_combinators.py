from emule_indexer.domain.matching.combinators import (
    AllMatcher,
    AnyMatcher,
    Matcher,
    NotMatcher,
)
from emule_indexer.domain.matching.matchers import KeywordMatcher
from emule_indexer.domain.matching.models import FileCandidate


class _Const:
    """Matcher de test à verdict constant (satisfait le Protocol Matcher)."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict

    def matches(self, candidate: FileCandidate) -> bool:
        return self._verdict


_ANY = FileCandidate(filename="whatever.avi")


def test_all_true_when_every_child_true() -> None:
    matcher = AllMatcher((_Const(True), _Const(True)))
    assert matcher.matches(_ANY) is True


def test_all_false_when_one_child_false() -> None:
    matcher = AllMatcher((_Const(True), _Const(False)))
    assert matcher.matches(_ANY) is False


def test_all_empty_is_true() -> None:
    # all([]) == True (neutre de la conjonction).
    matcher = AllMatcher(())
    assert matcher.matches(_ANY) is True


def test_any_true_when_one_child_true() -> None:
    matcher = AnyMatcher((_Const(False), _Const(True)))
    assert matcher.matches(_ANY) is True


def test_any_false_when_all_children_false() -> None:
    matcher = AnyMatcher((_Const(False), _Const(False)))
    assert matcher.matches(_ANY) is False


def test_any_empty_is_false() -> None:
    # any([]) == False (neutre de la disjonction).
    matcher = AnyMatcher(())
    assert matcher.matches(_ANY) is False


def test_not_inverts_child() -> None:
    assert NotMatcher(_Const(True)).matches(_ANY) is False
    assert NotMatcher(_Const(False)).matches(_ANY) is True


def test_nested_combinators() -> None:
    # all[ any[False, True], not False ] == all[True, True] == True
    matcher = AllMatcher((AnyMatcher((_Const(False), _Const(True))), NotMatcher(_Const(False))))
    assert matcher.matches(_ANY) is True


def test_real_leaf_satisfies_protocol_and_composes() -> None:
    leaf: Matcher = KeywordMatcher("keroro")
    matcher = AnyMatcher((leaf, _Const(False)))
    assert matcher.matches(FileCandidate(filename="Keroro 062A.avi")) is True
    assert matcher.matches(FileCandidate(filename="autre.avi")) is False
