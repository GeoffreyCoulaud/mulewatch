"""Child egress contract (analysis spec §4/§6 — DA6): DEFENSIVE parse parent-side.

``parse`` maps the child's outcome to ``(verdict, real_meta, checks)`` ALWAYS deterministically
(never propagates an exception — the service answers 200, §6). A child that times out, exits with
an error, exceeds the byte cap, or returns an unreadable/off-schema egress is a POISON signal →
``suspicious``. Strict schema: object ``{verdict ∈ {clean,suspicious,malicious}: str, real_meta:
obj, checks: list}``. Any deviation → ``suspicious``.

``classify_outcome`` returns the technical CATEGORY of the outcome (observability#2) — orthogonal
to the business verdict: ``ok`` (the child produced a valid egress), ``timeout`` (wall-clock
elapsed), ``nonzero_exit`` (the child crashed / exceeded an rlimit / exited != 0),
``egress_overflow`` (stdout exceeds the cap), ``malformed`` (unreadable / off-schema egress). In a
mass incident, we see ``suspicious`` rise as a business value AND the technical CAUSE (timeout,
crash, etc.).

DECISION (audit 2026-06-23 / error-boundary#3): an internal crash of a runner (ffprobe, clamav)
makes the child CRASH (returncode ≠ 0) instead of writing a clean ``suspicious`` JSON egress.
This is INTENDED: the ``returncode != 0 → suspicious`` mapping parent-side is the defense contract
(DA6 — a compromised child CANNOT lie if it does not control the returncode). The test
``test_nonzero_returncode_is_suspicious`` pins this boundary.
"""

import json
from typing import Literal

from download_verifier.checks.base import STATUS_RANK
from download_verifier.config import AnalysisConfig

ChildOutcome = Literal["ok", "timeout", "nonzero_exit", "egress_overflow", "malformed"]

_VALID_VERDICTS = frozenset(STATUS_RANK)


def _poison() -> tuple[str, dict[str, object], list[object]]:
    """Deterministic poison verdict (FRESH values → no shared mutation)."""
    return "suspicious", {}, []


def parse(
    stdout: bytes, returncode: int, timed_out: bool, cfg: AnalysisConfig
) -> tuple[str, dict[str, object], list[object]]:
    """Map the child egress to ``(verdict, real_meta, checks)`` (never raises)."""
    if timed_out or returncode != 0 or len(stdout) > cfg.egress_cap_bytes:
        return _poison()
    try:
        payload = json.loads(stdout)
    # RecursionError = defense-in-depth (cf. app.py §8); no dedicated test since json.loads
    # (C impl) does not recurse in CPython 3.12 — the except branch is covered by non-JSON cases.
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _poison()
    if not isinstance(payload, dict):
        return _poison()
    verdict = payload.get("verdict")
    real_meta = payload.get("real_meta")
    checks = payload.get("checks")
    if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
        return _poison()
    if not isinstance(real_meta, dict) or not isinstance(checks, list):
        return _poison()
    return verdict, real_meta, checks


def classify_outcome(
    stdout: bytes, returncode: int, timed_out: bool, cfg: AnalysisConfig
) -> ChildOutcome:
    """Technical category of the outcome (observability#2) — orthogonal to the verdict.

    Same defensive filters as ``parse`` (identical order: a healthy egress does not fall into an
    incident category). ``ok`` ⇔ ``parse`` would return the JSON's verdict; everything else is a
    distinct technical incident CAUSE to expose as a metric.
    """
    if timed_out:
        return "timeout"
    if returncode != 0:
        return "nonzero_exit"
    if len(stdout) > cfg.egress_cap_bytes:
        return "egress_overflow"
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return "malformed"
    if not isinstance(payload, dict):
        return "malformed"
    verdict = payload.get("verdict")
    real_meta = payload.get("real_meta")
    checks = payload.get("checks")
    if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
        return "malformed"
    if not isinstance(real_meta, dict) or not isinstance(checks, list):
        return "malformed"
    return "ok"
