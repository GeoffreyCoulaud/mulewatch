import dataclasses

import pytest

from emule_indexer.domain.matching.models import FileCandidate
from emule_indexer.domain.observation import FileObservation


def _full_observation() -> FileObservation:
    return FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=3 * 1024 * 1024,
        source_count=5,
        complete_source_count=2,
        keyword="keroro",
        media_length_sec=1234,
        bitrate_kbps=1500,
        codec="xvid",
        file_type="Video",
        raw_meta=(("0x0308", "0"),),
    )


def test_file_observation_is_frozen_and_holds_fields() -> None:
    observation = _full_observation()
    assert observation.ed2k_hash == "31d6cfe0d16ae931b73c59d7e0c089c0"
    assert observation.filename == "Keroro 062A.avi"
    assert observation.size_bytes == 3 * 1024 * 1024
    assert observation.source_count == 5
    assert observation.complete_source_count == 2
    assert observation.keyword == "keroro"
    assert observation.raw_meta == (("0x0308", "0"),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.filename = "autre"  # type: ignore[misc]


def test_media_fields_and_raw_meta_default_to_absent() -> None:
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=0,
        complete_source_count=0,
        keyword="keroro",
    )
    assert observation.media_length_sec is None
    assert observation.bitrate_kbps is None
    assert observation.codec is None
    assert observation.file_type is None
    assert observation.raw_meta == ()


def test_to_candidate_converts_units_with_media_metadata() -> None:
    # 3 Mio exactement -> size_mb == 3.0 (DÉCISION 8 : 1 Mio = 1024*1024 octets).
    candidate = _full_observation().to_candidate()
    assert candidate == FileCandidate(
        filename="Keroro 062A.avi",
        size_mb=3.0,
        duration_sec=1234.0,
        bitrate_kbps=1500.0,
    )


def test_to_candidate_maps_absent_media_metadata_to_none() -> None:
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=524288,  # 0.5 Mio
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )
    candidate = observation.to_candidate()
    assert candidate == FileCandidate(
        filename="Keroro 062A.avi",
        size_mb=0.5,
        duration_sec=None,
        bitrate_kbps=None,
    )
