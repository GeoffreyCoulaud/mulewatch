"""Read OpenVEX documents into a plain ``cve -> justification`` claim mapping.

Only ``not_affected`` statements carry a justification worth checking, so those
are the ones we keep. ``all_claims`` merges several documents and refuses to
paper over a shared CVE whose two files disagree on the justification.
"""

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
