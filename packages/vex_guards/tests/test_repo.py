from vex_guards import repo


def test_repo_root_contains_the_packages_dir() -> None:
    assert (repo.repo_root() / "packages").is_dir()


def test_dockerfiles_are_the_two_image_dockerfiles() -> None:
    files = repo.dockerfiles()
    assert [p.parent.name for p in files] == ["crawler", "verifier"]
    assert all(p.name == "Dockerfile" and p.is_file() for p in files)


def test_source_dirs_are_the_shipped_packages_only() -> None:
    names = {p.parent.name for p in repo.source_dirs()}
    assert names == {"crawler", "verifier", "matching"}
    assert "vex_guards" not in names


def test_vex_files_point_at_security_dir() -> None:
    files = repo.vex_files()
    assert set(files) == {"crawler", "verifier"}
    assert files["verifier"].name == "verifier.vex.openvex.json"
    assert files["verifier"].is_file()
