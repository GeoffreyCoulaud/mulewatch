import json
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from packaging.version import Version

from vex_guards.descriptors import ImageGuard, PackageAbsent, PackageMinVersion
from vex_guards.violations import Violation


@dataclass(frozen=True)
class DebPackage:
    name: str
    version: str


def load_dpkg_packages(path: Path) -> list[DebPackage]:
    """The dpkg-installed packages of a Syft JSON SBOM, in document order.

    Syft types a Debian package "deb". Everything else in the SBOM (the aMule nix
    closure, our Python wheels) is another cataloger's output, not dpkg's, so no
    dpkg-scoped guard has anything to say about it.
    """
    doc = json.loads(path.read_text())
    return [
        DebPackage(name=a["name"], version=a["version"])
        for a in doc["artifacts"]
        if a["type"] == "deb"
    ]


def _upstream(version: str) -> str:
    """The upstream part of a Debian ``[epoch:]upstream[-revision]`` version.

    The epoch and the Debian revision are packaging metadata, not upstream code, so
    a minimum-version claim is only ever about the middle part. Stripping them also
    keeps the result parseable: "1:8.14.1-2" is not a valid PEP 440 version.

    ponytail: PEP 440 ordering over a Debian upstream version. Close enough for the
    numeric upstreams we ship; a "~rc1" upstream would raise InvalidVersion. Swap in
    a real Debian comparator the day a min-version guard actually needs one.
    """
    _, _, without_epoch = version.rpartition(":")
    upstream, _, _ = without_epoch.rpartition("-")
    return upstream or without_epoch


def evaluate_image_guards(
    guards: dict[str, ImageGuard], packages: list[DebPackage]
) -> list[Violation]:
    by_name: dict[str, list[DebPackage]] = {}
    for pkg in packages:
        by_name.setdefault(pkg.name, []).append(pkg)
    violations: list[Violation] = []
    for cve, guard in guards.items():
        match guard:
            case PackageAbsent(package):
                if package in by_name:
                    violations.append(
                        Violation(cve, f"dpkg package {package!r} is present", "security")
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
