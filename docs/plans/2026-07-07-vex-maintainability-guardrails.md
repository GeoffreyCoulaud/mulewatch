# VEX maintainability guardrails: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development to
> implement this plan task by task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** author the 19 OpenVEX statements for both images and build a fourth workspace package
`vex_guards` holding four checks that keep those `not_affected` claims honest, wired into CI
(non-blocking PR job, SARIF in the daily scan, hard-fail at release).

**Architecture:** pure-data guard descriptors in a registry, one per VEX claim; four independent
check executables, one per cell of the 2x2 (repo/image x predicate/set). See spec
`docs/specs/2026-07-07-vex-maintainability-guardrails.md` for the full rationale.

**Tech stack:** Python 3.14, stdlib + `packaging` (semantic version compare), `ast` (import
detection), Syft-JSON SBOM parsing, Grype via an injectable subprocess runner.

## Global Constraints

Copied verbatim from the project gate and the spec; every task's requirements implicitly include
this section.

- **`mypy --strict`** over `src` AND `tests`; every test function annotated `-> None` with typed
  params. **`ruff`** selects `E,F,I,UP,B,SIM`, line length **100**.
- **100% branch coverage** for the `vex_guards` package, gated by its own
  `--cov-fail-under=100 --cov-branch`. TDD: write the failing test first, watch it fail, then the
  minimal implementation. Negative-path mandatory: every guard descriptor gets a positive test
  (current tree/SBOM passes) and a negative test (an injected violation fails).
- **English only** (identifiers and prose). Conventional commits (`feat(vex-guards):`, `test:`,
  `chore:`, `docs:`). **No em-dashes / en-dashes** anywhere, including comments and step labels.
- **The four live checks stay out of `poe check`.** Only the package's unit tests join the gate
  (via a fourth `poe test` entry). The Grype subprocess is behind an injectable runner so the
  suite reaches 100% with no Grype installed; its real `run` body carries `# pragma: no cover`.
- **The source scan excludes `packages/vex_guards/src`.** That tree is tooling and legitimately
  contains the excluded names (`"wget"`, `"tarfile"`, ...) as data; scanning it would false-positive.
- **`security/*.vex.openvex.json` stay standard OpenVEX v0.2.0.** No guard metadata is added to
  them; the registry is the only place claims map to guards.

---

## Task 1: Scaffold the `vex_guards` package and register it in the workspace

**Files:**
- Create: `packages/vex_guards/pyproject.toml`, `packages/vex_guards/src/vex_guards/__init__.py`,
  `packages/vex_guards/tests/conftest.py` (empty marker), `packages/vex_guards/tests/test_smoke.py`
- Modify: root `pyproject.toml` (workspace source, dev group, ruff `src`, mypy `files`, `poe test`)
- Modify: `packages/crawler/Dockerfile`, `packages/verifier/Dockerfile` (bind-mount)
- Modify: `AGENTS.md` (three packages to four)

**Interfaces:**
- Produces: an importable `vex_guards` package installed into the dev environment; a fourth
  `poe test` entry; both Dockerfiles able to `uv sync --locked` with the new member present.

- [ ] **Step 1: `packages/vex_guards/pyproject.toml`** mirroring an existing package's shape:

```toml
[project]
name = "vex-guards"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["packaging>=24"]

[build-system]
requires = ["uv_build>=0.9"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "vex_guards"
module-root = "src"

[tool.pytest.ini_options]
addopts = "--cov=vex_guards --cov-branch --cov-report=term-missing --cov-fail-under=100"
testpaths = ["tests"]

[tool.coverage.run]
branch = true
source = ["vex_guards"]
```

(Confirm the `[build-system]` and `[tool.uv.build-backend]` keys against
`packages/matching/pyproject.toml` and copy that project's exact backend to avoid drift.)

- [ ] **Step 2: register in root `pyproject.toml`.** Add to `[tool.uv.sources]`
  `vex-guards = { workspace = true }`; add `"vex-guards"` to `[dependency-groups].dev`; append
  `"packages/vex_guards/src"` and `"packages/vex_guards/tests"` to both `[tool.ruff].src` and
  `[tool.mypy].files`; add a fourth entry to `[tool.poe.tasks.test].sequence`:
  `{ cmd = "pytest", cwd = "packages/vex_guards" }`.

- [ ] **Step 3: bind-mount in both Dockerfiles.** In each builder's first
  `uv sync --locked --no-install-workspace` RUN, add
  `--mount=type=bind,source=packages/vex_guards/pyproject.toml,target=packages/vex_guards/pyproject.toml`
  next to the existing member mounts. Add a short comment: the tooling package is never installed
  (not named in any `uv sync --package`); the mount only satisfies `uv sync --locked`, which needs
  every member's `pyproject.toml`. Removing it via `uv sync --frozen` is a tracked follow-up.

- [ ] **Step 4: `AGENTS.md`.** Change "three packages" to "four packages" in the "What this is"
  paragraph and add a one-line row/mention that `packages/vex_guards/` is dev/CI tooling (not
  shipped), reading `security/*.vex.openvex.json`.

- [ ] **Step 5: smoke test** `packages/vex_guards/tests/test_smoke.py`:

```python
def test_package_imports() -> None:
    import vex_guards

    assert vex_guards.__name__ == "vex_guards"
```

- [ ] **Step 6:** `uv sync --dev` (regenerates `uv.lock` with the new member), then
  `( cd packages/vex_guards && uv run pytest )` passes; `uv run poe lint-all` passes.

- [ ] **Step 7: commit** `chore(vex-guards): scaffold tooling package and register in workspace`.

> Dockerfile builds are validated later by the compose integration suite (real Docker, run by the
> operator), not in this task's gate.

---

## Task 2: Core types (descriptors, `family`, `Violation`) and the registry

**Files:**
- Create: `packages/vex_guards/src/vex_guards/descriptors.py`,
  `packages/vex_guards/src/vex_guards/violations.py`,
  `packages/vex_guards/src/vex_guards/registry.py`
- Test: `packages/vex_guards/tests/test_descriptors.py`, `tests/test_registry.py`

**Interfaces:**
- Produces: `Guard` union, `family(guard) -> Literal["source", "image"]`,
  `JUSTIFICATION_BY_FAMILY`, `Violation`, `GUARDS: dict[str, Guard]`.

- [ ] **Step 1: failing tests** `test_descriptors.py`:

```python
from typing import get_args

from vex_guards.descriptors import (
    JUSTIFICATION_BY_FAMILY,
    BaseImageIsAlpine,
    BinaryNotInvoked,
    Guard,
    ImageGuard,
    ModuleNotImported,
    PackageAbsent,
    PackageMinVersion,
    SourceGuard,
    SubprocessDenies,
    family,
)


def test_source_descriptors_report_source_family() -> None:
    for guard in (
        ModuleNotImported("tarfile"),
        BinaryNotInvoked("wget"),
        SubprocessDenies("ffmpeg"),
        BaseImageIsAlpine(),
    ):
        assert family(guard) == "source"


def test_image_descriptors_report_image_family() -> None:
    for guard in (PackageAbsent("nghttp2"), PackageMinVersion("clamav", "0.99")):
        assert family(guard) == "image"


def test_justification_by_family_is_closed() -> None:
    assert JUSTIFICATION_BY_FAMILY == {
        "source": "vulnerable_code_not_in_execute_path",
        "image": "vulnerable_code_not_present",
    }


def test_guard_union_covers_both_families() -> None:
    assert set(get_args(Guard)) == set(get_args(SourceGuard)) | set(get_args(ImageGuard))
```

- [ ] **Step 2: implement `descriptors.py`:**

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ModuleNotImported:
    module: str


@dataclass(frozen=True)
class BinaryNotInvoked:
    name: str


@dataclass(frozen=True)
class SubprocessDenies:
    program: str


@dataclass(frozen=True)
class BaseImageIsAlpine:
    pass


@dataclass(frozen=True)
class PackageAbsent:
    package: str


@dataclass(frozen=True)
class PackageMinVersion:
    package: str
    minimum: str


SourceGuard = ModuleNotImported | BinaryNotInvoked | SubprocessDenies | BaseImageIsAlpine
ImageGuard = PackageAbsent | PackageMinVersion
Guard = SourceGuard | ImageGuard

Family = Literal["source", "image"]

JUSTIFICATION_BY_FAMILY: dict[Family, str] = {
    "source": "vulnerable_code_not_in_execute_path",
    "image": "vulnerable_code_not_present",
}


def family(guard: Guard) -> Family:
    match guard:
        case ModuleNotImported() | BinaryNotInvoked() | SubprocessDenies() | BaseImageIsAlpine():
            return "source"
        case PackageAbsent() | PackageMinVersion():
            return "image"
```

- [ ] **Step 3: `violations.py`:**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    cve: str
    message: str
    location: str  # repo-relative path pointing at the offending file
```

- [ ] **Step 4: failing test** `test_registry.py` (encodes the design; both sides of the map):

```python
from vex_guards.descriptors import (
    BaseImageIsAlpine,
    BinaryNotInvoked,
    ModuleNotImported,
    PackageAbsent,
    PackageMinVersion,
    SubprocessDenies,
    family,
)
from vex_guards.registry import GUARDS


def test_registry_has_the_eleven_advisories() -> None:
    assert set(GUARDS) == {
        "CVE-2026-11940",
        "CVE-2026-11972",
        "CVE-2026-4360",
        "CVE-2026-0864",
        "CVE-2025-15366",
        "CVE-2025-15367",
        "CVE-2026-12003",
        "CVE-2025-60876",
        "CVE-2016-1405",
        "CVE-2026-58055",
        "GHSA-cq8v-f236-94qc",
    }


def test_tarfile_cves_share_the_module_guard() -> None:
    for cve in ("CVE-2026-11940", "CVE-2026-11972", "CVE-2026-4360"):
        assert GUARDS[cve] == ModuleNotImported("tarfile")


def test_image_family_guards() -> None:
    assert GUARDS["CVE-2026-58055"] == PackageAbsent("nghttp2")
    assert GUARDS["CVE-2016-1405"] == PackageMinVersion("clamav", "0.99")
    assert family(GUARDS["CVE-2026-58055"]) == "image"
```

- [ ] **Step 5: implement `registry.py`** exactly as spec section 4 (all 11 entries).

- [ ] **Step 6:** suite green at 100%; `commit feat(vex-guards): descriptors, violation type, registry`.

---

## Task 3: Repo paths, VEX loader, and the 19 authored statements

**Files:**
- Create: `packages/vex_guards/src/vex_guards/repo.py`,
  `packages/vex_guards/src/vex_guards/vex_io.py`
- Modify (author): `security/crawler.vex.openvex.json`, `security/verifier.vex.openvex.json`
- Test: `tests/test_repo.py`, `tests/test_vex_io.py`

**Interfaces:**
- Produces: `repo.repo_root()`, `repo.source_dirs()` (the shipped packages, excluding
  `vex_guards`), `repo.dockerfiles()`, `repo.vex_files() -> dict[str, Path]`;
  `vex_io.load_claims(path) -> dict[str, str]` (cve to justification, `not_affected` only),
  `vex_io.all_claims(paths) -> dict[str, str]`.

- [ ] **Step 1: failing `test_repo.py`** (uses the real tree):

```python
from vex_guards import repo


def test_source_dirs_are_the_shipped_packages_only() -> None:
    names = {p.parent.name for p in repo.source_dirs()}
    assert names == {"crawler", "verifier", "matching"}
    assert "vex_guards" not in names


def test_vex_files_point_at_security_dir() -> None:
    files = repo.vex_files()
    assert set(files) == {"crawler", "verifier"}
    assert files["verifier"].name == "verifier.vex.openvex.json"
    assert files["verifier"].is_file()
```

- [ ] **Step 2: implement `repo.py`:**

```python
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]


def repo_root() -> Path:
    return _ROOT


def source_dirs() -> list[Path]:
    return sorted(
        d for d in (_ROOT / "packages").glob("*/src") if d.parent.name != "vex_guards"
    )


def dockerfiles() -> list[Path]:
    return [_ROOT / "packages" / name / "Dockerfile" for name in ("crawler", "verifier")]


def vex_files() -> dict[str, Path]:
    return {
        image: _ROOT / "security" / f"{image}.vex.openvex.json"
        for image in ("crawler", "verifier")
    }
```

- [ ] **Step 3: author `security/crawler.vex.openvex.json`** (OpenVEX v0.2.0, 8 statements) and
  `security/verifier.vex.openvex.json` (11 statements) per spec section 3: `@context`, `@id`
  (pointing at each file), `author`, `timestamp` (2026-07-08), `version: 1`, `statements[]` each
  with `vulnerability: {"name": "<CVE>"}`, `products: [{"@id": "pkg:oci/mulewatch-<image>",
  "subcomponents": [{"@id": "<versionless purl>"}, ...]}]`, `status: "not_affected"`,
  `justification`, `impact_statement` (the concrete reason from spec section 3). Match the schema
  `vexctl` emits so a later `vexctl add` appends cleanly.

- [ ] **Step 4: failing `test_vex_io.py`** with a small fixture VEX plus the real files:

```python
import json
from pathlib import Path

from vex_guards import repo
from vex_guards.vex_io import all_claims, load_claims


def _write(tmp_path: Path, statements: list[dict[str, object]]) -> Path:
    doc = {"@context": "https://openvex.dev/ns/v0.2.0", "statements": statements}
    path = tmp_path / "x.vex.openvex.json"
    path.write_text(json.dumps(doc))
    return path


def test_load_claims_keeps_not_affected_only(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "vulnerability": {"name": "CVE-1"},
                "status": "not_affected",
                "justification": "vulnerable_code_not_present",
            },
            {"vulnerability": {"name": "CVE-2"}, "status": "affected"},
        ],
    )
    assert load_claims(path) == {"CVE-1": "vulnerable_code_not_present"}


def test_all_claims_merges_and_agrees_on_shared_cves() -> None:
    claims = all_claims(list(repo.vex_files().values()))
    assert claims["CVE-2026-11940"] == "vulnerable_code_not_in_execute_path"
    assert claims["CVE-2016-1405"] == "vulnerable_code_not_present"
```

Add a negative test: two files disagreeing on the justification of a shared CVE raises
`ValueError`.

- [ ] **Step 5: implement `vex_io.py`:**

```python
import json
from pathlib import Path


def load_claims(path: Path) -> dict[str, str]:
    doc = json.loads(path.read_text())
    claims: dict[str, str] = {}
    for statement in doc["statements"]:
        if statement["status"] != "not_affected":
            continue
        claims[statement["vulnerability"]["name"]] = statement["justification"]
    return claims


def all_claims(paths: list[Path]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in paths:
        for cve, justification in load_claims(path).items():
            existing = merged.get(cve)
            if existing is not None and existing != justification:
                raise ValueError(f"{cve} has conflicting justifications across VEX files")
            merged[cve] = justification
    return merged
```

- [ ] **Step 6:** suite green; `commit feat(vex-guards): repo paths, VEX loader, author the 19 statements`.

---

## Task 4: SBOM loader and image-guard evaluation

**Files:**
- Create: `packages/vex_guards/src/vex_guards/sbom.py`
- Test: `tests/test_sbom.py`

**Interfaces:**
- Consumes: `ImageGuard`, `Violation`.
- Produces: `sbom.ApkPackage` (name, version), `sbom.load_apk_packages(path) -> list[ApkPackage]`,
  `sbom.evaluate_image_guards(guards: dict[str, ImageGuard], packages) -> list[Violation]`.

- [ ] **Step 1: failing `test_sbom.py`** with a crafted syft-json fixture. Cases:
  `load_apk_packages` returns only `type == "apk"` artifacts with name+version;
  `PackageAbsent("nghttp2")` passes when only `nghttp2-libs` present, fails when `nghttp2` present;
  `PackageMinVersion("clamav", "0.99")` passes for `1.4.4-r0`, fails for `0.98-r0`, passes
  vacuously when no `clamav` present. Assert each returned `Violation.cve` and a message naming the
  package.

- [ ] **Step 2: implement `sbom.py`:**

```python
import json
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version

from vex_guards.descriptors import ImageGuard, PackageAbsent, PackageMinVersion
from vex_guards.violations import Violation


@dataclass(frozen=True)
class ApkPackage:
    name: str
    version: str


def load_apk_packages(path: Path) -> list[ApkPackage]:
    doc = json.loads(path.read_text())
    return [
        ApkPackage(name=a["name"], version=a["version"])
        for a in doc["artifacts"]
        if a["type"] == "apk"
    ]


def _upstream(version: str) -> str:
    return version.split("-r")[0]


def evaluate_image_guards(
    guards: dict[str, ImageGuard], packages: list[ApkPackage]
) -> list[Violation]:
    by_name: dict[str, list[ApkPackage]] = {}
    for pkg in packages:
        by_name.setdefault(pkg.name, []).append(pkg)
    violations: list[Violation] = []
    for cve, guard in guards.items():
        match guard:
            case PackageAbsent(package):
                if package in by_name:
                    violations.append(
                        Violation(cve, f"apk package {package!r} is present", "security")
                    )
            case PackageMinVersion(package, minimum):
                for pkg in by_name.get(package, []):
                    if Version(_upstream(pkg.version)) < Version(minimum):
                        violations.append(
                            Violation(
                                cve,
                                f"{package} {pkg.version} is below {minimum}",
                                "security",
                            )
                        )
    return violations
```

(The `location` is refined to the image's VEX path by the caller in Task 9.)

- [ ] **Step 3:** suite green; `commit feat(vex-guards): syft-json SBOM loader and image-guard evaluation`.

---

## Task 5: Grype runner (injectable) and JSON parsing

**Files:**
- Create: `packages/vex_guards/src/vex_guards/grype.py`
- Test: `tests/test_grype.py`

**Interfaces:**
- Produces: `grype.GrypeRunner` (Protocol, `run(sbom_path: Path) -> set[str]`),
  `grype.parse_grype_json(text: str) -> set[str]`, `grype.SubprocessGrypeRunner`.

- [ ] **Step 1: failing `test_grype.py`:** `parse_grype_json` on a fixture with two matches
  returns the two vulnerability ids (deduped). A `FakeGrypeRunner` satisfying the Protocol returns
  a preset set. (The Protocol stub method body must be one line: `def run(...) -> set[str]: ...`.)

```python
from pathlib import Path

from vex_guards.grype import GrypeRunner, parse_grype_json


def test_parse_grype_json_collects_unique_ids() -> None:
    text = (
        '{"matches": ['
        '{"vulnerability": {"id": "CVE-1"}},'
        '{"vulnerability": {"id": "CVE-1"}},'
        '{"vulnerability": {"id": "GHSA-x"}}]}'
    )
    assert parse_grype_json(text) == {"CVE-1", "GHSA-x"}


class _Fake:
    def run(self, sbom_path: Path) -> set[str]:
        return {"CVE-1"}


def test_fake_satisfies_protocol() -> None:
    runner: GrypeRunner = _Fake()
    assert runner.run(Path("x")) == {"CVE-1"}
```

- [ ] **Step 2: implement `grype.py`:**

```python
import json
import subprocess
from pathlib import Path
from typing import Protocol


class GrypeRunner(Protocol):
    def run(self, sbom_path: Path) -> set[str]: ...


def parse_grype_json(text: str) -> set[str]:
    doc = json.loads(text)
    return {match["vulnerability"]["id"] for match in doc["matches"]}


class SubprocessGrypeRunner:
    def run(self, sbom_path: Path) -> set[str]:  # pragma: no cover - integration only
        completed = subprocess.run(
            ["grype", f"sbom:{sbom_path}", "-o", "json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            check=True,
            text=True,
        )
        return parse_grype_json(completed.stdout)
```

- [ ] **Step 3:** suite green; `commit feat(vex-guards): injectable Grype runner and JSON parser`.

---

## Task 6: SARIF emitter

**Files:**
- Create: `packages/vex_guards/src/vex_guards/sarif.py`
- Test: `tests/test_sarif.py`

**Interfaces:**
- Consumes: `Violation`.
- Produces: `sarif.build_sarif(rule_id: str, violations: list[Violation], vex_relpath: str) -> dict`.

- [ ] **Step 1: failing `test_sarif.py`:** an empty `violations` list yields a valid SARIF 2.1.0
  document with `runs[0].tool.driver.name == "vex-consistency"`, the rule present in
  `tool.driver.rules`, and `runs[0].results == []`. A one-violation list yields one result with
  `ruleId == rule_id`, `level == "error"`, the message mentioning the CVE, and the location URI
  equal to `vex_relpath`.

- [ ] **Step 2: implement `sarif.py`:**

```python
from vex_guards.violations import Violation


def build_sarif(rule_id: str, violations: list[Violation], vex_relpath: str) -> dict[str, object]:
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "vex-consistency",
                        "rules": [{"id": rule_id}],
                    }
                },
                "results": [
                    {
                        "ruleId": rule_id,
                        "level": "error",
                        "message": {"text": f"{v.cve}: {v.message}"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": vex_relpath}
                                }
                            }
                        ],
                    }
                    for v in violations
                ],
            }
        ],
    }
```

- [ ] **Step 3:** suite green; `commit feat(vex-guards): SARIF 2.1.0 emitter`.

---

## Task 7: `check_source_claims` (source scan + CLI)

**Files:**
- Create: `packages/vex_guards/src/vex_guards/source_scan.py`,
  `packages/vex_guards/src/vex_guards/check_source_claims.py`
- Test: `tests/test_source_scan.py`, `tests/test_check_source_claims.py`

**Interfaces:**
- Consumes: `SourceGuard`, `Violation`, `repo`, `registry.GUARDS`, `descriptors.family`.
- Produces: `source_scan.evaluate(guards: dict[str, SourceGuard], src_dirs: list[Path],
  dockerfiles: list[Path]) -> list[Violation]`; `check_source_claims.main() -> int`.

- [ ] **Step 1: failing `test_source_scan.py`** with synthetic trees (one temp dir per case):
  - `ModuleNotImported("tarfile")`: a file with `import tarfile` fails; `from tarfile import x`
    fails; `import tarfile as t` fails; `importlib.import_module("tarfile")` fails; a file with no
    such import passes; a file whose only mention is the string `"tarfile"` in a non-import context
    passes.
  - `BinaryNotInvoked("wget")`: a file containing `subprocess.run(["wget", url])` fails; a file
    with the word only inside `"wgets"` passes (word boundary).
  - `SubprocessDenies("ffmpeg")`: `["ffmpeg", ...]` fails; `["ffprobe", ...]` passes.
  - `BaseImageIsAlpine()`: a Dockerfile whose last `FROM` contains `alpine` passes; one ending in
    `FROM python:3.14-slim` fails.
  - Then: `evaluate` against the **real** `repo.source_dirs()` + `repo.dockerfiles()` with the real
    source-family subset of `GUARDS` returns `[]` (proves our tree is clean).

- [ ] **Step 2: implement `source_scan.py`.** Core helpers:
  - `_imported_modules(tree: ast.AST) -> set[str]`: walk `ast.Import` / `ast.ImportFrom` (top-level
    module of dotted names) and `ast.Call` to `importlib.import_module` / `__import__` with a
    constant string first arg.
  - `_string_words(tree: ast.AST) -> set[str]`: collect whole words (`re.findall(r"\w+", ...)` over
    every `ast.Constant` str) for `BinaryNotInvoked` / `SubprocessDenies`.
  - `_runtime_from(dockerfile: Path) -> str`: the last `FROM ...` line.
  - `evaluate` dispatches per descriptor with a `match`, parsing each `.py` under `src_dirs` once,
    and returns `Violation(cve, message, <repo-relative file>)` on the first offending file.

- [ ] **Step 3: implement `check_source_claims.py`:**

```python
import sys

from vex_guards.descriptors import SourceGuard, family
from vex_guards.registry import GUARDS
from vex_guards.repo import dockerfiles, source_dirs
from vex_guards.source_scan import evaluate


def main() -> int:
    guards: dict[str, SourceGuard] = {
        cve: g for cve, g in GUARDS.items() if family(g) == "source"  # type: ignore[misc]
    }
    violations = evaluate(guards, source_dirs(), dockerfiles())
    for v in violations:
        print(f"::error::{v.cve}: {v.message} ({v.location})")
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

  (Resolve the `family`-narrowing so mypy is satisfied without the `type: ignore` if practical, e.g.
  an `is_source_guard` `TypeGuard`.)

- [ ] **Step 4: `test_check_source_claims.py`:** `main()` returns 0 on the real repo; and, using a
  `monkeypatch` of `source_dirs` to a temp tree containing `import tarfile`, `main()` returns 1 and
  prints an `::error::` line naming the CVE.

- [ ] **Step 5:** suite green at 100%; `commit feat(vex-guards): check_source_claims`.

---

## Task 8: `check_claim_coverage` (CLI)

**Files:**
- Create: `packages/vex_guards/src/vex_guards/check_claim_coverage.py`
- Test: `tests/test_check_claim_coverage.py`

**Interfaces:**
- Consumes: `GUARDS`, `family`, `JUSTIFICATION_BY_FAMILY`, `vex_io.all_claims`, `repo.vex_files`.
- Produces: `check_claim_coverage.main() -> int`.

- [ ] **Step 1: failing tests.** With `all_claims` / `GUARDS` injected via `monkeypatch`:
  - a claim with no guard fails (message names the CVE, "no guard");
  - a guard with no claim fails ("no claim");
  - a family mismatch (image guard vs `vulnerable_code_not_in_execute_path`) fails;
  - the real registry against the real VEX returns 0 (proves authored VEX and registry agree). This
    is the TDD moment: if Task 3's VEX and Task 2's registry disagree, this test is red until they
    match.

- [ ] **Step 2: implement `check_claim_coverage.py`:**

```python
import sys

from vex_guards.descriptors import JUSTIFICATION_BY_FAMILY, family
from vex_guards.registry import GUARDS
from vex_guards.repo import vex_files
from vex_guards.vex_io import all_claims


def main() -> int:
    claims = all_claims(list(vex_files().values()))
    problems: list[str] = []
    for cve in set(claims) - set(GUARDS):
        problems.append(f"{cve}: claim has no guard")
    for cve in set(GUARDS) - set(claims):
        problems.append(f"{cve}: guard has no claim")
    for cve in set(claims) & set(GUARDS):
        expected = JUSTIFICATION_BY_FAMILY[family(GUARDS[cve])]
        if claims[cve] != expected:
            problems.append(f"{cve}: justification {claims[cve]!r} does not match guard family")
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 3:** suite green; `commit feat(vex-guards): check_claim_coverage`.

---

## Task 9: `check_image_claims` (CLI)

**Files:**
- Create: `packages/vex_guards/src/vex_guards/check_image_claims.py`
- Test: `tests/test_check_image_claims.py`

**Interfaces:**
- Consumes: `GUARDS`, `family`, `vex_io.load_claims`, `sbom.load_apk_packages`,
  `sbom.evaluate_image_guards`, `sarif.build_sarif`.
- Produces: `check_image_claims.main(argv: list[str] | None = None) -> int`. Args:
  `--sbom PATH --vex PATH [--format {fail,sarif}] [--output PATH]` (default `fail`).

- [ ] **Step 1: failing tests** driving `main` with `argv`:
  - `--vex` = a fixture verifier VEX (has `CVE-2026-58055`, `CVE-2016-1405`), `--sbom` a fixture
    with `nghttp2` present: `--format fail` returns 1 and prints the CVE; a clean SBOM returns 0.
  - scoping: a crawler VEX (no image-family CVE) with any SBOM returns 0 (no image guards apply).
  - `--format sarif --output f`: writes a SARIF doc whose results match the violations; a clean run
    writes an empty-results SARIF and returns 0.

- [ ] **Step 2: implement `check_image_claims.py`.** Load the image's claims; select
  `{cve: GUARDS[cve] for cve in claims if cve in GUARDS and family(GUARDS[cve]) == "image"}`;
  `evaluate_image_guards` against `load_apk_packages(sbom)`; refine each `Violation.location` to the
  `--vex` path (repo-relative); then either print `::error::` lines + return `1 if violations else 0`
  (fail mode) or write `build_sarif("unsatisfied-image-claim", violations, vex_relpath)` to
  `--output` and return 0 (sarif mode). Use `argparse`; `if __name__` guard `# pragma: no cover`.

- [ ] **Step 3:** suite green at 100%; `commit feat(vex-guards): check_image_claims`.

---

## Task 10: `check_stale_claims` (CLI)

**Files:**
- Create: `packages/vex_guards/src/vex_guards/check_stale_claims.py`
- Test: `tests/test_check_stale_claims.py`

**Interfaces:**
- Consumes: `vex_io.load_claims`, `grype.GrypeRunner` / `SubprocessGrypeRunner`, `sarif.build_sarif`.
- Produces: `check_stale_claims.main(argv=None, runner: GrypeRunner | None = None) -> int`. Args:
  `--sbom PATH --vex PATH [--format {fail,sarif}] [--output PATH]`.

- [ ] **Step 1: failing tests** injecting a `FakeGrypeRunner`:
  - claims `{CVE-A, CVE-B}`, runner reports `{CVE-A}`: `CVE-B` is stale; `--format fail` returns 1
    naming `CVE-B`; when the runner reports both, returns 0.
  - `--format sarif --output f` writes a SARIF with rule `stale-vex-entry`; empty when nothing stale.
  - default runner is `SubprocessGrypeRunner` when `runner is None` (assert the type without calling
    `run`, so no Grype needed).

- [ ] **Step 2: implement `check_stale_claims.py`.** `runner = runner or SubprocessGrypeRunner()`;
  `reported = runner.run(sbom)`; `stale = [Violation(cve, "no longer reported by Grype", vex_relpath)
  for cve in load_claims(vex) if cve not in reported]`; fail/sarif output as in Task 9 with rule
  `stale-vex-entry`. `argparse`; `if __name__` guard `# pragma: no cover`.

- [ ] **Step 3:** suite green at 100%; `commit feat(vex-guards): check_stale_claims`.

---

## Task 11: poe tasks, CI wiring, and SECURITY.md

**Files:**
- Modify: root `pyproject.toml` (`[tool.poe.tasks]`), `.github/workflows/pr.yml`,
  `.github/workflows/grype-scan.yml`, `.github/workflows/release.yml`, `SECURITY.md`

- [ ] **Step 1: poe tasks (NOT in `poe check`).** Add
  `vex-source-claims = { cmd = "python -m vex_guards.check_source_claims", help = "..." }` and
  `vex-claim-coverage = { cmd = "python -m vex_guards.check_claim_coverage", help = "..." }`. Do
  **not** add them to the `check` or `lint-all` sequences.

- [ ] **Step 2: `pr.yml`.** Add a `vex-checks` job beside `validate`: `runs-on: ubuntu-latest`,
  `permissions: { contents: read }`, steps = checkout (pinned SHA), the repo's uv setup (mirror
  what `validate.yml` uses, pinned), `uv sync --dev`, then two named steps
  `- name: VEX source claims hold` / `run: uv run poe vex-source-claims` and
  `- name: Every VEX claim is guarded` / `run: uv run poe vex-claim-coverage`. Leave branch
  protection unchanged so only `validate / gate` is required.

- [ ] **Step 3: `grype-scan.yml`.** Add `actions/checkout` (pinned) and uv setup; install the Grype
  CLI on PATH (pinned installer) for the stale check. After the existing "Extract SBOM and VEX"
  step, add two steps: `check_image_claims --sbom /tmp/sbom.syft.json --vex /tmp/vex.openvex.json
  --format sarif --output /tmp/image-claims.sarif` and the same for `check_stale_claims` to
  `/tmp/stale.sarif`; then two `upload-sarif` steps (pinned) with `category:
  vex-image-claims-${{ matrix.package }}` and `vex-stale-claims-${{ matrix.package }}`. Keep the
  existing Grype SARIF upload with `if: always()`. The two new steps must not fail the job.

- [ ] **Step 4: `release.yml`.** In `publish-manifest`, add uv setup + Grype CLI install; after the
  Syft-JSON SBOM step and **before** the attest step, four hard-fail steps: `uv run poe
  vex-source-claims`, `uv run poe vex-claim-coverage`, `check_image_claims --sbom /tmp/sbom.syft.json
  --vex security/${{ matrix.package }}.vex.openvex.json --format fail`, and `check_stale_claims`
  with the same paths. Any non-zero exit stops the job before `cosign attest`.

- [ ] **Step 5: `SECURITY.md`.** Add a short subsection under the triage section: the four checks,
  what each catches, and where each runs (PR job, daily SARIF, release hard-fail). No em-dashes.

- [ ] **Step 6:** `uv run poe lint-all` + `template-check` pass; commit
  `ci(vex-guards): wire the four checks into pr, grype-scan, and release`.

> The workflow logic is exercised only by real CI (OIDC, a pushed image); it cannot run locally.
> Section 12 of the spec lists the post-merge confirmation points.

---

## Task 12: Empirical suppression validation, full gate, holistic review

- [ ] **Step 1: prove the VEX suppresses on the real images.** For each image: generate the
  Syft-JSON SBOM (`docker run anchore/syft <image> -o syft-json`), run
  `grype sbom:<sbom> --vex security/<image>.vex.openvex.json -o json` and confirm the flagged CVEs
  are suppressed. If a base PURL fails to match (Grype wants the qualified form), adjust the
  subcomponent PURLs in the VEX and re-run `check_claim_coverage`. Record the outcome. (Run by the
  operator or the main agent with Docker; not a subagent unit test.)
- [ ] **Step 2: run `check_image_claims` and `check_stale_claims` locally** against a real SBOM +
  the repo VEX (both `--format fail`) and confirm exit 0, as a live end-to-end check of the two
  image checks before CI ever runs them.
- [ ] **Step 3: full gate.** `uv run poe check` green (the `vex_guards` suite now runs as the fourth
  `poe test` entry at 100% branch coverage; ruff + mypy span the new package).
- [ ] **Step 4: holistic whole-branch review** (per subagent-driven-development): dispatch the final
  code reviewer over the whole diff. Fix Critical/Important findings.
- [ ] **Step 5:** proceed to Wrap (handoff recording the `--frozen` Dockerfile follow-up and the
  post-merge CI confirmation points; PR; tag `v0.31.0-vex-guardrails`).
