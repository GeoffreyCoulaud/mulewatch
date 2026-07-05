import stat
from pathlib import Path

import pytest

from mulewatch.adapters.quarantine_fs import FilesystemQuarantine


def test_promote_moves_the_file_to_quarantine_by_hash(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    source = staging / "Keroro 062A.avi"
    source.write_bytes(b"\x00\x01\x02")  # the crawler NEVER reads these bytes
    adapter = FilesystemQuarantine(quarantine)

    adapter.promote(source, "a" * 32)

    moved = quarantine / ("a" * 32)
    assert not source.exists()  # atomic rename: the source is gone
    assert moved.exists()
    assert moved.stat().st_size == 3


def test_promote_never_sets_executable_bits(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    source = staging / "x.bin"
    source.write_bytes(b"data")
    FilesystemQuarantine(quarantine).promote(source, "b" * 32)
    mode = (quarantine / ("b" * 32)).stat().st_mode
    assert not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def test_promote_missing_source_raises(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    adapter = FilesystemQuarantine(quarantine)
    with pytest.raises(FileNotFoundError):
        adapter.promote(tmp_path / "absent.part", "c" * 32)


def test_promote_is_idempotent_when_source_already_consumed(tmp_path: Path) -> None:
    # Regression logic-download#0: os.replace CONSUMES the source. If a POST-promote step
    # (enqueue_verification / set_state) fails and the loop retries on the next cycle,
    # promote is called again with the source ALREADY consumed while quarantine/<hash> is in
    # place. This re-promote must be an idempotent NO-OP (the file IS promoted), not raise —
    # otherwise the file stays stuck in completed forever, never verified.
    staging = tmp_path / "staging"
    staging.mkdir()
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    source = staging / "Keroro 062A.avi"
    source.write_bytes(b"\x00\x01\x02")
    adapter = FilesystemQuarantine(quarantine)
    adapter.promote(source, "a" * 32)  # 1st promotion: source consumed by the rename
    moved = quarantine / ("a" * 32)
    assert moved.exists() and not source.exists()

    adapter.promote(source, "a" * 32)  # retry: source missing, target present → no-op

    assert moved.exists()
    assert moved.read_bytes() == b"\x00\x01\x02"  # target intact, not corrupted
