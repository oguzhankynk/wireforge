import wireforge


def test_version_is_semver() -> None:
    parts = wireforge.__version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
