"""CLI gate: assert every image-family VEX claim holds against a built image's SBOM.

Each image guard asserts a fact about the packages shipped in the image (a
package is absent, or meets a minimum version). Given a Syft SBOM and the VEX
document authored for that image, this checks the image-family claims only:
source-family claims are scoped out. In ``fail`` mode a breach prints a GitHub
``::error::`` annotation and exits non-zero; in ``sarif`` mode the same breaches
are written to ``--output`` as a SARIF report (always exit zero).
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from vex_guards import repo
from vex_guards.descriptors import ImageGuard, is_image_guard
from vex_guards.registry import GUARDS
from vex_guards.sarif import build_sarif
from vex_guards.sbom import evaluate_image_guards, load_apk_packages
from vex_guards.vex_io import load_claims

_RULE_ID = "unsatisfied-image-claim"


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo.repo_root()))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom", required=True, help="Syft JSON SBOM of the built image")
    parser.add_argument("--vex", required=True, help="OpenVEX document authored for the image")
    parser.add_argument("--format", choices=("fail", "sarif"), default="fail")
    parser.add_argument("--output", help="SARIF output path (required by --format sarif)")
    args = parser.parse_args(argv)

    claims = load_claims(Path(args.vex))
    # Iterate GUARDS so the value binds the narrowed loop variable: is_image_guard
    # is a TypeGuard, so ``guard`` types as ImageGuard here with no cast/ignore.
    guards: dict[str, ImageGuard] = {
        cve: guard for cve, guard in GUARDS.items() if cve in claims and is_image_guard(guard)
    }
    packages = load_apk_packages(Path(args.sbom))
    vex_relpath = _display_path(Path(args.vex))
    raw = evaluate_image_guards(guards, packages)
    violations = [dataclasses.replace(v, location=vex_relpath) for v in raw]

    if args.format == "sarif":
        Path(args.output).write_text(json.dumps(build_sarif(_RULE_ID, violations, vex_relpath)))
        return 0

    for v in violations:
        print(f"::error::{v.cve}: {v.message} ({v.location})")
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
