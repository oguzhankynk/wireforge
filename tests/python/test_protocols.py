"""The library of published protocols: every model starts a working project."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from wireforge import cli, resources, service
from wireforge.loader import load_project
from wireforge.server.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

PROTOCOLS = service.protocols()


def test_the_library() -> None:
    assert {"modbus_rtu", "modbus_tcp"} <= set(PROTOCOLS)
    assert not set(PROTOCOLS) & set(service.templates())


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_a_protocol_has_a_readme_with_its_source(protocol: str) -> None:
    readme = (resources.protocols_dir() / protocol / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# ")
    assert "Source" in readme
    assert "Not covered" in readme


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_a_protocol_makes_a_valid_project(tmp_path: Path, protocol: str) -> None:
    result = service.init_project(tmp_path / "demo", protocol, "my_link")
    assert result.ok, result.text
    assert result.data["kind"] == "protocol"
    project = load_project(tmp_path / "demo").project
    assert project is not None
    assert project.name == "my_link"
    original = load_project(resources.protocols_dir() / protocol).project
    assert original is not None
    assert project.namespace == original.namespace
    assert (tmp_path / "demo" / "README.md").exists()


def test_the_namespace_and_the_version_can_be_replaced(tmp_path: Path) -> None:
    result = service.init_project(tmp_path / "x", "modbus_tcp", "gateway", "acme::modbus", "2.0.0")
    assert result.ok, result.text
    project = load_project(tmp_path / "x").project
    assert project is not None
    assert (project.namespace, project.version) == ("acme::modbus", "2.0.0")


def test_an_unknown_name_lists_the_protocols(tmp_path: Path) -> None:
    with pytest.raises(service.ServiceError, match="modbus_rtu"):
        service.init_project(tmp_path / "x", "nope")


def test_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init", str(tmp_path / "a"), "--protocol", "modbus_rtu"]) == 0
    assert "from the protocol 'modbus_rtu'" in capsys.readouterr().out
    both = ["init", str(tmp_path / "b"), "--protocol", "modbus_rtu", "--template", "raw"]
    assert cli.main(both) == cli.EXIT_ERROR
    assert cli.main(["init", str(tmp_path / "c"), "--protocol", "nope"]) == cli.EXIT_ERROR


def test_the_command_line_finds_the_checksum_plugin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A protocol with a custom checksum works without --plugins: the project has its plugin."""
    project = str(tmp_path / "drone")
    assert cli.main(["init", project, "--protocol", "mavlink2"]) == 0
    capsys.readouterr()
    assert cli.main(["encode", project, "CommandAck", "{command: 400}", "--interface", "link"]) == 0
    # The command (0x0190) is sent, the zero result and the extensions are not: two payload bytes.
    assert capsys.readouterr().out.startswith("fd0200000001014d00009001")


def test_a_project_from_a_protocol_over_the_api(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    body = {"id": "bus", "name": "bus", "template": "modbus_rtu"}
    assert client.post("/api/projects", json=body).status_code == 201
    assert (tmp_path / "bus" / "vectors").is_dir()
