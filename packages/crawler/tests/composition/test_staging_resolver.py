"""Tests de ``resolve_staging_path`` (DÉCISION DV10) — les DEUX branches, sans e2e."""

from pathlib import Path

from emule_indexer.composition.app import resolve_staging_path
from emule_indexer.domain.matching.engine import DownloadCandidate
from emule_indexer.ports.catalog_repository import ObservedFile
from emule_indexer.ports.mule_download_client import DownloadEntry


class _Cat:
    """Satisfait le Protocol narrow CatalogReader (last_observation seul)."""

    def __init__(self, obs: ObservedFile | None) -> None:
        self._obs = obs

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return ()

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return self._obs


def test_resolve_uses_observation_filename() -> None:
    entry = DownloadEntry(ed2k_hash="a" * 32, size_done=1, size_full=1)
    path = resolve_staging_path(Path("/staging"), _Cat(ObservedFile("Keroro.avi", 1)), entry)
    assert path == Path("/staging/Keroro.avi")


def test_resolve_falls_back_to_hash_when_no_observation() -> None:
    entry = DownloadEntry(ed2k_hash="b" * 32, size_done=1, size_full=1)
    path = resolve_staging_path(Path("/staging"), _Cat(None), entry)
    assert path == Path("/staging") / ("b" * 32)


def test_resolve_confines_hostile_traversal_filename_to_basename() -> None:
    # filename = input hostile (CLAUDE.md) : un nom avec traversal ('../../etc/passwd') NE DOIT
    # PAS pouvoir sortir de staging_base — la SOURCE de os.replace reste confinée au basename.
    entry = DownloadEntry(ed2k_hash="c" * 32, size_done=1, size_full=1)
    staging = Path("/staging")
    path = resolve_staging_path(staging, _Cat(ObservedFile("../../etc/passwd", 1)), entry)
    assert path == staging / "passwd"  # confiné au basename
    assert ".." not in path.parts  # plus aucune composante de traversal
    assert path.parent == staging  # le parent EST staging_base : ne peut pas s'en échapper


def test_resolve_falls_back_to_hash_when_filename_is_degenerate() -> None:
    # nom dégénéré '..' : Path('..').name == '..' (PAS '' !) → staging_base / '..' remonterait
    # d'un cran. Le rejet explicite des noms {'', '.', '..'} retombe sur le hash (confiné) :
    # couvre la branche de garde anti-traversal.
    entry = DownloadEntry(ed2k_hash="d" * 32, size_done=1, size_full=1)
    staging = Path("/staging")
    path = resolve_staging_path(staging, _Cat(ObservedFile("..", 1)), entry)
    assert path == staging / ("d" * 32)
    assert ".." not in path.parts  # ne remonte PAS d'un cran
    assert path.parent == staging  # confiné
