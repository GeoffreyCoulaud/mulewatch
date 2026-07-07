"""The record a guard emits when its premise is falsified."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    cve: str
    message: str
    location: str  # repo-relative path pointing at the offending file
