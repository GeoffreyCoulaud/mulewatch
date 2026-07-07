"""Guard descriptors, their two families, and the ``family`` classifier.

A guard is a small frozen dataclass that names one falsifiable premise behind a
VEX ``not_affected`` claim (e.g. "``tarfile`` is never imported"). Guards split
into two families: ``source`` guards assert something about our own code path,
``image`` guards assert something about the packages present in the image.
"""

from dataclasses import dataclass
from typing import Literal, assert_never


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
        case _:  # pragma: no cover
            assert_never(guard)
