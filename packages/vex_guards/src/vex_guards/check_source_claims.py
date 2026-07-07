"""CLI gate: assert every source-family VEX claim still holds against our tree.

Exits non-zero (and prints a GitHub ``::error::`` annotation per breach) if any
source guard's premise is contradicted by the shipped source or the image base.
"""

import sys

from vex_guards.descriptors import SourceGuard, is_source_guard
from vex_guards.registry import GUARDS
from vex_guards.repo import dockerfiles, source_dirs
from vex_guards.source_scan import evaluate


def main() -> int:
    guards: dict[str, SourceGuard] = {cve: g for cve, g in GUARDS.items() if is_source_guard(g)}
    violations = evaluate(guards, source_dirs(), dockerfiles())
    for v in violations:
        print(f"::error::{v.cve}: {v.message} ({v.location})")
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
