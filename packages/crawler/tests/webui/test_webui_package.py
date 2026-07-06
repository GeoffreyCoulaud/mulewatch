import mulewatch.webui


def test_package_is_importable() -> None:
    assert mulewatch.webui.__name__ == "mulewatch.webui"
