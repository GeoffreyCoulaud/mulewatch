"""compact CLI: safe-by-default (fresh output, source present, keep-recent-days >= 0)."""

from pathlib import Path

from mulewatch.compact.__main__ import main

from ..merge.helpers import HASH_A, make_catalog
from .helpers import read_ranges


def _src(tmp_path: Path) -> Path:
    return make_catalog(
        tmp_path / "src.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 1}],
            "file_observations": [
                {
                    "ed2k_hash": HASH_A,
                    "filename": "f.avi",
                    "size_bytes": 1,
                    "source_count": 1,
                    "complete_source_count": 0,
                    "raw_meta": "[]",
                    "keyword": "k",
                    "observed_at": "2020-01-01T00:00:00.000000+00:00",
                    "node_id": "n",
                }
            ],
        },
    )


def test_new_output_succeeds(tmp_path: Path) -> None:
    out = tmp_path / "out.db"
    assert main([str(_src(tmp_path)), "-o", str(out)]) == 0
    assert len(read_ranges(out)) == 1  # keep-recent-days defaults to 90; the 2020 obs is old


def test_existing_output_refused(tmp_path: Path) -> None:
    out = tmp_path / "out.db"
    out.write_bytes(b"")
    assert main([str(_src(tmp_path)), "-o", str(out)]) == 2


def test_missing_source_refused(tmp_path: Path) -> None:
    assert main([str(tmp_path / "absent.db"), "-o", str(tmp_path / "out.db")]) == 2


def test_negative_keep_recent_days_refused(tmp_path: Path) -> None:
    assert (
        main([str(_src(tmp_path)), "-o", str(tmp_path / "out.db"), "--keep-recent-days", "-1"]) == 2
    )
