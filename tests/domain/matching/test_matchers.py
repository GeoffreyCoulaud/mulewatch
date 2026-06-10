from emule_indexer.domain.matching.matchers import KeywordMatcher
from emule_indexer.domain.matching.models import FileCandidate


def test_keyword_single_word_present() -> None:
    matcher = KeywordMatcher("keroro")
    assert matcher.matches(FileCandidate(filename="Keroro 062A.avi")) is True


def test_keyword_single_word_absent() -> None:
    matcher = KeywordMatcher("titar")
    assert matcher.matches(FileCandidate(filename="Keroro 062A.avi")) is False


def test_keyword_multiword_contiguous_present() -> None:
    matcher = KeywordMatcher("mission titar")
    candidate = FileCandidate(filename="Keroro Mission Titar 062A.avi")
    assert matcher.matches(candidate) is True


def test_keyword_multiword_non_contiguous_absent() -> None:
    matcher = KeywordMatcher("mission titar")
    candidate = FileCandidate(filename="mission keroro titar.avi")
    assert matcher.matches(candidate) is False


def test_keyword_accent_and_case_insensitive_via_tokenize() -> None:
    matcher = KeywordMatcher("teletoon")
    assert matcher.matches(FileCandidate(filename="Keroro TÉLÉTOON.avi")) is True


def test_keyword_empty_phrase_matches_anything() -> None:
    matcher = KeywordMatcher("")
    assert matcher.matches(FileCandidate(filename="whatever.avi")) is True


def test_keyword_phrase_longer_than_filename_is_absent() -> None:
    matcher = KeywordMatcher("keroro mission titar special")
    assert matcher.matches(FileCandidate(filename="keroro mission titar.avi")) is False
