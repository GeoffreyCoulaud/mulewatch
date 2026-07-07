import json
from pathlib import Path

from vex_guards.descriptors import ImageGuard, PackageAbsent, PackageMinVersion
from vex_guards.sbom import ApkPackage, evaluate_image_guards, load_apk_packages


def _write_sbom(tmp_path: Path, artifacts: list[dict[str, str]]) -> Path:
    doc = {"artifacts": artifacts}
    path = tmp_path / "sbom.syft.json"
    path.write_text(json.dumps(doc))
    return path


def test_load_apk_packages_keeps_only_apk_artifacts(tmp_path: Path) -> None:
    path = _write_sbom(
        tmp_path,
        [
            {"type": "apk", "name": "clamav", "version": "1.4.4-r0"},
            {"type": "apk", "name": "nghttp2-libs", "version": "1.64.0-r0"},
            {"type": "python", "name": "packaging", "version": "24.0"},
        ],
    )
    assert load_apk_packages(path) == [
        ApkPackage("clamav", "1.4.4-r0"),
        ApkPackage("nghttp2-libs", "1.64.0-r0"),
    ]


def test_package_absent_passes_when_only_sibling_package_present() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2024-0001": PackageAbsent("nghttp2")}
    packages = [ApkPackage("nghttp2-libs", "1.64.0-r0")]
    assert evaluate_image_guards(guards, packages) == []


def test_package_absent_fails_when_package_present() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2024-0001": PackageAbsent("nghttp2")}
    packages = [ApkPackage("nghttp2", "1.64.0-r0")]
    violations = evaluate_image_guards(guards, packages)
    assert len(violations) == 1
    assert violations[0].cve == "CVE-2024-0001"
    assert "nghttp2" in violations[0].message


def test_package_min_version_passes_at_or_above_minimum() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2016-1405": PackageMinVersion("clamav", "0.99")}
    packages = [ApkPackage("clamav", "1.4.4-r0")]
    assert evaluate_image_guards(guards, packages) == []


def test_package_min_version_fails_below_minimum() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2016-1405": PackageMinVersion("clamav", "0.99")}
    packages = [ApkPackage("clamav", "0.98-r0")]
    violations = evaluate_image_guards(guards, packages)
    assert len(violations) == 1
    assert violations[0].cve == "CVE-2016-1405"
    assert "clamav" in violations[0].message


def test_package_min_version_passes_vacuously_when_package_absent() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2016-1405": PackageMinVersion("clamav", "0.99")}
    packages = [ApkPackage("nghttp2-libs", "1.64.0-r0")]
    assert evaluate_image_guards(guards, packages) == []
