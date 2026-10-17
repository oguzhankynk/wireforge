from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from wireforge import generate, golden, test_cases, test_project
from wireforge.loader import load_project

if TYPE_CHECKING:
    import pytest

    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"
PLUGIN_FILE = AVIONICS / "plugins" / "vendor_sum.py"
OPTIONS = test_project.TestOptions(plugins=PLUGIN_FILE)


def _project(location: Path) -> Project:
    result = load_project(location)
    assert result.project is not None, result.issues
    return result.project


def _inline(tmp_path: Path, text: str) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "project.yaml").write_text(text, encoding="utf-8")
    return project


def _message(profile: test_cases.ProfileCases, name: str) -> test_cases.MessageCases:
    return next(cases for cases in profile.messages if cases.message.name == name)


def test_message_cases_follow_the_reference_codec() -> None:
    profiles = test_cases.cases(_project(EXAMPLES / "01_basic_telemetry"))
    assert [profile.profile for profile in profiles] == [None]
    reading = _message(profiles[0], "SensorReading")
    assert reading.default_validated == "Ok"
    assert isinstance(reading.default_encoded, bytes)
    encodes = {case.name: case for case in reading.encodes}
    assert isinstance(encodes["sensorId_min"].encoded, bytes)
    assert encodes["sensorId_min"].encoded[0] == 1
    assert encodes["sensorId_below_min"].encoded == "OutOfRange"
    assert encodes["sensorId_below_min"].validated == "OutOfRange"
    assert encodes["state_invalid"].edit.value == 4
    assert encodes["temperature_above_max"].edit.value == 151.0
    assert encodes["samples_empty"].edit.value is test_cases.CLEAR
    assert isinstance(encodes["samples_empty"].encoded, bytes)
    assert encodes["samples_0_max"].edit.target == "samples[0U]"
    decodes = {case.name: case.status for case in reading.decodes}
    assert decodes["truncated"] == "Truncated"
    assert decodes["extended"] == "TrailingBytes"
    assert repr(test_cases.CLEAR) == "Clear()"


def test_decode_cases_cover_checksums_counts_and_constants() -> None:
    avionics = test_cases.cases(_project(AVIONICS), golden.load_plugins(PLUGIN_FILE))
    assert [profile.profile for profile in avionics] == ["platformA", "platformB"]
    command = {case.status for case in _message(avionics[0], "Command").decodes}
    assert "ChecksumMismatch" in command
    status = {case.status for case in _message(avionics[0], "NodeBStatus").decodes}
    assert "CountExceedsMax" in status, "a fill array one element too long"
    edge = test_cases.cases(_project(EXAMPLES / "03_edge_cases"))
    everything = {case.status for case in _message(edge[0], "Everything").decodes}
    assert {"ConstMismatch", "CountExceedsMax", "OutOfRange"} <= everything
    extra = {case.name for case in _message(avionics[1], "NavSolution").encodes}
    assert "extra_max" in extra, "profile-only fields are tested in their profile"
    assert "extra_max" not in {case.name for case in _message(avionics[0], "NavSolution").encodes}


def test_interface_cases() -> None:
    profile = test_cases.cases(_project(EXAMPLES / "01_basic_telemetry"))[0]
    (cases,) = profile.interfaces
    assert cases.sync == bytes.fromhex("a55a")
    assert cases.corrupt_status == "ChecksumMismatch"
    assert cases.exact
    assert cases.overhead == 10
    assert cases.corrupt[:-1] == cases.frames[0][1][:-1]
    raw = test_cases.cases(_project(AVIONICS), golden.load_plugins(PLUGIN_FILE))[0]
    node_b = next(item for item in raw.interfaces if item.interface.name == "nodeB_to_our")
    assert node_b.corrupt_status == "Ok"
    assert node_b.overhead == 0


RESYNC = """\
schema_version: 1
project: resync
messages:
  M:
    id: 1
    fields:
      - { name: a, type: u8 }
      - { name: b, type: f32, min: -3.4028234663852886e38, max: 1.5 }
      - { name: c, type: i8, bits: 4 }
      - { name: pad, type: u8, bits: 4, reserved: true }
      - { name: d, type: u8, max: 10 }
interfaces:
  link:
    version: "1.0.0"
    direction: both
    header:
      fields:
        - { name: id, type: u8, role: message_id }
        - { name: length, type: u8, role: payload_length }
    framing: { kind: length_prefixed, max_frame_size: 32, resync: true }
    messages: [M]
"""


def test_resync_without_sync_is_not_exact(tmp_path: Path) -> None:
    profile = test_cases.cases(_project(_inline(tmp_path, RESYNC)))[0]
    (cases,) = profile.interfaces
    assert not cases.exact
    names = {case.name for case in profile.messages[0].encodes}
    assert "b_below_min" not in names, "beyond the f32 range"
    assert {"b_min", "b_max", "b_above_max", "c_below_min", "c_above_max"} <= names
    source = test_project.render(_project(_inline(tmp_path, RESYNC)), tmp_path, tmp_path)
    assert "EXPECT_GE(rejected.errors, 1U);" in source["interface_test.cpp"]


def test_render_files_and_cmake() -> None:
    project = _project(AVIONICS)
    directory = ROOT / "build" / "x" / project.name / "generated_tests"
    files = test_project.render(project, AVIONICS, directory, OPTIONS)
    assert set(files) == {
        "CMakeLists.txt",
        "CMakePresets.json",
        ".clang-tidy",
        "README.md",
        "test_support.hpp",
        "wire_support.hpp",
        "handlers.hpp",
        "diff_harness.cpp",
        "fuzz_target.cpp",
        "selftest.cpp",
        "golden_test.cpp",
        "message_test.cpp",
        "interface_test.cpp",
    }
    cmake = files["CMakeLists.txt"]
    assert (
        'wireforge_add_profile("_platformB" PLATFORM_B=1 MAX_TRACKS=5 CHANNELS=5 '
        "WIREFORGE_PROFILE_platformB)" in cmake
    )
    assert '"${CMAKE_CURRENT_SOURCE_DIR}/../../../../runtime/include"' in cmake
    assert "${WIREFORGE_MODEL_DIR}/app/include" in cmake
    assert '--filter "avionics_demo/model[.]hpp"' in cmake
    assert "@" not in cmake.replace("@file", "")
    assert '"name": "asan-ubsan"' in files["CMakePresets.json"]
    assert "#if defined(WIREFORGE_PROFILE_platformA)" in files["message_test.cpp"]
    assert "Status::ChecksumMismatch" in files["message_test.cpp"]
    assert test_project.render(project, AVIONICS, directory, OPTIONS) == files, "deterministic"


def test_paths_without_common_directory_are_absolute() -> None:
    project = _project(EXAMPLES / "03_edge_cases")
    cmake = test_project.render(project, EXAMPLES, Path("/"), test_project.TestOptions())[
        "CMakeLists.txt"
    ]
    runtime = test_project.RUNTIME_INCLUDE.as_posix()
    assert f'set(WIREFORGE_RUNTIME_INCLUDE_DIR "{runtime}"' in cmake
    assert "WIREFORGE_MODEL_DIR" not in cmake, "no user include directories"


def test_generate_cli_writes_the_test_project(tmp_path: Path) -> None:
    args = [str(AVIONICS), "-o", str(tmp_path), "--tests", "--plugins", str(PLUGIN_FILE)]
    assert generate.main(args) == 0
    directory = tmp_path / "avionics_demo" / "generated_tests"
    golden_test = directory / "golden_test.cpp"
    assert golden_test.exists()
    stamp = golden_test.stat().st_mtime_ns
    assert generate.main([*args, "--coverage-threshold", "90"]) == 0
    assert golden_test.stat().st_mtime_ns == stamp, "unchanged files are not rewritten"


def test_custom_checksum_needs_plugins(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert generate.main([str(AVIONICS), "-o", str(tmp_path), "--tests"]) == 1
    assert "cannot compute the test expectations" in capsys.readouterr().out
    assert (tmp_path / "avionics_demo" / "model.hpp").exists()


def test_every_generated_test_records_its_requirements() -> None:
    for name in ("01_basic_telemetry", "02_multi_interface_avionics", "03_edge_cases"):
        project = _project(EXAMPLES / name)
        directory = ROOT / "build" / "x" / project.name / "generated_tests"
        files = test_project.render(project, EXAMPLES / name, directory, OPTIONS)
        for source in ("golden_test.cpp", "message_test.cpp", "interface_test.cpp"):
            lines = files[source].splitlines()
            tests = [index for index, line in enumerate(lines) if line.startswith("TEST(")]
            assert tests, f"{name}/{source}"
            for index in tests:
                follower = lines[index + 1].strip()
                assert follower.startswith('::testing::Test::RecordProperty("requirements", "LLR-')
        assert "static_assert(min_wire_size" in files["message_test.cpp"]
        assert "WireSize) {" in files["message_test.cpp"]
        assert "static_assert(" not in files["message_test.cpp"].split("TEST(")[0]
