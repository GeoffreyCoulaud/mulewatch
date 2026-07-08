"""CLI gate: flag VEX ``not_affected`` claims Grype no longer reports.

A ``not_affected`` claim asserts a fact about a live vulnerability. Once Grype
stops reporting that vulnerability for the image, the claim has outlived its
subject and should be pruned. Given a Syft SBOM (scanned by Grype) and the VEX
document authored for that image, this lists every claimed CVE that Grype no
longer reports. In ``fail`` mode a stale entry prints a GitHub ``::error::``
annotation and exits non-zero; in ``sarif`` mode the same entries are written to
``--output`` as a SARIF report (always exit zero).
"""

import argparse
import json
import sys
from pathlib import Path

from vex_guards import repo
from vex_guards.grype import GrypeRunner, SubprocessGrypeRunner
from vex_guards.sarif import build_sarif
from vex_guards.vex_io import load_claims
from vex_guards.violations import Violation

_RULE_ID = "stale-vex-entry"


def main(argv: list[str] | None = None, runner: GrypeRunner | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom", required=True, help="Syft JSON SBOM of the built image")
    parser.add_argument("--vex", required=True, help="OpenVEX document authored for the image")
    parser.add_argument("--format", choices=("fail", "sarif"), default="fail")
    parser.add_argument("--output", help="SARIF output path (required by --format sarif)")
    args = parser.parse_args(argv)

    runner = runner or SubprocessGrypeRunner()
    reported = runner.run(Path(args.sbom))
    vex_relpath = repo.display_path(Path(args.vex))
    stale = [
        Violation(cve, "no longer reported by Grype", vex_relpath)
        for cve in load_claims(Path(args.vex))
        if cve not in reported
    ]

    if args.format == "sarif":
        Path(args.output).write_text(json.dumps(build_sarif(_RULE_ID, stale, vex_relpath)))
        return 0

    for v in stale:
        print(f"::error::{v.cve}: {v.message} ({v.location})")
    return 1 if stale else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
