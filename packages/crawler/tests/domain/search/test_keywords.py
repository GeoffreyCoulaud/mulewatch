from catalog_matching.models import TargetSegment
from emule_indexer.domain.search.keywords import SearchKeyword, generate_keywords

_S2E062A = TargetSegment(
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="A",
    title="Les demoiselles cambrioleuses",
)
_S2E062B = TargetSegment(
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="B",
    title="Le grand combat sous-marin",
)


def test_broad_keyword_is_first_and_tagged_broad() -> None:
    keywords = generate_keywords([_S2E062A])
    assert keywords[0] == SearchKeyword(text="keroro", origin="broad")


def test_segment_id_keyword_is_zero_padded_and_lowercased() -> None:
    keywords = generate_keywords([_S2E062A])
    texts = [kw.text for kw in keywords]
    assert "062a" in texts
    segment_kw = next(kw for kw in keywords if kw.text == "062a")
    assert segment_kw.origin == "S2E062A"


def test_title_tokens_are_generated_and_tagged_with_target_id() -> None:
    keywords = generate_keywords([_S2E062A])
    texts = [kw.text for kw in keywords]
    assert "demoiselles" in texts
    assert "cambrioleuses" in texts
    token = next(kw for kw in keywords if kw.text == "demoiselles")
    assert token.origin == "S2E062A"


def test_short_title_tokens_are_dropped() -> None:
    # "le" (len 2) reste, mais un token d'un seul caractère est écarté ; on force le cas
    # avec un titre contenant un mot d'une lettre.
    target = TargetSegment(
        season=2, seasonal_number=1, absolute_number=1, segment="A", title="a b cd"
    )
    texts = [kw.text for kw in generate_keywords([target])]
    assert "a" not in texts  # 1 caractère : écarté
    assert "b" not in texts
    assert "cd" in texts  # 2 caractères : gardé


def test_duplicate_tokens_across_targets_appear_once_first_seen_wins() -> None:
    shared = TargetSegment(
        season=2, seasonal_number=2, absolute_number=2, segment="A", title="combat secret"
    )
    other = TargetSegment(
        season=2, seasonal_number=3, absolute_number=3, segment="A", title="combat final"
    )
    keywords = generate_keywords([shared, other])
    combats = [kw for kw in keywords if kw.text == "combat"]
    assert len(combats) == 1
    assert combats[0].origin == "S2E002A"  # premier vu gagne


def test_empty_targets_yields_only_the_broad_keyword() -> None:
    keywords = generate_keywords([])
    assert keywords == (SearchKeyword(text="keroro", origin="broad"),)


def test_keyword_is_frozen_and_hashable() -> None:
    keyword = SearchKeyword(text="keroro", origin="broad")
    assert {keyword, keyword} == {keyword}
    assert hash(keyword) == hash(SearchKeyword(text="keroro", origin="broad"))


def test_two_segments_produce_distinct_segment_ids() -> None:
    texts = [kw.text for kw in generate_keywords([_S2E062A, _S2E062B])]
    assert "062a" in texts
    assert "062b" in texts
