from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wireforge import llr, test_cases
from wireforge.loader import load_project

if TYPE_CHECKING:
    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
NAMES = ("01_basic_telemetry", "02_multi_interface_avionics", "03_edge_cases", "04_user_type")

BASIC_KEYS = [
    "SensorState.VALUES",
    "frameCrc.ALGORITHM",
    "Vec3.LAYOUT",
    "Vec3.x.DEFAULT",
    "Vec3.y.DEFAULT",
    "Vec3.z.DEFAULT",
    "SensorReading.LAYOUT",
    "SensorReading.SIZE",
    "SensorReading.TRUNCATED",
    "SensorReading.TRAILING",
    "SensorReading.sensorId.DEFAULT",
    "SensorReading.sensorId.RANGE",
    "SensorReading.state.DEFAULT",
    "SensorReading.state.ENUM",
    "SensorReading.temperature.DEFAULT",
    "SensorReading.temperature.RANGE",
    "SensorReading.sampleCount.RANGE",
    "SensorReading.samples.COUNT",
    "sensor_to_us.header.sync.CONST",
    "sensor_to_us.trailer.crc.CHECKSUM",
    "sensor_to_us.FRAME_ENCODE",
    "sensor_to_us.FRAME_SYNC",
    "sensor_to_us.FRAME_LENGTH",
    "sensor_to_us.FRAME_RESYNC",
    "sensor_to_us.FRAME_MAX",
    "sensor_to_us.DISPATCH",
    "sensor_to_us.DISPATCH_UNKNOWN",
    "sensor_to_us.RAW",
]


def _project(example: str) -> Project:
    result = load_project(EXAMPLES / example)
    assert result.project is not None, result.issues
    return result.project


def _copy(tmp_path: Path, text: str) -> Project:
    (tmp_path / "project.yaml").write_text(text, encoding="utf-8")
    result = load_project(tmp_path)
    assert result.project is not None, result.issues
    return result.project


def _by_key(project: Project) -> dict[str, llr.Llr]:
    return {item.key: item for item in llr.derive(project)}


def test_basic_example_derives_the_expected_keys() -> None:
    assert [item.key for item in llr.derive(_project("01_basic_telemetry"))] == BASIC_KEYS


def test_keys_are_unique_and_kinds_known() -> None:
    for name in NAMES:
        items = llr.derive(_project(name))
        keys = [item.key for item in items]
        assert len(keys) == len(set(keys)), name
        assert {item.kind for item in items} <= set(llr.KINDS), name
        assert all(item.key.endswith(f".{item.kind}") for item in items)
        assert all(item.text and item.source.line for item in items)


def test_display_id_is_the_only_prefix() -> None:
    assert (
        llr.display_id("SensorReading.temperature.RANGE") == "LLR-SensorReading.temperature.RANGE"
    )


def test_parents_follow_the_owner_chain() -> None:
    items = _by_key(_project("01_basic_telemetry"))
    assert items["SensorReading.temperature.RANGE"].parents == ("HLR-SNS-001", "HLR-SNS-004")
    assert items["SensorReading.sensorId.RANGE"].parents == ("HLR-SNS-001",)
    assert items["frameCrc.ALGORITHM"].parents == ("HLR-SNS-005",)
    assert items["sensor_to_us.FRAME_RESYNC"].parents == ("HLR-SNS-001", "HLR-SNS-006")
    assert items["sensor_to_us.trailer.crc.CHECKSUM"].parents == ("HLR-SNS-001", "HLR-SNS-005")
    assert items["sensor_to_us.DISPATCH"].parents == ("HLR-SNS-001", "HLR-SNS-006")
    assert items["Vec3.LAYOUT"].derived
    assert not items["SensorReading.LAYOUT"].derived


def test_runtime_verified_marks_the_unknown_id_rule_only() -> None:
    marked = {
        item.key
        for item in llr.derive(_project("02_multi_interface_avionics"))
        if item.runtime_verified
    }
    assert marked == {
        "our_to_nodeA.DISPATCH_UNKNOWN",
        "our_to_nodeC.DISPATCH_UNKNOWN",
    }


def test_expression_values_are_shown_per_profile() -> None:
    items = _by_key(_project("02_multi_interface_avionics"))
    assert "MAX_TRACKS (platformA: 3, platformB: 5)" in items["NavSolution.trackCount.RANGE"].text
    assert "platformA" in items["NavSolution.SIZE"].text


def test_wire_size_text_without_profiles() -> None:
    items = _by_key(_project("01_basic_telemetry"))
    assert items["SensorReading.SIZE"].text == (
        "The wire size of `SensorReading` shall be between 19 and 27 bytes."
    )


BASE = """\
schema_version: 1
project: p
messages:
  M:
    id: 1
    fields:
      - { name: a, type: u8, min: 1, max: 9 }
      - { name: b, type: u16, default: 3 }
"""


def test_keys_do_not_depend_on_field_order(tmp_path: Path) -> None:
    first = {item.key for item in llr.derive(_copy(tmp_path, BASE))}
    first_field = "      - { name: a, type: u8, min: 1, max: 9 }\n"
    second_field = "      - { name: b, type: u16, default: 3 }\n"
    swapped = BASE.replace(first_field + second_field, second_field + first_field)
    assert swapped != BASE
    assert {item.key for item in llr.derive(_copy(tmp_path, swapped))} == first


def test_keys_change_with_names(tmp_path: Path) -> None:
    renamed = BASE.replace("name: a,", "name: alpha,")
    keys = {item.key for item in llr.derive(_copy(tmp_path, renamed))}
    assert "M.alpha.RANGE" in keys
    assert "M.a.RANGE" not in keys


def test_range_check_switches_remove_the_range_rules(tmp_path: Path) -> None:
    off = BASE.replace(
        "project: p\n", "project: p\ndefaults: { range_check: { encode: false, decode: false } }\n"
    )
    keys = {item.key for item in llr.derive(_copy(tmp_path, off))}
    assert "M.a.RANGE" not in keys
    one_way = BASE.replace(
        "project: p\n", "project: p\ndefaults: { range_check: { decode: false } }\n"
    )
    text = _by_key(_copy(tmp_path, one_way))["M.a.RANGE"].text
    assert text.startswith("Encoding shall fail")


@pytest.mark.parametrize("name", NAMES)
def test_every_mapped_key_exists(name: str) -> None:
    """Whatever the test generators ask the verifier for is a derived requirement."""
    project = _project(name)
    verifier = llr.Verifier(project)
    known = verifier.known
    for profile in test_cases.cases(project):
        for message in profile.messages:
            groups = [verifier.defaults(message.message), verifier.wire_size(message.message)]
            groups += [verifier.encode_case(message.message, case) for case in message.encodes]
            groups += [verifier.decode_case(message.message, case) for case in message.decodes]
            for keys in groups:
                assert set(keys) <= known
                assert keys, (message.message.name, "a test without a requirement")
        for cases in profile.interfaces:
            interface = cases.interface
            framed = interface.framing.kind != "none"
            tests = ["DispatchesEveryMessage", "EncodeFrameNeedsRoom", "WireSize"]
            tests += ["DeliversRawBytesWithoutDecoding", "RawCanForwardToDecoder"]
            tests += ["DeliversByteByByte"] if framed else []
            tests += ["SkipsBytesBeforeSync"] if cases.sync else []
            for test in tests:
                keys = verifier.interface_test(interface, test)
                assert keys, (interface.name, test)
                assert set(keys) <= known
            status = cases.corrupt_status
            keys = verifier.interface_test(
                interface, "RejectsCorruptedFrame", corrupt_status=status
            )
            assert keys, interface.name


def test_decode_cases_map_to_the_failing_field() -> None:
    project = _project("01_basic_telemetry")
    verifier = llr.Verifier(project)
    (profile,) = test_cases.cases(project)
    (reading,) = profile.messages
    mapped = {case.name: verifier.decode_case(reading.message, case) for case in reading.decodes}
    assert mapped["truncated"] == ("SensorReading.TRUNCATED",)
    assert mapped["extended"] == ("SensorReading.TRAILING",)
    assert ("SensorReading.sensorId.RANGE",) in mapped.values()
    assert ("SensorReading.state.ENUM",) in mapped.values()
    assert ("SensorReading.sampleCount.RANGE",) in mapped.values()


def test_field_at_enters_nested_types_and_skips_indices() -> None:
    project = _project("01_basic_telemetry")
    reading = project.messages["SensorReading"]
    found = llr.field_at(project, reading, "/accel/y")
    assert found is not None
    owner, item = found
    assert (owner.name, item.name) == ("Vec3", "y")
    found = llr.field_at(project, reading, "/samples/2")
    assert found is not None
    assert found[1].name == "samples"
    assert llr.field_at(project, reading, "/nothing") is None
    assert llr.field_at(project, reading, "") is None


def test_owner_of_header_and_trailer_fields() -> None:
    project = _project("01_basic_telemetry")
    interface = project.interfaces["sensor_to_us"]
    assert llr.owner_of((interface.header or ())[0]) == "sensor_to_us.header"
    assert llr.owner_of(interface.trailer[0]) == "sensor_to_us.trailer"
    assert llr.owner_of(project.types["Vec3"].fields[0]) == "Vec3"
    assert llr.owner_of(project.messages["SensorReading"].fields[0]) == "SensorReading"


def test_requirement_notes_only_with_a_catalog() -> None:
    assert llr.requirement_notes(_project("03_edge_cases")) == {}
    notes = llr.requirement_notes(_project("01_basic_telemetry"))
    assert notes["frameCrc"] == ["/// Requirements: HLR-SNS-005; LLR-frameCrc.ALGORITHM"]
    assert all(len(line) <= 100 for lines in notes.values() for line in lines)
    assert notes["SensorReading"][0].startswith("/// Requirements: HLR-SNS-001, HLR-SNS-004; LLR-")
