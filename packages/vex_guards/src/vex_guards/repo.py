"""Repo-relative paths the guard checks read: source trees, Dockerfiles, VEX files.

Everything is derived from the repo root (four parents up from this module), so
the checks stay correct no matter the working directory the gate runs from.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]


def repo_root() -> Path:
    return _ROOT


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT))
    except ValueError:
        return str(path)


def source_dirs() -> list[Path]:
    return sorted(d for d in (_ROOT / "packages").glob("*/src") if d.parent.name != "vex_guards")


def dockerfiles() -> list[Path]:
    return [_ROOT / "packages" / name / "Dockerfile" for name in ("crawler", "verifier")]


def vex_files() -> dict[str, Path]:
    return {
        image: _ROOT / "security" / f"{image}.vex.openvex.json" for image in ("crawler", "verifier")
    }
