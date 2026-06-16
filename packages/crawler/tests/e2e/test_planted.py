"""Vérifie que la constante PLANTED_ED2K_HASH correspond bien au binaire planté commité."""

from __future__ import annotations

from tests.e2e.md4 import ed2k_hash, md4
from tests.e2e.planted import (
    PLANTED_ED2K_HASH,
    PLANTED_FILENAME,
    PLANTED_PATH,
    PLANTED_SIZE,
    PLANTED_TARGET_ID,
)


def test_planted_binary_is_committed_and_nonempty() -> None:
    assert PLANTED_PATH.is_file()
    data = PLANTED_PATH.read_bytes()
    assert len(data) == PLANTED_SIZE
    assert len(data) > 0


def test_planted_hash_matches_committed_binary() -> None:
    data = PLANTED_PATH.read_bytes()
    assert ed2k_hash(data) == PLANTED_ED2K_HASH


def test_planted_hash_is_not_the_empty_file_hash() -> None:
    # Garde-fou : un 0-octet (hash 31d6cfe0…) est « instantanément complet » côté amuled et jamais
    # listé comme partfile actif (leçon du download_integration). Le planté n'en est pas un.
    assert md4(b"") != PLANTED_ED2K_HASH


def test_planted_filename_and_target_wired_for_segment_id_rule() -> None:
    # Le nom doit satisfaire is_video (.mp4) + segment_id (n°62 A) + keroro → cible S2E062A.
    assert PLANTED_FILENAME.lower().endswith(".mp4")
    assert "keroro" in PLANTED_FILENAME.lower()
    assert "62" in PLANTED_FILENAME
    assert PLANTED_TARGET_ID == "S2E062A"
