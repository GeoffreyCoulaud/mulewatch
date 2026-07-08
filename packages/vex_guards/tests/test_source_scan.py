import ast
from pathlib import Path

import pytest

from vex_guards import repo
from vex_guards.descriptors import (
    BaseImageIsAlpine,
    BinaryNotInvoked,
    ModuleNotImported,
    SourceGuard,
    SubprocessDenies,
    is_source_guard,
)
from vex_guards.registry import GUARDS
from vex_guards.source_scan import _imported_modules, _rel, evaluate


def _src_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    for name, code in files.items():
        (root / name).write_text(code)
    return root


def _dockerfile(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "Dockerfile"
    path.write_text(text)
    return path


def _named_dockerfile(tmp_path: Path, name: str, text: str) -> Path:
    """A Dockerfile under its own subdir so several coexist with distinct paths."""
    directory = tmp_path / name
    directory.mkdir()
    path = directory / "Dockerfile"
    path.write_text(text)
    return path


# Every form that makes the module reachable: plain, from-import, alias, dotted,
# and the three dynamic-import spellings the scanner also has to see.
IMPORT_FAILS = [
    "import tarfile",
    "from tarfile import TarFile",
    "import tarfile as t",
    "import tarfile.something",
    'importlib.import_module("tarfile")',
    'import_module("tarfile")',  # bare-name alias: from importlib import import_module
    '__import__("tarfile")',
]


@pytest.mark.parametrize("code", IMPORT_FAILS)
def test_module_not_imported_flags_each_import_form(tmp_path: Path, code: str) -> None:
    src = _src_dir(tmp_path, {"mod.py": code})
    guards: dict[str, SourceGuard] = {"CVE-X": ModuleNotImported("tarfile")}
    assert [v.cve for v in evaluate(guards, [src], [])] == ["CVE-X"]


# None of these register "tarfile": an unrelated import, a bare string mention,
# a relative import (module is None), and dynamic imports with a non-constant or
# absent name.
IMPORT_PASSES = [
    "import os",
    'x = "tarfile"',
    "from . import helper",
    "importlib.import_module(module_name)",
    "importlib.import_module()",
]


@pytest.mark.parametrize("code", IMPORT_PASSES)
def test_module_not_imported_passes(tmp_path: Path, code: str) -> None:
    src = _src_dir(tmp_path, {"mod.py": code})
    guards: dict[str, SourceGuard] = {"CVE-X": ModuleNotImported("tarfile")}
    assert evaluate(guards, [src], []) == []


def test_binary_not_invoked_flags_subprocess_word(tmp_path: Path) -> None:
    src = _src_dir(tmp_path, {"mod.py": 'subprocess.run(["wget", url])'})
    guards: dict[str, SourceGuard] = {"CVE-W": BinaryNotInvoked("wget")}
    assert [v.cve for v in evaluate(guards, [src], [])] == ["CVE-W"]


def test_binary_not_invoked_passes_on_substring_word(tmp_path: Path) -> None:
    src = _src_dir(tmp_path, {"mod.py": 'name = "wgets"'})
    guards: dict[str, SourceGuard] = {"CVE-W": BinaryNotInvoked("wget")}
    assert evaluate(guards, [src], []) == []


def test_subprocess_denies_flags_program(tmp_path: Path) -> None:
    src = _src_dir(tmp_path, {"mod.py": 'subprocess.run(["ffmpeg", "-i", path])'})
    guards: dict[str, SourceGuard] = {"CVE-F": SubprocessDenies("ffmpeg")}
    assert [v.cve for v in evaluate(guards, [src], [])] == ["CVE-F"]


def test_subprocess_denies_passes_on_other_program(tmp_path: Path) -> None:
    src = _src_dir(tmp_path, {"mod.py": 'subprocess.run(["ffprobe", path])'})
    guards: dict[str, SourceGuard] = {"CVE-F": SubprocessDenies("ffmpeg")}
    assert evaluate(guards, [src], []) == []


def test_base_image_is_alpine_passes_when_last_from_is_alpine(tmp_path: Path) -> None:
    df = _dockerfile(tmp_path, "FROM builder AS build\nRUN true\nFROM python:3.14-alpine\n")
    guards: dict[str, SourceGuard] = {"CVE-A": BaseImageIsAlpine()}
    assert evaluate(guards, [], [df]) == []


def test_base_image_is_alpine_flags_when_last_from_is_not_alpine(tmp_path: Path) -> None:
    # The first stage IS alpine; only the LAST FROM (the runtime) must count.
    df = _dockerfile(tmp_path, "FROM alpine AS build\nRUN true\nFROM python:3.14-slim\n")
    guards: dict[str, SourceGuard] = {"CVE-A": BaseImageIsAlpine()}
    assert [v.cve for v in evaluate(guards, [], [df])] == ["CVE-A"]


def test_base_image_is_alpine_flags_only_the_non_alpine_dockerfile_among_two(
    tmp_path: Path,
) -> None:
    # One image is alpine, the other drifted to slim: the guard must flag the
    # drifted one specifically (the old "any alpine passes" logic wrongly stayed green).
    alpine_df = _named_dockerfile(tmp_path, "crawler", "FROM python:3.14-alpine\n")
    slim_df = _named_dockerfile(tmp_path, "verifier", "FROM python:3.14-slim\n")
    guards: dict[str, SourceGuard] = {"CVE-A": BaseImageIsAlpine()}
    violations = evaluate(guards, [], [alpine_df, slim_df])
    assert [v.cve for v in violations] == ["CVE-A"]
    assert violations[0].location == _rel(slim_df)


def test_base_image_is_alpine_passes_when_both_dockerfiles_are_alpine(tmp_path: Path) -> None:
    a = _named_dockerfile(tmp_path, "crawler", "FROM python:3.14-alpine\n")
    b = _named_dockerfile(tmp_path, "verifier", "FROM alpine:3.20\n")
    guards: dict[str, SourceGuard] = {"CVE-A": BaseImageIsAlpine()}
    assert evaluate(guards, [], [a, b]) == []


def test_base_image_is_alpine_passes_when_no_dockerfiles(tmp_path: Path) -> None:
    # An empty Dockerfile list is vacuously satisfied: no violation, no IndexError.
    guards: dict[str, SourceGuard] = {"CVE-A": BaseImageIsAlpine()}
    assert evaluate(guards, [], []) == []


def test_imported_modules_detects_and_ignores_dynamic_import_forms() -> None:
    code = (
        'importlib.import_module("tarfile")\n'  # importlib.import_module attribute call
        'import_module("configparser")\n'  # bare-name import_module alias call
        '__import__("csv")\n'  # __import__ name call
        "importlib.import_module(name)\n"  # non-constant argument, ignored
        "importlib.import_module()\n"  # no argument, ignored
        'subprocess.run(["ls"])\n'  # not an import call at all, ignored
    )
    assert _imported_modules(ast.parse(code)) == {"tarfile", "configparser", "csv"}


def test_real_source_tree_has_no_source_claim_violations() -> None:
    guards: dict[str, SourceGuard] = {cve: g for cve, g in GUARDS.items() if is_source_guard(g)}
    assert evaluate(guards, repo.source_dirs(), repo.dockerfiles()) == []
