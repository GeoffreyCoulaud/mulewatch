"""CLI gate: enforce a bijection between VEX ``not_affected`` claims and guards.

Every published claim must anchor to exactly one guard, every guard must back a
claim, and each claim's justification must match its guard's family. Any breach
prints a GitHub ``::error::`` annotation and exits non-zero.
"""

import sys

from vex_guards.descriptors import JUSTIFICATION_BY_FAMILY, family
from vex_guards.registry import GUARDS
from vex_guards.repo import vex_files
from vex_guards.vex_io import all_claims


def main() -> int:
    claims = all_claims(list(vex_files().values()))
    problems: list[str] = []
    for cve in set(claims) - set(GUARDS):
        problems.append(f"{cve}: claim has no guard")
    for cve in set(GUARDS) - set(claims):
        problems.append(f"{cve}: guard has no claim")
    for cve in set(claims) & set(GUARDS):
        expected = JUSTIFICATION_BY_FAMILY[family(GUARDS[cve])]
        if claims[cve] != expected:
            problems.append(f"{cve}: justification {claims[cve]!r} does not match guard family")
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
