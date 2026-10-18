from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from wireforge import cli, service
from wireforge.loader import load_project
from wireforge.server.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

TEMPLATES = service.templates()


def test_the_templates() -> None:
    assert TEMPLATES == ["command", "raw", "telemetry"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_template_makes_a_valid_project(tmp_path: Path, template: str) -> None:
    result = service.init_project(tmp_path / "demo", template)
    assert result.ok, result.text
    project = load_project(tmp_path / "demo").project
    assert project is not None
    assert project.name == "demo"
    assert project.namespace == "demo"
    assert project.messages
    assert "wireforge test" in result.text


def test_name_namespace_and_version_are_replaced(tmp_path: Path) -> None:
    result = service.init_project(tmp_path / "x", "command", "link_one", "acme::link", "2.3.4")
    assert result.ok, result.text
    project = load_project(tmp_path / "x").project
    assert project is not None
    assert (project.name, project.namespace, project.version) == ("link_one", "acme::link", "2.3.4")


def test_the_directory_name_becomes_the_project_name(tmp_path: Path) -> None:
    result = service.init_project(tmp_path / "my-link.v2")
    project = load_project(tmp_path / "my-link.v2").project
    assert result.ok
    assert project is not None
    assert project.name == "my_link_v2"
    assert service.init_project(tmp_path / "9lives").ok


def test_nothing_is_overwritten_and_bad_input_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "busy"
    directory.mkdir()
    (directory / "note.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(service.ServiceError, match="not an empty directory"):
        service.init_project(directory)
    assert (directory / "note.txt").read_text(encoding="utf-8") == "keep"
    with pytest.raises(service.ServiceError, match="unknown template"):
        service.init_project(tmp_path / "a", "nope")
    with pytest.raises(service.ServiceError, match="not a valid project name"):
        service.init_project(tmp_path / "b", "raw", "bad name")
    assert service.init_project(tmp_path / "empty_ok").ok
    empty = tmp_path / "empty"
    empty.mkdir()
    assert service.init_project(empty).ok


def test_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init", "--list"]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed == [*TEMPLATES, *(f"{name} (protocol)" for name in service.protocols())]
    assert cli.main(["init", str(tmp_path / "cli"), "--template", "raw", "--name", "pkt"]) == 0
    assert "created" in capsys.readouterr().out
    assert cli.main(["init", str(tmp_path / "cli")]) == cli.EXIT_ERROR


def test_a_project_from_a_template_over_the_api(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    body = {"id": "fresh", "name": "fresh_link", "template": "command"}
    created = client.post("/api/projects", json=body)
    assert created.status_code == 201
    assert "Command" in (tmp_path / "fresh" / "project.yaml").read_text(encoding="utf-8")
    assert client.post("/api/projects", json=body).status_code == 409
    missing = client.post("/api/projects", json={"id": "x", "name": "x", "template": "nope"})
    assert missing.status_code == 400


def test_the_templates_are_listed_with_a_summary(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    listed = client.get("/api/templates").json()
    assert [item["name"] for item in listed] == [*TEMPLATES, *service.protocols()]
    assert all(item["summary"] for item in listed)
    assert {item["kind"] for item in listed} == {"template", "protocol"}
