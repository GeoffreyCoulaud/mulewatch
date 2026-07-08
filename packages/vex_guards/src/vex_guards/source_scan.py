"""The source-family scanner: proves our own code never reaches the paths a
``vulnerable_code_not_in_execute_path`` VEX claim declares exempt.

Each source guard is a falsifiable premise about our shipped Python (a module is
never imported, a binary is never invoked) or about the runtime image base. The
scanner reads only our own tree: it parses every ``.py`` under the shipped source
dirs once and inspects the last ``FROM`` of each Dockerfile, emitting a
``Violation`` the moment a premise is contradicted.
"""

import ast
import re
from pathlib import Path
from typing import assert_never

from vex_guards import repo
from vex_guards.descriptors import (
    BaseImageIsAlpine,
    BinaryNotInvoked,
    ModuleNotImported,
    SourceGuard,
    SubprocessDenies,
)
from vex_guards.violations import Violation


def _top_level(dotted: str) -> str:
    """The first segment of a dotted module name (``tarfile.sub`` -> ``tarfile``)."""
    return dotted.split(".", 1)[0]


def _dynamic_import_target(call: ast.Call) -> str | None:
    """The literal module name a dynamic import call names, or ``None``.

    Recognises ``importlib.import_module("x")`` (an attribute call), the bare-name
    alias ``import_module("x")`` (``from importlib import import_module``) and
    ``__import__("x")`` (a name call), but only when the first argument is a
    constant string.
    """
    func = call.func
    is_import_module = (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
        isinstance(func, ast.Name) and func.id == "import_module"
    )
    is_dunder_import = isinstance(func, ast.Name) and func.id == "__import__"
    if not (is_import_module or is_dunder_import):
        return None
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _imported_modules(tree: ast.AST) -> set[str]:
    """Top-level modules made reachable by ``import``, ``from`` and dynamic imports."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(_top_level(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                modules.add(_top_level(node.module))
        elif isinstance(node, ast.Call):
            target = _dynamic_import_target(node)
            if target is not None:
                modules.add(_top_level(target))
    return modules


def _string_words(tree: ast.AST) -> set[str]:
    """Whole words appearing in any string literal (so ``wget`` != ``wgets``)."""
    words: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            words.update(re.findall(r"\w+", node.value))
    return words


def _runtime_from(dockerfile: Path) -> str:
    """The text of the last ``FROM`` line (case-insensitive), or ``""`` if none."""
    last = ""
    for line in dockerfile.read_text().splitlines():
        if line.strip().lower().startswith("from "):
            last = line
    return last


def _rel(path: Path) -> str:
    return str(path.relative_to(repo.repo_root(), walk_up=True))


def _parse_sources(src_dirs: list[Path]) -> list[tuple[Path, ast.AST]]:
    parsed: list[tuple[Path, ast.AST]] = []
    for src_dir in src_dirs:
        for path in sorted(src_dir.rglob("*.py")):
            parsed.append((path, ast.parse(path.read_text(), filename=str(path))))
    return parsed


def _module_violation(
    cve: str, module: str, parsed: list[tuple[Path, ast.AST]]
) -> Violation | None:
    for path, tree in parsed:
        if module in _imported_modules(tree):
            return Violation(cve, f"module {module!r} is imported", _rel(path))
    return None


def _word_violation(cve: str, word: str, parsed: list[tuple[Path, ast.AST]]) -> Violation | None:
    for path, tree in parsed:
        if word in _string_words(tree):
            return Violation(cve, f"{word!r} appears in a string literal", _rel(path))
    return None


def _alpine_violations(cve: str, dockerfiles: list[Path]) -> list[Violation]:
    """One violation per Dockerfile whose last ``FROM`` is not an Alpine base.

    The claim is that EVERY runtime image is built on Alpine, so each Dockerfile
    is judged on its own last ``FROM``; a drifted image is flagged individually.
    An empty list is vacuously satisfied.
    """
    violations: list[Violation] = []
    for dockerfile in dockerfiles:
        runtime_from = _runtime_from(dockerfile)
        if "alpine" not in runtime_from.lower():
            rel = _rel(dockerfile)
            violations.append(
                Violation(cve, f"runtime base image of {rel} is not Alpine ({runtime_from!r})", rel)
            )
    return violations


def evaluate(
    guards: dict[str, SourceGuard],
    src_dirs: list[Path],
    dockerfiles: list[Path],
) -> list[Violation]:
    parsed = _parse_sources(src_dirs)
    violations: list[Violation] = []
    for cve, guard in guards.items():
        match guard:
            case ModuleNotImported(module=module):
                found = _module_violation(cve, module, parsed)
            case BinaryNotInvoked(name=name):
                found = _word_violation(cve, name, parsed)
            case SubprocessDenies(program=program):
                found = _word_violation(cve, program, parsed)
            case BaseImageIsAlpine():
                violations.extend(_alpine_violations(cve, dockerfiles))
                continue
            case _:  # pragma: no cover
                assert_never(guard)
        if found is not None:
            violations.append(found)
    return violations
