from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from wireforge import cli, golden, service, wireshark
from wireforge.codec import CodecError, ReferenceCodec, WireNaN
from wireforge.loader import load_project
from wireforge.test_cases import MUTATIONS

if TYPE_CHECKING:
    from collections.abc import Iterator

    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
BASIC = EXAMPLES / "01_basic_telemetry"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"
EDGE = EXAMPLES / "03_edge_cases"
CASES = Path(__file__).resolve().parent / "wireshark"
PLUGINS = golden.load_plugins(AVIONICS / "plugins" / "vendor_sum.py")
# The avionics example's custom checksum, as a Lua hook (16-bit sum of all bytes).
VENDOR_SUM_HOOK = """
_G.wireforge_checksums = _G.wireforge_checksums or {}
_G.wireforge_checksums["vendor::SumChecksum"] = function(data)
  local sum = 0
  for index = 1, #data do sum = (sum + data:byte(index)) % 65536 end
  return sum
end
"""
UDP_PORT = 9999
TCP_PORT = 9998
SEVERITY_WARN = 0x00600000
LOCAL_TSHARK = ROOT / "build" / "tshark-jammy" / "tshark"


def _project(location: Path) -> Project:
    result = load_project(location)
    assert result.project is not None
    return result.project


# --- generator ----------------------------------------------------------------------------------


def test_protocol_names_and_fields() -> None:
    project = _project(AVIONICS)
    data = wireshark.model(project)
    names = [protocol["name"] for protocol in data["protocols"]]
    assert names == [
        "avionics_demo_nodeb_to_our",
        "avionics_demo_our_to_nodea",
        "avionics_demo_our_to_nodec",
    ]
    node_a = data["protocols"][1]
    assert node_a["profiles"] == ["platformA", "platformB"]
    specs = {spec["abbr"]: spec for spec in node_a["fields"]}
    # lsb_first: `valid` is bit 0 of the NavFlags byte, `mode` bits 1..3.
    assert specs["avionics_demo_our_to_nodea.NavSolution.flags.valid"]["mask"] == 0x01
    assert specs["avionics_demo_our_to_nodea.NavSolution.flags.mode"]["mask"] == 0x0E
    assert specs["avionics_demo_our_to_nodea.NavSolution.alt"]["label"] == "alt [m]"
    # `extra` exists only on platformB; the fields are the union of all profiles.
    assert "avionics_demo_our_to_nodea.NavSolution.extra" in specs
    platform_a, platform_b = node_a["layouts"]
    assert platform_a["max_frame_size"] == 128
    assert platform_b["max_frame_size"] == 192
    tracks_a = next(f for f in platform_a["messages"][0]["fields"] if f.get("name") == "tracks")
    assert tracks_a["occurs"] == {
        "max": 3,
        "min": 0,
        "count": ["field", "trackCount"],
        "dynamic": True,
    }
    assert platform_a["sync"] == [{"offset": 0, "bytes": b"\xa5\x5a"}]
    assert platform_a["length_field"] == {"name": "length", "offset": 5, "width": 2}


def test_count_expressions_fold_constants() -> None:
    data = wireshark.model(_project(CASES))
    (layout,) = data["protocols"][1]["layouts"]
    sample = next(item for item in layout["messages"] if item["name"] == "Sample")
    values = next(item for item in sample["fields"] if item.get("name") == "values")
    assert values["occurs"]["count"] == ["binary", "*", ["field", "pairs"], 2]


def test_lua_rendering() -> None:
    assert wireshark.lua_table({"a": [1, True], "end": 'x"\\\n', 3: -1.5}) == (
        '{ a = { 1, true }, ["end"] = "x\\"\\\\\\010", [3] = -1.5 }'
    )
    assert wireshark.lua_table([float("inf"), b"\xa5Z"]) == '{ math.huge, "\\165Z" }'
    long = wireshark.lua_table({"items": list(range(40))})
    assert long.startswith("{\n  items = {\n    0,\n")
    with pytest.raises(TypeError):
        wireshark.lua_table({"x": None})


def test_generate_writes_the_plugin(tmp_path: Path) -> None:
    target = tmp_path / "out" / "basic.lua"
    plugin = wireshark.generate(BASIC, target)
    assert plugin.issues == []
    assert plugin.protocols == ("basic_telemetry_sensor_to_us",)
    text = target.read_text(encoding="utf-8")
    assert text.startswith("-- Wireshark dissector for basic_telemetry 0.1.0.")
    assert "local MODEL = {" in text
    assert text.rstrip().endswith("for _, spec in ipairs(MODEL.protocols) do register(spec) end")
    assert wireshark.main([str(BASIC), "-o", str(target)]) == 0


def _write_project(directory: Path, interfaces: str) -> Path:
    directory.mkdir()
    (directory / "project.yaml").write_text(
        "schema_version: 1\nproject: demo\n"
        "messages:\n  Ping:\n    id: 1\n    fields:\n      - { name: n, type: u8 }\n"
        f"interfaces:\n{interfaces}",
        encoding="utf-8",
    )
    return directory


def _interface(name: str, message: str = "Ping") -> str:
    return (
        f"  {name}:\n    version: 1.0.0\n    direction: rx\n    header: none\n"
        f"    framing: {{ kind: none }}\n    messages: [{message}]\n"
    )


def test_generate_reports_problems(tmp_path: Path) -> None:
    project = _write_project(tmp_path / "bad", _interface("link", "Missing"))
    target = tmp_path / "x.lua"
    plugin = wireshark.generate(project, target)
    assert plugin.issues
    assert plugin.protocols == ()
    assert not target.exists()
    assert wireshark.main([str(project), "-o", str(target)]) == 1


def test_protocol_name_collisions_are_errors(tmp_path: Path) -> None:
    project = _write_project(tmp_path / "twins", _interface("Link") + _interface("link"))
    issues = wireshark.generate(project, tmp_path / "x.lua").issues
    assert len(issues) == 1
    assert "interfaces Link and link map to the same protocol name demo_link" in issues[0].message


def test_service_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result, text = service.wireshark_plugin(AVIONICS)
    assert result.ok
    assert result.data["protocols"] == [
        "avionics_demo_nodeb_to_our",
        "avionics_demo_our_to_nodea",
        "avionics_demo_our_to_nodec",
    ]
    assert text.startswith("-- Wireshark dissector for avionics_demo 2.1.0.")
    target = tmp_path / "plugins" / "avionics.lua"
    assert cli.main(["wireshark", str(AVIONICS), "-o", str(target)]) == cli.EXIT_OK
    assert target.read_text(encoding="utf-8") == text
    assert "avionics_demo_our_to_nodea" in capsys.readouterr().out
    broken = _write_project(tmp_path / "broken", _interface("link", "Missing"))
    assert cli.main(["wireshark", str(broken), "-o", str(tmp_path / "x.lua")]) == cli.EXIT_FAILED
    assert not (tmp_path / "x.lua").exists()


# --- tshark harness -----------------------------------------------------------------------------


def _tshark() -> Path | None:
    """Locate tshark: $WIREFORGE_TSHARK (required to exist), the PATH or quality/fetch_tshark.sh."""
    configured = os.environ.get("WIREFORGE_TSHARK")
    if configured:
        return Path(configured)
    found = shutil.which("tshark")
    if found:
        return Path(found)
    return LOCAL_TSHARK if LOCAL_TSHARK.exists() else None


TSHARK = _tshark()
needs_tshark = pytest.mark.skipif(
    TSHARK is None, reason="tshark not found (install it or run quality/fetch_tshark.sh)"
)


def _ethernet_ip(protocol: int, transport: bytes) -> bytes:
    ip = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(transport),
        0,
        0,
        64,
        protocol,
        0,
        bytes([10, 0, 0, 1]),
        bytes([10, 0, 0, 2]),
    )
    return b"\x00" * 12 + b"\x08\x00" + ip + transport


def udp_packet(payload: bytes) -> bytes:
    return _ethernet_ip(17, struct.pack(">HHHH", 40000, UDP_PORT, 8 + len(payload), 0) + payload)


def tcp_packet(payload: bytes, sequence: int) -> bytes:
    flags = 0x18  # PSH, ACK
    header = struct.pack(">HHIIBBHHH", 40001, TCP_PORT, sequence, 1, 5 << 4, flags, 65535, 0, 0)
    return _ethernet_ip(6, header + payload)


def write_pcap(path: Path, packets: list[bytes]) -> None:
    with path.open("wb") as out:
        out.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for index, packet in enumerate(packets):
            out.write(struct.pack("<IIII", index, 0, len(packet), len(packet)) + packet)


def _pairs(items: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """JSON object hook that keeps repeated keys (tshark repeats them for array elements)."""
    return items


@dataclass
class Dissection:
    """What tshark shows of our protocol in one packet."""

    values: dict[str, list[str]] = field(default_factory=dict)
    statuses: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    frames: int = 0


def _walk(node: list[tuple[str, Any]], prefix: str, result: Dissection) -> None:
    for key, value in node:
        if key == "_ws.expert" and isinstance(value, list):
            info = dict(value)
            mine = any(name.startswith(prefix) for name, _ in value)
            text = str(info.get("_ws.expert.message", ""))
            severe = int(str(info.get("_ws.expert.severity", "0"))) >= SEVERITY_WARN
            if mine and severe:
                result.problems.append(text)
                if text.startswith("["):
                    result.statuses.append(text[1 : text.index("]")])
            continue
        if isinstance(value, list):
            _walk(value, prefix, result)
        elif key.startswith(prefix) and isinstance(value, str):
            result.values.setdefault(key, []).append(value)


def run_tshark(
    tmp_path: Path, plugin: Path, protocol: str, packets: list[bytes], prefs: list[str]
) -> list[Dissection]:
    assert TSHARK is not None
    capture = tmp_path / f"{protocol}.pcap"
    write_pcap(capture, packets)
    hook = tmp_path / "hooks.lua"
    hook.write_text(VENDOR_SUM_HOOK, encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    env = {**os.environ, "WIRESHARK_CONFIG_DIR": str(config), "HOME": str(tmp_path)}
    command = [str(TSHARK), "-n", "-r", str(capture), "-T", "json"]
    command += ["-X", f"lua_script:{hook}", "-X", f"lua_script:{plugin}"]
    for pref in prefs:
        command += ["-o", f"{protocol}.{pref}"]
    done = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert done.returncode == 0, done.stderr
    assert "Lua" not in done.stderr, done.stderr
    result = []
    for packet in json.loads(done.stdout, object_pairs_hook=_pairs):
        dissection = Dissection()
        for name, value in _layers(packet):
            if name == protocol:
                dissection.frames += any(key == f"{protocol}.header" for key, _ in value)
                _walk(value, protocol + ".", dissection)
            elif name.startswith("Lua Error"):
                dissection.problems.append(name)
        result.append(dissection)
    return result


def _layers(packet: list[tuple[str, Any]]) -> Iterator[tuple[str, Any]]:
    source = dict(packet)["_source"]
    yield from dict(source)["layers"]


# --- expected values ----------------------------------------------------------------------------


def _flatten(value: object, path: str, out: dict[str, list[object]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(item, f"{path}.{key}", out)
    elif isinstance(value, list):
        for item in value:
            _flatten(item, path, out)
    else:
        out.setdefault(path, []).append(value)


def _same(project: Project, expected: object, shown: str) -> bool:
    if isinstance(expected, bool):
        return shown in {"1", "True"} if expected else shown in {"0", "False"}
    if isinstance(expected, WireNaN) or (isinstance(expected, float) and math.isnan(expected)):
        return shown.lower() == "nan"
    if isinstance(expected, float):
        return math.isclose(float(shown), expected, rel_tol=1e-5, abs_tol=1e-30)
    if isinstance(expected, int):
        number = int(shown, 0)
        return number == expected or any(
            number == expected % (1 << bits) for bits in (8, 16, 32, 64)
        )
    assert isinstance(expected, str)
    members = {
        name: value for enum in project.enums.values() for name, value in enum.values.items()
    }
    if shown.lstrip("-").isdigit() and expected in members:
        return int(shown) == members[expected]
    return shown == expected.replace("\0", "\\0")


@dataclass(frozen=True)
class Case:
    """One frame sent in its own UDP datagram with the reference codec's verdict."""

    name: str
    frame: bytes
    status: str
    expected: dict[str, list[object]] | None = None


def _decoded(codec: ReferenceCodec, interface: str, frame: bytes, protocol: str) -> Case:
    try:
        decoded = codec.decode_frame(interface, frame)
    except CodecError as error:
        return Case("", frame, error.status)
    expected: dict[str, list[object]] = {}
    _flatten(decoded.header, f"{protocol}.header", expected)
    _flatten(decoded.value, f"{protocol}.{decoded.message}", expected)
    _flatten(decoded.trailer, f"{protocol}.trailer", expected)
    return Case("", frame, "Ok", expected)


def _cases(project: Project, profile: str | None, interface: str) -> list[Case]:
    codec = ReferenceCodec(project, profile, PLUGINS)
    protocol = wireshark.protocol_name(project, project.interfaces[interface])
    cases = []
    for vector in golden.vectors(project, PLUGINS):
        if vector["profile"] != profile or vector["interface"] != interface:
            continue
        frame = bytes.fromhex(vector["frame"])
        case = _decoded(codec, interface, frame, protocol)
        cases.append(Case(f"{vector['message']}/{vector['sample']}", frame, "Ok", case.expected))
        for index, original in enumerate(frame):
            for name, change in MUTATIONS:
                mutated = frame[:index] + bytes([change(original)]) + frame[index + 1 :]
                verdict = _decoded(codec, interface, mutated, protocol)
                label = f"{vector['message']}/{vector['sample']}/byte{index}_{name}"
                cases.append(Case(label, mutated, verdict.status, verdict.expected))
        for label, data in (("truncated", frame[:-1]), ("extended", frame + b"\x00")):
            verdict = _decoded(codec, interface, data, protocol)
            cases.append(
                Case(
                    f"{vector['message']}/{vector['sample']}/{label}",
                    data,
                    verdict.status,
                    verdict.expected,
                )
            )
    return cases


def _variants(project: Project) -> Iterator[tuple[str | None, str]]:
    profiles: list[str | None] = [*project.profiles] or [None]
    for profile in profiles:
        for interface in sorted(project.interfaces):
            yield profile, interface


EXAMPLE_VARIANTS = [
    pytest.param(location, profile, interface, id=f"{location.name}-{profile}-{interface}")
    for location in (BASIC, AVIONICS, EDGE, CASES)
    for profile, interface in _variants(_project(location))
]


def _prefs(profile: str | None, *, port: str = "udp_port", number: int = UDP_PORT) -> list[str]:
    prefs = [f"{port}:{number}"]
    if profile is not None:
        prefs.append(f"profile:{profile}")
    return prefs


@needs_tshark
@pytest.mark.parametrize(("location", "profile", "interface"), EXAMPLE_VARIANTS)
def test_tshark_matches_the_reference_codec(
    tmp_path: Path, location: Path, profile: str | None, interface: str
) -> None:
    """Golden frames show the reference values; every mutated frame is flagged iff it is bad."""
    project = _project(location)
    plugin = tmp_path / "plugin.lua"
    assert wireshark.generate(location, plugin).issues == []
    protocol = wireshark.protocol_name(project, project.interfaces[interface])
    cases = _cases(project, profile, interface)
    framing = project.interfaces[interface].framing.kind
    shown = run_tshark(
        tmp_path, plugin, protocol, [udp_packet(case.frame) for case in cases], _prefs(profile)
    )
    assert len(shown) == len(cases)
    failures = []
    for case, dissection in zip(cases, shown, strict=True):
        if case.status == "Ok":
            if dissection.problems:
                failures.append(f"{case.name}: unexpected {dissection.problems}")
            assert case.expected is not None
            for abbr, values in case.expected.items():
                got = dissection.values.get(abbr, [])
                if len(got) != len(values) or not all(
                    _same(project, want, have) for want, have in zip(values, got, strict=True)
                ):
                    failures.append(f"{case.name}: {abbr} shows {got}, expected {values}")
        elif not dissection.problems:
            failures.append(f"{case.name}: {case.status} not reported")
        elif framing == "none" and case.status not in dissection.statuses:
            failures.append(f"{case.name}: {case.status} not in {dissection.statuses}")
    assert failures == []


@needs_tshark
@pytest.mark.parametrize(("location", "profile", "interface"), EXAMPLE_VARIANTS)
def test_tshark_reassembles_tcp_streams(
    tmp_path: Path, location: Path, profile: str | None, interface: str
) -> None:
    """Frames split over TCP segments (and garbage before resync frames) are all dissected."""
    project = _project(location)
    item = project.interfaces[interface]
    if item.framing.kind == "none":
        pytest.skip("framing none: every read is one frame, there is no stream")
    plugin = tmp_path / "plugin.lua"
    wireshark.generate(location, plugin)
    protocol = wireshark.protocol_name(project, item)
    frames = [
        bytes.fromhex(vector["frame"])
        for vector in golden.vectors(project, PLUGINS)
        if vector["profile"] == profile and vector["interface"] == interface
    ]
    garbage = b"\x00\x13" if item.framing.resync else b""
    stream = garbage.join(frames)
    sizes = [1, 3, 7, 2, 11, 5]
    packets: list[bytes] = []
    position, sequence = 0, 1
    while position < len(stream):
        size = sizes[len(packets) % len(sizes)]
        packets.append(tcp_packet(stream[position : position + size], sequence))
        position += size
        sequence += size
    shown = run_tshark(
        tmp_path, plugin, protocol, packets, _prefs(profile, port="tcp_port", number=TCP_PORT)
    )
    dissected = sum(dissection.frames for dissection in shown)
    problems = [problem for dissection in shown for problem in dissection.problems]
    resyncs = [problem for problem in problems if problem.startswith("[Resync]")]
    assert [problem for problem in problems if not problem.startswith("[Resync]")] == []
    assert len(resyncs) == (len(frames) - 1 if garbage else 0)
    assert dissected == len(frames)
