"""Tests TDD du CLI ``python -m emule_indexer.merge`` (safe-by-default) — §6/§7 du design.

On appelle ``main(argv)`` directement (rend un ``int``) ; les erreurs d'usage/merge rendent
``2`` avec un message clair sur ``stderr`` (jamais de traceback) ; argparse rend lui-même
``2`` (via ``SystemExit``) pour une erreur de parsing (groupe mutuellement exclusif/requis).
"""

from pathlib import Path

import pytest

from emule_indexer.merge.__main__ import main

from .helpers import HASH_A, HASH_B, count, make_catalog


def _seed(path: Path, letter: str) -> Path:
    ed2k = HASH_A if letter == "a" else HASH_B
    return make_catalog(
        path,
        {
            "files": [{"ed2k_hash": ed2k, "size_bytes": 100}],
            "file_observations": [
                {
                    "ed2k_hash": ed2k,
                    "filename": "f.avi",
                    "size_bytes": 100,
                    "source_count": 1,
                    "complete_source_count": 0,
                    "raw_meta": "[]",
                    "keyword": "k",
                    "observed_at": f"t-{letter}",
                    "node_id": f"node-{letter}",
                }
            ],
        },
    )


def test_t1_cli_output_mode_merges(tmp_path: Path) -> None:
    src_a = _seed(tmp_path / "a.db", "a")
    src_b = _seed(tmp_path / "b.db", "b")
    out = tmp_path / "out.db"

    code = main(["--output", str(out), str(src_a), str(src_b)])

    assert code == 0
    assert count(out, "files") == 2


def test_t7_output_exists_without_force_errors(tmp_path: Path) -> None:
    src = _seed(tmp_path / "a.db", "a")
    existing = _seed(tmp_path / "out.db", "b")  # la sortie existe déjà (1 fichier : HASH_B).

    code = main(["--output", str(existing), str(src)])

    assert code == 2
    # Pas d'écrasement : le fichier existant est inchangé (toujours son seul fichier).
    assert count(existing, "files") == 1


def test_t8_output_exists_with_force_appends(tmp_path: Path) -> None:
    src = _seed(tmp_path / "a.db", "a")
    existing = _seed(tmp_path / "out.db", "b")

    code = main(["--output", str(existing), "--force", str(src)])

    assert code == 0
    # Append idempotent : la sortie contient désormais l'union (HASH_A + HASH_B).
    assert count(existing, "files") == 2

    # Re-merge avec --force → no-op (idempotent).
    code = main(["--output", str(existing), "--force", str(src)])
    assert code == 0
    assert count(existing, "files") == 2


def test_t9_into_explicit_dest_is_a_source(tmp_path: Path) -> None:
    src_a = _seed(tmp_path / "a.db", "a")
    src_b = _seed(tmp_path / "b.db", "b")

    code = main(["--into", str(src_a), str(src_a), str(src_b)])

    assert code == 0
    # srcA contient désormais l'union (son propre HASH_A + HASH_B venu de srcB).
    assert count(src_a, "files") == 2

    # Idempotent : re-merger ne duplique rien.
    code = main(["--into", str(src_a), str(src_a), str(src_b)])
    assert code == 0
    assert count(src_a, "files") == 2


def test_t10_into_must_be_a_listed_source(tmp_path: Path) -> None:
    src_a = _seed(tmp_path / "a.db", "a")
    src_b = _seed(tmp_path / "b.db", "b")
    other = _seed(tmp_path / "other.db", "a")  # existe mais N'est PAS dans la liste.

    code = main(["--into", str(other), str(src_a), str(src_b)])

    assert code == 2


def test_t11_output_and_into_mutually_exclusive(tmp_path: Path) -> None:
    src = _seed(tmp_path / "a.db", "a")
    out = tmp_path / "out.db"

    # --output ET --into → erreur argparse (SystemExit code 2).
    with pytest.raises(SystemExit) as excinfo:
        main(["--output", str(out), "--into", str(src), str(src)])
    assert excinfo.value.code == 2


def test_t11_neither_output_nor_into_required(tmp_path: Path) -> None:
    src = _seed(tmp_path / "a.db", "a")

    # NI --output NI --into → erreur argparse (groupe required → SystemExit code 2).
    with pytest.raises(SystemExit) as excinfo:
        main([str(src)])
    assert excinfo.value.code == 2


def test_t12_force_with_into_is_rejected(tmp_path: Path) -> None:
    src_a = _seed(tmp_path / "a.db", "a")
    src_b = _seed(tmp_path / "b.db", "b")

    code = main(["--into", str(src_a), "--force", str(src_a), str(src_b)])

    assert code == 2


def test_t13_missing_source_file_errors_before_output_created(tmp_path: Path) -> None:
    src = _seed(tmp_path / "a.db", "a")
    missing = tmp_path / "missing.db"
    out = tmp_path / "out.db"

    code = main(["--output", str(out), str(src), str(missing)])

    assert code == 2
    # Fail-fast : la sortie n'a PAS été créée (on échoue avant d'ouvrir/créer la sortie).
    assert not out.exists()
