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
