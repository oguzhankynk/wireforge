import filecmp
import shutil
import subprocess
from pathlib import Path

import pytest

from wireforge import bundle, generate, nodes, service, test_project, wireshark
from wireforge.loader import load_project
from wireforge.model import Project

ROOT = Path(__file__).parents[2]
EXAMPLES = sorted(path.parent for path in (ROOT / "examples").glob("*/project.yaml"))

SYSTEM = """\
schema_version: 1
project: sys
version: "1.0.0"
namespace: demo::sys
nodes:
  A: { description: First, language: cpp }
  B: { language: c }
  C: {}
enums:
  Mode: { type: u8, values: { idle: 0, run: 1 } }
types:
  Pair: { fields: [{ name: x, type: u8 }, { name: y, type: u8 }] }
messages:
  Command: { id: 1, fields: [{ name: mode, type: Mode }, { name: pair, type: Pair }] }
  Status: { id: 2, fields: [{ name: value, type: u16 }] }
  Note: { id: 3, fields: [{ name: text, type: u8 }] }
  Beat: { id: 4, fields: [{ name: n, type: u8 }] }
interfaces:
  a_b:
    version: "1.0.0"
    nodes: [A, B]
    header: { fields: [{ name: id, type: u8, role: message_id }] }
    framing: { kind: none }
    messages: [{ name: Command, from: A }, { name: Status, from: B }]
  a_c:
    version: "1.0.0"
    nodes: [A, C]
    header: { fields: [{ name: id, type: u8, role: message_id }] }
    framing: { kind: none }
    messages: [{ name: Note, from: A }]
  bus:
    version: "1.0.0"
    nodes: [A, B, C]
    header: { fields: [{ name: id, type: u8, role: message_id }] }
    framing: { kind: none }
    messages: [{ name: Beat, from: [A, B, C] }, { name: Status, from: B }]
"""
NOTE = "Note: { id: 3, fields: [{ name: text, type: u8 }] }"


def _project(tmp_path: Path, text: str = SYSTEM, name: str = "model") -> Path:
    directory = tmp_path / name
    directory.mkdir(exist_ok=True)
    (directory / "project.yaml").write_text(text, encoding="utf-8")
    return directory


def _load(directory: Path) -> Project:
    result = load_project(directory)
    assert result.project is not None
    return result.project


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_view_keeps_only_the_units_interfaces_and_what_they_use(tmp_path: Path) -> None:
    project = _load(_project(tmp_path))
    view = nodes.node_view(project, "C")
    assert list(view.interfaces) == ["a_c", "bus"]
    assert sorted(view.messages) == ["Beat", "Note", "Status"]
    assert not view.types
    assert not view.enums
    assert view.name == "sys_C"
    assert view.namespace == "demo::sys::C"
    assert nodes.node_view(project, "B").enums.keys() == {"Mode"}
    assert nodes.node_view(project, "B").types.keys() == {"Pair"}


def test_view_leaves_the_project_unchanged(tmp_path: Path) -> None:
    project = _load(_project(tmp_path))
    nodes.node_view(project, "A")
    assert list(project.interfaces) == ["a_b", "a_c", "bus"]
    assert project.name == "sys"


def test_directions(tmp_path: Path) -> None:
    project = _load(_project(tmp_path))
    assert nodes.directions(project, "A", "a_b") == {"Command": "send", "Status": "receive"}
    assert nodes.directions(project, "B", "a_b") == {"Command": "receive", "Status": "send"}
    assert nodes.directions(project, "A", "bus") == {"Beat": "both", "Status": "receive"}
    assert nodes.directions(project, "C", "a_c") == {"Note": "receive"}


def test_unknown_node_is_an_error(tmp_path: Path) -> None:
    project = _load(_project(tmp_path))
    with pytest.raises(nodes.NodeError, match="unknown node 'Z'"):
        nodes.node_view(project, "Z")


def test_hash_follows_only_what_reaches_the_unit(tmp_path: Path) -> None:
    before = _load(_project(tmp_path))
    changed = SYSTEM.replace(NOTE, NOTE.replace("}]", "}, { name: more, type: u8 }]"))
    assert changed != SYSTEM
    after = _load(_project(tmp_path, changed, "changed"))
    assert nodes.node_hash(before, "B") == nodes.node_hash(after, "B")
    assert nodes.node_hash(before, "A") != nodes.node_hash(after, "A")
    assert nodes.node_hash(before, "C") != nodes.node_hash(after, "C")


def test_hash_ignores_where_things_are_written(tmp_path: Path) -> None:
    project = _load(_project(tmp_path))
    shifted = _load(_project(tmp_path, "\n\n" + SYSTEM, "shifted"))
    assert nodes.node_hash(project, "A") == nodes.node_hash(shifted, "A")


def test_nodes_enter_the_bundle_hash(tmp_path: Path) -> None:
    plain = bundle.build_bundle(_project(tmp_path))["bundle"]["content_hash"]
    text = SYSTEM.replace("  C: {}\n", "  C: { description: Third }\n")
    changed = bundle.build_bundle(_project(tmp_path, text, "changed"))["bundle"]["content_hash"]
    assert changed != plain


def test_unit_output_layout_and_namespaces(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert generate.generate(_project(tmp_path), out) == []
    assert sorted(path.name for path in (out / "nodes").iterdir()) == ["A", "B", "C"]
    header = _read(out / "nodes/A/sys_A/model.hpp")
    assert "namespace demo::sys::A" in header
    assert "(node A)" in header
    assert "demo_sys_B_" in _read(out / "nodes/B/sys_B/model.h")


def test_single_node_output_equals_the_full_run(tmp_path: Path) -> None:
    directory = _project(tmp_path)
    full = tmp_path / "full"
    only = tmp_path / "only"
    assert generate.generate(directory, full) == []
    assert generate.generate(directory, only, node="B") == []
    assert [path.name for path in (only / "nodes").iterdir()] == ["B"]
    comparison = filecmp.dircmp(full / "nodes/B", only / "nodes/B")
    assert comparison.diff_files == []
    assert comparison.left_only == comparison.right_only == []


def test_node_tests_sit_in_the_unit_directory(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert generate.generate(_project(tmp_path), out, test_project.TestOptions(), node="A") == []
    cmake = _read(out / "nodes/A/generated_tests/CMakeLists.txt")
    assert 'CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE' in cmake


def test_unknown_node_is_reported(tmp_path: Path) -> None:
    issues = generate.generate(_project(tmp_path), tmp_path / "out", node="Z")
    assert "unknown node 'Z'" in str(issues[0])


def _has_nodes(path: Path) -> bool:
    loaded = load_project(path).project
    return loaded is not None and bool(loaded.nodes)


PLAIN = [path for path in EXAMPLES if not _has_nodes(path)]


@pytest.mark.parametrize("project", PLAIN, ids=lambda path: path.name)
def test_projects_without_nodes_keep_their_output_paths(project: Path, tmp_path: Path) -> None:
    loaded = _load(project)
    out = tmp_path / "out"
    assert generate.generate(project, out) == []
    top = {path.relative_to(out).parts[0] for path in out.rglob("*") if path.is_file()}
    assert top == {loaded.name}
    issues = generate.generate(project, tmp_path / "again", node="A")
    assert "needs a project that defines `nodes`" in str(issues[0])


FIXTURE = ROOT / "tests/python/system_fixture"


def _unit(tmp_path: Path, node: str, language: str) -> Path:
    out = tmp_path / f"{node}-{language}"
    assert generate.generate(FIXTURE, out, language=language, node=node) == []
    return out / "nodes" / node / f"fixture_{node}" / "interfaces"


def test_send_only_interface_has_no_received_callbacks(tmp_path: Path) -> None:
    cpp = _read(_unit(tmp_path, "A", "cpp") / "a_c.hpp")
    assert "onNoteReceived" not in cpp
    assert "onNoteRaw" not in cpp
    assert "UnhandledReason::NotForThisNode" in cpp
    assert "encode_frame(const Note&" in cpp
    c_source = _read(_unit(tmp_path, "A", "c") / "a_c.c")
    assert "WF_UNHANDLED_NOT_FOR_THIS_NODE" in c_source
    assert "_default_raw" not in c_source


def test_receive_only_interface_has_no_encoders(tmp_path: Path) -> None:
    cpp = _read(_unit(tmp_path, "C", "cpp") / "a_c.hpp")
    assert "encode_frame" not in cpp
    assert "send(L& link" not in cpp
    assert "onNoteReceived" in cpp
    c_header = _read(_unit(tmp_path, "C", "c") / "a_c.h")
    assert "_encode_Note" not in c_header
    assert "_link_send_Note" not in c_header


def test_message_counters_follow_the_directions(tmp_path: Path) -> None:
    cpp = _read(_unit(tmp_path, "A", "cpp") / "a_b.hpp")
    assert "rx_Status" in cpp
    assert "tx_Command" in cpp
    assert "rx_Command" not in cpp
    assert "tx_Status" not in cpp
    assert "MessageCounters" not in _read(_unit(tmp_path, "A", "cpp") / "bus.hpp")
    c_header = _read(_unit(tmp_path, "B", "c") / "a_b.h")
    assert "uint32_t rx_Command" in c_header
    assert "uint32_t tx_Status" in c_header
    assert "message_counters" not in _read(_unit(tmp_path, "B", "c") / "bus.h")


def test_shared_bus_message_is_both_ways(tmp_path: Path) -> None:
    project = _load(FIXTURE)
    assert nodes.directions(project, "A", "bus") == {"Heartbeat": "both", "VersionQuery": "send"}
    assert nodes.directions(project, "B", "bus") == {
        "Heartbeat": "both",
        "VersionQuery": "receive",
    }
    cpp = _read(_unit(tmp_path, "A", "cpp") / "bus.hpp")
    assert "onHeartbeatReceived" in cpp
    assert "onVersionQueryReceived" not in cpp


def test_wireshark_names_the_sender() -> None:

    lua = wireshark.render(_load(FIXTURE), "0" * 64)
    assert "Command (from A)" in lua
    assert "Heartbeat (from A, B, C)" in lua


def test_projects_without_nodes_have_endpoint_send(tmp_path: Path) -> None:
    out = tmp_path / "plain"
    assert generate.generate(EXAMPLES[0], out) == []
    text = "".join(_read(path) for path in out.rglob("*.hpp"))
    assert "send(L& link" in text


def _mixed_fixture(tmp_path: Path) -> Path:
    """Return a copy of the system fixture in which unit B is written in C."""
    copy = tmp_path / "mixed"
    shutil.copytree(FIXTURE, copy)
    text = (copy / "project.yaml").read_text(encoding="utf-8")
    old = "  B: { description: Motor controller }"
    assert old in text
    new = "  B: { description: Motor controller, language: c }"
    (copy / "project.yaml").write_text(text.replace(old, new), encoding="utf-8")
    return copy


def test_system_project_is_written_with_the_units(tmp_path: Path) -> None:
    out = tmp_path / "out"
    options = test_project.TestOptions()
    project = _mixed_fixture(tmp_path)
    assert generate.generate(project, out, options) == []
    system = out / "system"
    cmake = _read(system / "CMakeLists.txt")
    assert "fixture_B_c" in cmake, "the C unit is a static library"
    assert "fixture_A_c" not in cmake
    assert "../nodes/A" in cmake
    source = _read(system / "system_test.cpp")
    assert "TEST(System, a_b_Command_pattern_A_to_B)" in source
    assert "TEST(System, a_b_Status_pattern_B_to_A)" in source
    assert "TEST(System, bus_Heartbeat_pattern_B_to_C)" in source
    assert "a_c_Note_pattern_A_to_C" in source
    assert "_C_to_A" not in source.replace("Heartbeat_default_C_to_A", "").replace(
        "Heartbeat_pattern_C_to_A", ""
    ), "C sends only Heartbeat"
    assert (system / "handlers_B.hpp").is_file()
    assert (system / "api_B.hpp").is_file()
    assert not (system / "api_A.hpp").exists()
    assert (system / "system_icd.md").is_file()
    assert generate.generate(project, out, options, node="A") == []


@pytest.mark.skipif(
    shutil.which("cmake") is None,
    reason="needs cmake",
)
def test_system_test_passes_and_fails_for_a_broken_receiver(tmp_path: Path) -> None:
    project = _mixed_fixture(tmp_path)
    out = tmp_path / "out"
    build = service.BuildOptions(system=True)
    result = service.run_tests(project, out, test_project.TestOptions(), build)
    assert result.ok, result.text[-2000:]
    assert "100% tests passed" in result.text
    # Receiver B decodes Command wrongly: the system test must notice.
    source = out / "nodes" / "B" / "fixture_B" / "interfaces" / "a_b.c"
    lines = _read(source).split("\n")
    at = next(i for i, line in enumerate(lines) if "Command_deserialize_with" in line)
    lines.insert(at + 1, "    message.speed = (uint16_t)(message.speed + 1U);")
    source.write_text("\n".join(lines), encoding="utf-8")
    build_dir = out / "system" / "build" / "test"
    cmake, ctest = shutil.which("cmake") or "cmake", shutil.which("ctest") or "ctest"
    rebuilt = subprocess.run(
        [cmake, "--build", str(build_dir), "--config", "Debug"], capture_output=True, check=False
    )
    assert rebuilt.returncode == 0
    failed = subprocess.run(
        [ctest, "--test-dir", str(build_dir), "-C", "Debug"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode != 0
    assert "a_b_Command_pattern_A_to_B" in failed.stdout
