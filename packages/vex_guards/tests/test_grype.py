from pathlib import Path

from vex_guards.grype import GrypeRunner, parse_grype_json


def test_parse_grype_json_collects_unique_ids() -> None:
    text = (
        '{"matches": ['
        '{"vulnerability": {"id": "CVE-1"}},'
        '{"vulnerability": {"id": "CVE-1"}},'
        '{"vulnerability": {"id": "GHSA-x"}}]}'
    )
    assert parse_grype_json(text) == {"CVE-1", "GHSA-x"}


class _Fake:
    def run(self, sbom_path: Path) -> set[str]:
        return {"CVE-1"}


def test_fake_satisfies_protocol() -> None:
    runner: GrypeRunner = _Fake()
    assert runner.run(Path("x")) == {"CVE-1"}
