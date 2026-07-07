"""The authoritative advisory to guard mapping.

Every OpenVEX ``not_affected`` claim we publish is anchored here to exactly one
guard descriptor. The keys are advisory identifiers (CVE or GHSA); the values are
the falsifiable premise each claim rests on.
"""

from vex_guards.descriptors import (
    BaseImageIsAlpine,
    BinaryNotInvoked,
    Guard,
    ModuleNotImported,
    PackageAbsent,
    PackageMinVersion,
    SubprocessDenies,
)

GUARDS: dict[str, Guard] = {
    "CVE-2026-11940": ModuleNotImported("tarfile"),
    "CVE-2026-11972": ModuleNotImported("tarfile"),
    "CVE-2026-4360": ModuleNotImported("tarfile"),
    "CVE-2026-0864": ModuleNotImported("configparser"),
    "CVE-2025-15366": ModuleNotImported("imaplib"),
    "CVE-2025-15367": ModuleNotImported("poplib"),
    "CVE-2025-60876": BinaryNotInvoked("wget"),
    "GHSA-cq8v-f236-94qc": SubprocessDenies("ffmpeg"),
    "CVE-2026-12003": BaseImageIsAlpine(),
    "CVE-2026-58055": PackageAbsent("nghttp2"),
    "CVE-2016-1405": PackageMinVersion("clamav", "0.99"),
}
