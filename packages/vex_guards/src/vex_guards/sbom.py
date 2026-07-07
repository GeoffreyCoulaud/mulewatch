import json
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

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
            case _:  # pragma: no cover
                assert_never(guard)
    return violations
