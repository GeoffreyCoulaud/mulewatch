import ast
from pathlib import Path

import pytest

from vex_guards import repo
from vex_guards.descriptors import ModuleNotImported, SourceGuard, SubprocessDenies, is_source_guard
from vex_guards.registry import GUARDS
from vex_guards.source_scan import _imported_modules, evaluate


def _src_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    for name, code in files.items():
        (root / name).write_text(code)
    return root


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
    assert [v.cve for v in evaluate(guards, [src])] == ["CVE-X"]


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
    assert evaluate(guards, [src]) == []


def test_subprocess_denies_flags_program(tmp_path: Path) -> None:
    src = _src_dir(tmp_path, {"mod.py": 'subprocess.run(["ffmpeg", "-i", path])'})
    guards: dict[str, SourceGuard] = {"CVE-F": SubprocessDenies("ffmpeg")}
    assert [v.cve for v in evaluate(guards, [src])] == ["CVE-F"]


def test_subprocess_denies_passes_on_other_program(tmp_path: Path) -> None:
    src = _src_dir(tmp_path, {"mod.py": 'subprocess.run(["ffprobe", path])'})
    guards: dict[str, SourceGuard] = {"CVE-F": SubprocessDenies("ffmpeg")}
    assert evaluate(guards, [src]) == []


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
    assert evaluate(guards, repo.source_dirs()) == []
