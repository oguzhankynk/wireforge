from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from wireforge import cli, resources, versioning
from wireforge.server import app as server_app

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_resources_come_from_the_repository_or_the_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert resources.schema_dir() == ROOT / "schema"
    assert (resources.runtime_include() / "wireforge" / "status.hpp").exists()
    assert (resources.cmake_dir() / "WireForgeConfig.cmake").exists()
    packaged = tmp_path / "_data"
    (packaged / "schema").mkdir(parents=True)
    (packaged / "ui").mkdir()
    monkeypatch.setattr(resources, "PACKAGE_DATA", packaged)
    assert resources.schema_dir() == packaged / "schema"
    assert resources.runtime_include() == ROOT / "runtime" / "include"
    assert resources.ui_dir() is None, "no index.html"
    (packaged / "ui" / "index.html").write_text(
        "<!doctype html><title>WireForge</title>", encoding="utf-8"
    )
    assert resources.ui_dir() == packaged / "ui"


def test_versions_agree() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'\nversion = "{versioning.TOOL_VERSION}"\n' in pyproject
    cmake = (ROOT / "cmake" / "package" / "WireForgeConfigVersion.cmake").read_text(
        encoding="utf-8"
    )
    match = re.search(r'set\(PACKAGE_VERSION "([^"]+)"\)', cmake)
    assert match is not None
    assert match.group(1) == versioning.TOOL_VERSION
    package = (ROOT / "ui" / "package.json").read_text(encoding="utf-8")
    assert f'"version": "{versioning.TOOL_VERSION}"' in package


def test_cli_version_and_cmake_dir(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["version"]) == cli.EXIT_OK
    assert capsys.readouterr().out == f"wireforge {versioning.TOOL_VERSION} (file format 1.0)\n"
    assert cli.main(["cmake-dir"]) == cli.EXIT_OK
    assert Path(capsys.readouterr().out.strip()) == resources.cmake_dir()


def test_server_serves_the_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>WireForge UI</title>", encoding="utf-8")
    monkeypatch.setattr(resources, "ui_dir", lambda: ui)
    client = TestClient(server_app.create_app(tmp_path))
    assert "WireForge UI" in client.get("/").text
    assert client.get("/api/health").json()["status"] == "ok", "the API still wins"
