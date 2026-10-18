from __future__ import annotations

import asyncio
import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from wireforge import service
from wireforge.mcp_server import TOOL_NAMES, create_server

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"


def _scenario() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "agent_scenario", ROOT / "quality" / "agent_scenario.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(workspace: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = asyncio.run(create_server(workspace).call_tool(name, arguments))
    structured = getattr(result, "structured_content", None)
    assert isinstance(structured, dict)
    return structured


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    shutil.copytree(EXAMPLES / "02_multi_interface_avionics", root / "avionics")
    shutil.copytree(EXAMPLES / "02_multi_interface_avionics", root / "avionics_next")
    return root


def test_tools_are_listed_with_descriptions(workspace: Path) -> None:
    tools = asyncio.run(create_server(workspace).list_tools())
    assert [tool.name for tool in tools] == list(TOOL_NAMES)
    assert all(tool.description for tool in tools)
    schema = next(tool for tool in tools if tool.name == "upsert_struct").input_schema
    assert {"project", "section", "name", "definition", "dry_run"} <= set(schema["properties"])


def test_agent_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        del cwd
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "100% tests passed\n", "")

    monkeypatch.setattr(service, "_run", fake)
    scenario = _scenario()
    results = asyncio.run(scenario.run(tmp_path))
    assert {name: result["ok"] for name, result in results.items()} == dict.fromkeys(results, True)
    assert results["validate_project"]["issues"] == []
    files = results["generate_code"]["data"]["files"]
    assert "model.hpp" in files
    assert "generated_tests/CMakeLists.txt" in files
    assert len(commands) == 3, "configure, build, ctest"
    decoded = results["decode_hex"]["data"]
    assert decoded["message"] == "Position"
    sample = dict(results["encode_sample"]["data"]["value"])
    sample["nSats"] = len(sample["sats"])  # the count field is written from the array length
    assert decoded["value"] == sample
    assert scenario.main(["agent_scenario", str(tmp_path / "again")]) == 0


def test_reading_tools(workspace: Path) -> None:
    assert _call(workspace, "list_projects", {})["data"]["projects"] == [
        "avionics",
        "avionics_next",
    ]
    listed = _call(workspace, "list_messages", {"project": "avionics"})["data"]
    assert {item["name"] for item in listed["messages"]} == {
        "NavSolution",
        "NodeBStatus",
        "Command",
    }
    assert (
        _call(workspace, "get_message", {"project": "avionics", "name": "TrackRef"})["data"][
            "section"
        ]
        == "types"
    )
    layout = _call(
        workspace,
        "analyze_layout",
        {"project": "avionics", "profile": "platformB", "export": "csv"},
    )
    assert layout["text"].startswith("profile,owner")
    sample = _call(
        workspace,
        "encode_sample",
        {
            "project": "avionics",
            "message": "Command",
            "interface": "our_to_nodeC",
            "profile": "platformA",
        },
    )
    assert sample["ok"], "the custom checksum plugin is found"
    payload = _call(
        workspace,
        "decode_hex",
        {
            "project": "avionics",
            "hex_bytes": sample["data"]["payload"],
            "message": "Command",
            "profile": "platformA",
        },
    )
    assert payload["ok"]


def test_writing_tools_and_compat(workspace: Path) -> None:
    dry = _call(
        workspace,
        "upsert_struct",
        {
            "project": "avionics_next",
            "section": "messages",
            "name": "Command",
            "definition": {
                "id": 0x0301,
                "description": "Command sent to nodeC.",
                "fields": [
                    {"name": "target", "type": "u8", "min": 1, "max": 8, "default": 1},
                    {"name": "opcode", "type": "u16"},
                    {
                        "name": "sum",
                        "type": "u16",
                        "checksum": {"use": "vendorSum", "covers": {"scope": "preceding"}},
                    },
                ],
            },
            "dry_run": True,
        },
    )
    assert "max: 8" in dry["data"]["diff"]
    assert dry["data"]["written"] is False
    _call(
        workspace,
        "upsert_entry",
        {"project": "avionics_next", "section": "defines", "name": "MAX_TRACKS", "definition": 9},
    )
    report = _call(
        workspace, "compat_check", {"project": "avionics_next", "old_project": "avionics"}
    )
    assert report["ok"], "a define default used by profiles changes no profile layout"
    edited = _call(
        workspace,
        "upsert_entry",
        {
            "project": "avionics_next",
            "section": "profiles",
            "name": "platformB",
            "definition": {"defines": {"PLATFORM_B": 1, "MAX_TRACKS": 6, "CHANNELS": 5}},
        },
    )
    assert edited["ok"]
    report = _call(
        workspace, "compat_check", {"project": "avionics_next", "old_project": "avionics"}
    )
    assert not report["ok"]
    git = shutil.which("git") or "git"
    for arguments in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(
            [git, "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
            check=True,
        )
    same = _call(workspace, "compat_check", {"project": "avionics", "revision": "HEAD"})
    assert same["ok"]


def test_errors_are_tool_errors(workspace: Path) -> None:
    server = create_server(workspace)
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("validate_project", {"project": "missing"}, "no project"),
        ("validate_project", {"project": "../x"}, "not a project"),
        (
            "upsert_struct",
            {"project": "avionics", "section": "enums", "name": "E", "definition": {}},
            "section",
        ),
        (
            "upsert_entry",
            {"project": "avionics", "section": "nope", "name": "E", "definition": {}},
            "section",
        ),
        ("get_message", {"project": "avionics", "name": "Nope"}, "no message"),
        ("analyze_layout", {"project": "avionics", "export": "pdf"}, "export"),
        ("compat_check", {"project": "avionics"}, "old_project"),
        ("decode_hex", {"project": "avionics", "hex_bytes": "zz", "message": "Command"}, "not hex"),
        (
            "encode_sample",
            {"project": "avionics", "message": "Nope", "profile": "platformA"},
            "no message",
        ),
        ("create_project", {"project": "avionics", "name": "x"}, "already exists"),
    ]
    for name, arguments, text in cases:
        with pytest.raises(ToolError, match=text):
            asyncio.run(server.call_tool(name, arguments))


def test_wireshark_dissector(workspace: Path) -> None:
    result = _call(workspace, "wireshark_dissector", {"project": "avionics"})
    assert result["ok"]
    plugin = Path(result["data"]["output"])
    assert plugin == workspace / ".wireforge" / "generated" / "wireshark" / "avionics.lua"
    assert plugin.read_text(encoding="utf-8").startswith("-- Wireshark dissector for avionics_demo")
    assert "avionics_demo_our_to_nodea" in result["data"]["protocols"]


def test_lifecycle_docs(workspace: Path) -> None:
    result = _call(workspace, "lifecycle_docs", {"project": "avionics"})
    assert result["ok"]
    assert "index.md" in result["data"]["files"]
    assert (workspace / ".wireforge" / "docs" / "avionics" / "index.md").exists()


def test_lifecycle_docs_with_results_only_below_the_output(
    workspace: Path, make_results: Callable[..., Path]
) -> None:
    inside = make_results(workspace / ".wireforge" / "generated" / "run")
    result = _call(workspace, "lifecycle_docs", {"project": "avionics", "results_dir": str(inside)})
    assert result["ok"]
    report = workspace / ".wireforge" / "docs" / "avionics" / "test_report.md"
    assert "Runtime headers" in report.read_text(encoding="utf-8")
    outside = make_results(workspace / "elsewhere")
    with pytest.raises(ToolError, match="below the workspace output"):
        _call(workspace, "lifecycle_docs", {"project": "avionics", "results_dir": str(outside)})


def test_requirements_tools(workspace: Path) -> None:
    sample = Path(__file__).resolve().parents[2] / "examples" / "02_multi_interface_avionics"
    copy = workspace / "srs_sample.reqif"
    (workspace / "avionics" / "requirements" / "hlr.yaml").unlink()
    shutil.copy(sample / "requirements" / "srs_sample.reqif", copy)
    arguments = {"project": "avionics", "path": str(copy), "baseline": "B1"}
    result = _call(workspace, "requirements_import", arguments)
    assert result["data"]["requirements"] == 5
    locked = _call(workspace, "requirements_lock", {"project": "avionics"})
    assert locked["ok"]
    assert (workspace / "avionics" / "llr_ids.lock").exists()
    with pytest.raises(ToolError, match="inside the workspace"):
        _call(workspace, "requirements_import", {**arguments, "path": str(sample / "project.yaml")})


def test_create_project_from_a_template(workspace: Path) -> None:
    result = _call(
        workspace,
        "create_project",
        {"project": "first", "name": "first_link", "template": "raw"},
    )
    assert result["ok"]
    assert "Packet" in (workspace / "first" / "project.yaml").read_text(encoding="utf-8")
