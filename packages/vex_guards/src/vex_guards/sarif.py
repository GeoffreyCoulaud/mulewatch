from vex_guards.violations import Violation


def build_sarif(rule_id: str, violations: list[Violation], vex_relpath: str) -> dict[str, object]:
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "vex-consistency",
                        "rules": [{"id": rule_id}],
                    }
                },
                "results": [
                    {
                        "ruleId": rule_id,
                        "level": "error",
                        "message": {"text": f"{v.cve}: {v.message}"},
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": vex_relpath}}}
                        ],
                    }
                    for v in violations
                ],
            }
        ],
    }
