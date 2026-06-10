import datetime

from emule_indexer.domain.matching.models import FileCandidate, TargetSegment


def test_file_candidate_defaults() -> None:
    candidate = FileCandidate(filename="keroro.avi")
    assert candidate.filename == "keroro.avi"
    assert candidate.size_mb is None
    assert candidate.duration_sec is None
    assert candidate.bitrate_kbps is None


def test_file_candidate_with_attributes() -> None:
    candidate = FileCandidate(
        filename="keroro.avi",
        size_mb=120.0,
        duration_sec=1320.0,
        bitrate_kbps=900.0,
    )
    assert candidate.size_mb == 120.0
    assert candidate.duration_sec == 1320.0
    assert candidate.bitrate_kbps == 900.0


def test_target_segment_defaults() -> None:
    target = TargetSegment(season=2, number=62, segment="a", title="Les demoiselles")
    assert target.broadcast_date is None
    assert target.status == "lost"
    assert target.aliases == ()


def test_target_segment_target_id_pads_and_uppercases() -> None:
    target = TargetSegment(season=2, number=62, segment="a", title="Les demoiselles")
    assert target.target_id == "S2E062A"


def test_target_segment_full_fields() -> None:
    target = TargetSegment(
        season=1,
        number=5,
        segment="b",
        title="Le grand combat",
        broadcast_date=datetime.date(2008, 9, 21),
        status="partial",
        aliases=("alt one", "alt two"),
    )
    assert target.target_id == "S1E005B"
    assert target.broadcast_date == datetime.date(2008, 9, 21)
    assert target.status == "partial"
    assert target.aliases == ("alt one", "alt two")
