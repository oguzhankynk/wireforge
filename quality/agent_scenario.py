"""Agent scenario through the MCP server: ICD text -> messages -> validate -> generate -> tests.

An agent (here a deterministic parser in place of the language model) reads a short ICD, turns it
into WireForge definitions and drives the MCP tools: create_project, upsert_entry (checksum,
interface), upsert_struct (messages), validate_project, generate_code and run_tests.

`python quality/agent_scenario.py <workspace>` runs it with a real build of the generated tests;
tests/python/test_mcp.py runs it with the build replaced.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wireforge.mcp_server import create_server

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

ICD_TEXT = """\
Interface ground_link 1.0.0: rx, big endian, sync 0xEB90, CRC-32 over header and payload,
max frame 64 bytes. Header: sync u16, message id u8, payload length u8.
Message Heartbeat id 0x01: uptime u32 [s]; state u8 0..3; battery u16 [mV] 0..5000.
Message Position id 0x02: lat f64 [deg] -90..90; lon f64 [deg] -180..180; nSats u8 0..12;
sats u8 x 12 counted by nSats.
"""

FIELD = re.compile(
    r"(?P<name>\w+) (?P<type>[uif]\d+)(?: x (?P<count>\d+) counted by (?P<counter>\w+))?"
    r"(?: \[(?P<units>[^\]]+)\])?(?: (?P<min>-?\d+)\.\.(?P<max>-?\d+))?"
)
MESSAGE = re.compile(
    r"Message (?P<name>\w+) id (?P<id>0x[0-9A-Fa-f]+): (?P<fields>.+?)\.(?= Message|$)"
)


def _field(text: str) -> dict[str, Any]:
    match = FIELD.fullmatch(text.strip())
    if match is None:
        message = f"cannot read field {text!r}"
        raise ValueError(message)
    field: dict[str, Any] = {"name": match["name"], "type": match["type"]}
    if match["units"]:
        field["units"] = match["units"]
    if match["min"] is not None:
        field["min"] = int(match["min"])
        field["max"] = int(match["max"])
    if match["count"]:
        field["occurs"] = {"max": int(match["count"]), "count": match["counter"]}
    return field


def parse_icd(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Turn the ICD text into tool calls (tool name, arguments): what the agent decides."""
    flat = " ".join(text.split())
    calls: list[tuple[str, dict[str, Any]]] = [
        ("create_project", {"project": "ground", "name": "ground", "namespace": "demo::ground"}),
        (
            "upsert_entry",
            {"section": "checksums", "name": "frameCrc", "definition": {"algo": "crc32"}},
        ),
    ]
    messages = []
    for match in MESSAGE.finditer(flat):
        fields = [_field(part) for part in match["fields"].split(";")]
        messages.append(match["name"])
        definition = {"id": int(match["id"], 16), "fields": fields}
        calls.append(
            (
                "upsert_struct",
                {"section": "messages", "name": match["name"], "definition": definition},
            )
        )
    interface = {
        "version": "1.0.0",
        "direction": "rx",
        "endian": "big",
        "header": {
            "fields": [
                {"name": "sync", "type": "u16", "const": 0xEB90, "role": "sync"},
                {"name": "msgId", "type": "u8", "role": "message_id"},
                {"name": "length", "type": "u8", "role": "payload_length"},
            ]
        },
        "trailer": {
            "fields": [
                {
                    "name": "crc",
                    "type": "u32",
                    "checksum": {"use": "frameCrc", "covers": {"scope": "header_and_payload"}},
                }
            ]
        },
        "framing": {"kind": "sync_length", "max_frame_size": 64, "resync": True},
        "messages": messages,
    }
    calls.append(
        ("upsert_entry", {"section": "interfaces", "name": "ground_link", "definition": interface})
    )
    return calls


async def _call(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await server.call_tool(name, arguments)
    structured = getattr(result, "structured_content", None)
    if not isinstance(structured, dict):
        message = f"{name}: no structured result"
        raise TypeError(message)
    return structured


async def run(workspace: Path) -> dict[str, dict[str, Any]]:
    """Run the scenario; return the result of every step by name."""
    server = create_server(workspace)
    results: dict[str, dict[str, Any]] = {}
    for name, decided in parse_icd(ICD_TEXT):
        arguments = decided if name == "create_project" else {"project": "ground", **decided}
        results[f"{name}:{arguments.get('name', '')}"] = await _call(server, name, arguments)
    for name in ("validate_project", "generate_code", "run_tests"):
        results[name] = await _call(server, name, {"project": "ground"})
    results["encode_sample"] = await _call(
        server,
        "encode_sample",
        {"project": "ground", "message": "Position", "interface": "ground_link"},
    )
    frame = results["encode_sample"]["data"]["frame"]
    results["decode_hex"] = await _call(
        server, "decode_hex", {"project": "ground", "hex_bytes": frame, "interface": "ground_link"}
    )
    return results


def main(argv: list[str]) -> int:
    """Run the scenario in the workspace given on the command line."""
    workspace = Path(argv[1])
    workspace.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(run(workspace))
    failed = [name for name, result in results.items() if not result["ok"]]
    for name in failed:
        sys.stdout.write(f"FAILED {name}\n{json.dumps(results[name], indent=2)}\n")
    tests = results["run_tests"]["text"].strip().splitlines()
    sys.stdout.write(f"agent scenario: {len(results)} steps, {len(failed)} failed; {tests[-3:]}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
