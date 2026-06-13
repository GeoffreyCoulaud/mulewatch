import download_verifier


def test_package_is_importable() -> None:
    assert download_verifier.__name__ == "download_verifier"
