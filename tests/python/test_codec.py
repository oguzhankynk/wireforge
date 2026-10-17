import json
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from wireforge import golden
from wireforge.codec import CodecError, ReferenceCodec, crc
from wireforge.loader import load_project
from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
GOLDEN = ROOT / "tests" / "golden"
PLUGIN_FILE = EXAMPLES / "02_multi_interface_avionics" / "plugins" / "vendor_sum.py"
PLUGINS = golden.load_plugins(PLUGIN_FILE)


def _project(path: Path) -> Project:
    result = load_project(path)
    assert result.project is not None, [str(issue) for issue in result.issues]
    return result.project


def _inline(tmp_path: Path, text: str) -> Project:
    (tmp_path / "project.yaml").write_text(text, encoding="utf-8")
    return _project(tmp_path)


EXAMPLE_NAMES = sorted(path.parent.name for path in EXAMPLES.glob("*/project.yaml"))


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_golden_files_are_up_to_date(name: str) -> None:
    text = golden.render(_project(EXAMPLES / name), PLUGINS)
    assert (GOLDEN / f"{name}.json").read_text(encoding="utf-8") == text


def _golden_vectors() -> list[tuple[str, dict[str, Any]]]:
    vectors: list[tuple[str, dict[str, Any]]] = []
    for name in EXAMPLE_NAMES:
        loaded = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
        vectors.extend((name, vector) for vector in loaded)
    return vectors


@pytest.mark.parametrize(
    ("example", "vector"),
    _golden_vectors(),
    ids=lambda item: (
        item
        if isinstance(item, str)
        else f"{item['interface']}-{item['message']}-{item['profile']}-{item['sample']}"
    ),
)
def test_golden_vector_round_trip(example: str, vector: dict[str, Any]) -> None:
    codec = ReferenceCodec(_project(EXAMPLES / example), vector["profile"], PLUGINS)
    payload = bytes.fromhex(vector["payload"])
    frame = bytes.fromhex(vector["frame"])
    assert codec.encode_message(vector["message"], vector["value"], vector["interface"]) == payload
    assert codec.decode_message(vector["message"], payload, vector["interface"]) == vector["value"]
    decoded = codec.decode_frame(vector["interface"], frame)
    assert decoded.message == vector["message"]
    assert decoded.value == vector["value"]


def test_every_example_has_pattern_vectors() -> None:
    samples = {vector["sample"] for _, vector in _golden_vectors()}
    assert samples == {"default", "pattern"}


BITS = """
schema_version: 1
project: bits
messages:
  Group:
    id: 1
    bit_order: {order}
    endian: {endian}
    fields:
      - {{ name: a, type: u8, bits: 3 }}
      - {{ name: b, type: u8, bits: 7 }}
      - {{ name: c, type: u8, bits: 6 }}
interfaces:
  raw: {{ version: "1.0.0", direction: both, messages: [Group] }}
"""


@pytest.mark.parametrize(
    ("order", "endian", "wire"),
    [
        ("msb_first", "big", "b56a"),
        ("msb_first", "little", "6ab5"),
        ("lsb_first", "big", "aaad"),
        ("lsb_first", "little", "adaa"),
    ],
)
def test_bit_groups_match_the_cpp_runtime(
    tmp_path: Path, order: str, endian: str, wire: str
) -> None:
    # Same group and values as the C++ test BitGroups.StraddlingFieldsInBothOrdersAndEndians.
    codec = ReferenceCodec(_inline(tmp_path, BITS.format(order=order, endian=endian)))
    value = {"a": 5, "b": 0x55, "c": 0x2A}
    assert codec.encode_message("Group", value).hex() == wire
    assert codec.decode_message("Group", bytes.fromhex(wire)) == value


RULES = """
schema_version: 1
project: rules
defaults: { endian: big }
enums:
  Mode: { type: u8, values: { idle: 0, run: 1 } }
checksums:
  c16: { algo: crc, width: 16, poly: 0x1021, init: 0xFFFF, refin: false, refout: false, xorout: 0 }
  custom: { algo: custom, impl: "my::Sum", include: my/sum.hpp, size_bytes: 1 }
types:
  Pair:
    fields:
      - { name: x, type: i8 }
      - { name: y, type: i8 }
messages:
  Values:
    id: 1
    fields:
      - { name: magic, type: u16, const: 0xBEEF }
      - { name: level, type: i16, min: -10, max: 10 }
      - { name: mode, type: Mode }
      - { name: flag, type: bool }
      - { name: ratio, type: f32 }
      - { name: n, type: u8, max: 3 }
      - { name: items, type: u16, occurs: { max: 3, count: n } }
      - { name: half, type: u8 }
      - { name: doubled, type: u8, occurs: { max: 4, count: "half * 2" } }
      - { name: tag, type: char, occurs: { max: 4 } }
      - { name: pairs, type: Pair, occurs: { max: 2 } }
      - { name: crc, type: u16, checksum: { use: c16, covers: { scope: preceding } } }
  Tail:
    id: 2
    fields:
      - { name: head, type: u8 }
      - { name: rest, type: u8, occurs: { max: 4, fill: true } }
  Lenient:
    id: 3
    range_check: { encode: false, decode: false }
    fields:
      - { name: level, type: i16, min: -10, max: 10 }
  Custom:
    id: 4
    fields:
      - { name: data, type: u8 }
      - { name: sum, type: u8, checksum: { use: custom, covers: { scope: preceding } } }
interfaces:
  link:
    version: "1.0.0"
    direction: both
    header:
      fields:
        - { name: sync, type: u8, const: 0x7E, role: sync }
        - { name: id, type: u8, role: message_id }
        - { name: length, type: u8, role: payload_length }
    trailer:
      fields:
        - { name: fcs, type: u16, checksum: { use: c16, covers: { scope: header_and_payload } } }
    framing: { kind: sync_length, max_frame_size: 64 }
    messages: [Values, Tail, Lenient, Custom]
  fixed:
    version: "1.0.0"
    direction: tx
    framing: { kind: fixed, frame_size: 3 }
    messages: [Tail]
"""


@pytest.fixture
def rules(tmp_path: Path) -> ReferenceCodec:
    return ReferenceCodec(
        _inline(tmp_path, RULES), plugins={"my::Sum": lambda data: sum(data) & 0xFF}
    )


def _values() -> dict[str, Any]:
    return {
        "level": -2,
        "mode": "run",
        "flag": True,
        "ratio": 0.5,
        "items": [1, 2],
        "half": 1,
        "doubled": [7, 8],
        "tag": "ab",
        "pairs": [{"x": -1, "y": 2}, {"x": 3, "y": -4}],
    }


def test_encoding_layout(rules: ReferenceCodec) -> None:
    payload = rules.encode_message("Values", _values())
    expected = "beef" + "fffe" + "01" + "01" + "3f000000" + "02" + "00010002" + "01" + "0708"
    expected += "61620000" + "ff0203fc"
    assert payload.hex().startswith(expected)
    assert len(payload) == len(expected) // 2 + 2
    decoded = rules.decode_message("Values", payload)
    assert decoded["n"] == 2, "the count field is filled from the list length"
    assert decoded["magic"] == 0xBEEF
    assert decoded["tag"] == "ab"
    assert decoded["mode"] == "run"
    assert decoded["pairs"] == _values()["pairs"]


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"level": 11}, "OutOfRange"),
        ({"level": 40000}, "OutOfRange"),
        ({"mode": "fly"}, "OutOfRange"),
        ({"flag": 3}, "OutOfRange"),
        ({"ratio": 1e39}, "OutOfRange"),
        ({"ratio": "x"}, "OutOfRange"),
        ({"items": [1, 2, 3, 4]}, "CountExceedsMax"),
        ({"doubled": [1]}, "CountExceedsMax"),
        ({"tag": "abcde"}, "CountExceedsMax"),
        ({"pairs": [{"x": 1, "y": 1}]}, "CountExceedsMax"),
        ({"items": 5}, "InvalidState"),
        ({"pairs": [5, 6]}, "InvalidState"),
    ],
)
def test_encoding_errors(rules: ReferenceCodec, change: dict[str, Any], status: str) -> None:
    with pytest.raises(CodecError) as info:
        rules.encode_message("Values", {**_values(), **change})
    assert info.value.status == status


def _corrupt(rules: ReferenceCodec, offset: int, mask: int) -> bytes:
    payload = bytearray(rules.encode_message("Values", _values()))
    payload[offset] ^= mask
    return bytes(payload)


@pytest.mark.parametrize(
    ("offset", "value", "status"),
    [
        (0, 0xFF, "ConstMismatch"),
        (3, 0x20, "OutOfRange"),
        (4, 9, "OutOfRange"),
        (5, 2, "OutOfRange"),
        (15, 8, "CountExceedsMax"),
        (27, 1, "ChecksumMismatch"),
    ],
)
def test_decoding_errors(rules: ReferenceCodec, offset: int, value: int, status: str) -> None:
    with pytest.raises(CodecError) as info:
        rules.decode_message("Values", _corrupt(rules, offset, value))
    assert info.value.status == status


def test_truncated_and_trailing(rules: ReferenceCodec) -> None:
    payload = rules.encode_message("Values", _values())
    with pytest.raises(CodecError, match="Truncated"):
        rules.decode_message("Values", payload[:-1])
    with pytest.raises(CodecError, match="TrailingBytes"):
        rules.decode_message("Values", payload + b"\x00")


def test_range_check_can_be_disabled(rules: ReferenceCodec) -> None:
    payload = rules.encode_message("Lenient", {"level": 500})
    assert rules.decode_message("Lenient", payload) == {"level": 500}
    with pytest.raises(CodecError, match="OutOfRange"):
        rules.encode_message("Lenient", {"level": 70000})


def test_fill_takes_the_rest_of_the_payload(rules: ReferenceCodec) -> None:
    payload = rules.encode_message("Tail", {"head": 1, "rest": [2, 3, 4]})
    assert payload.hex() == "01020304"
    assert rules.decode_message("Tail", payload) == {"head": 1, "rest": [2, 3, 4]}
    with pytest.raises(CodecError, match="CountExceedsMax"):
        rules.decode_message("Tail", bytes(6))


def test_custom_checksum_plugin(tmp_path: Path, rules: ReferenceCodec) -> None:
    assert rules.encode_message("Custom", {"data": 0x10}).hex() == "1010"
    without = ReferenceCodec(_inline(tmp_path, RULES))
    with pytest.raises(CodecError, match="no plugin"):
        without.encode_message("Custom", {"data": 1})


def test_frames(rules: ReferenceCodec) -> None:
    frame = rules.encode_frame("link", "Tail", {"head": 9, "rest": [1]})
    assert frame[:3].hex() == "7e0202"
    decoded = rules.decode_frame("link", frame)
    assert decoded.message == "Tail"
    assert decoded.value == {"head": 9, "rest": [1]}
    assert decoded.header["length"] == 2
    bad = bytearray(frame)
    bad[-1] ^= 1
    with pytest.raises(CodecError, match="ChecksumMismatch"):
        rules.decode_frame("link", bytes(bad))


@pytest.mark.parametrize(
    ("offset", "value", "status"),
    [(0, 0x7F, "ConstMismatch"), (1, 0x09, "UnknownMessageId"), (2, 0x05, "InvalidLength")],
)
def test_frame_header_errors(rules: ReferenceCodec, offset: int, value: int, status: str) -> None:
    frame = bytearray(rules.encode_frame("link", "Tail", {"head": 9, "rest": [1]}))
    frame[offset] = value
    fcs = crc(rules.project.checksums["c16"], bytes(frame[:-2]))
    frame[-2:] = fcs.to_bytes(2, "big")  # keep the trailer valid to reach the header check
    with pytest.raises(CodecError) as info:
        rules.decode_frame("link", bytes(frame))
    assert info.value.status == status


def test_fixed_framing_needs_exact_size(rules: ReferenceCodec) -> None:
    assert rules.encode_frame("fixed", "Tail", {"head": 1, "rest": [2, 3]}).hex() == "010203"
    with pytest.raises(CodecError, match="InvalidLength"):
        rules.encode_frame("fixed", "Tail", {"head": 1, "rest": [2]})
    with pytest.raises(CodecError, match="Truncated"):
        rules.decode_frame("link", b"\x7e")
    with pytest.raises(CodecError, match="InvalidState"):
        rules.encode_frame("fixed", "Values", {})


def test_unknown_names_and_profiles(rules: ReferenceCodec) -> None:
    with pytest.raises(CodecError, match="unknown message"):
        rules.encode_message("Nope", {})
    with pytest.raises(CodecError, match="unknown interface"):
        rules.encode_frame("nope", "Tail", {})
    with pytest.raises(CodecError, match="mapping"):
        rules.encode_message("Values", {**_values(), "pairs": [[1], [2]]})
    with pytest.raises(ValueError, match="unknown profile"):
        ReferenceCodec(rules.project, "platformZ")


def test_profiles_change_the_layout() -> None:
    project = _project(EXAMPLES / "02_multi_interface_avionics")
    small = ReferenceCodec(project, "platformA")
    large = ReferenceCodec(project, "platformB")
    assert len(small.encode_message("NodeBStatus", {})) == 4 + 3 * 2
    assert len(large.encode_message("NodeBStatus", {})) == 4 + 5 * 2
    assert "extra" not in small.default_value("NavSolution")
    assert "extra" in large.default_value("NavSolution")


def test_golden_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    example = EXAMPLES / "01_basic_telemetry"
    output = tmp_path / "golden.json"
    assert golden.main([str(example), str(output)]) == 0
    assert golden.main([str(example), str(output), "--check"]) == 0
    output.write_text("[]\n", encoding="utf-8")
    assert golden.main([str(example), str(output), "--check"]) == 1
    assert golden.main([str(EXAMPLES / "02_multi_interface_avionics"), str(output)]) == 1
    assert "no plugin" in capsys.readouterr().out
    broken = tmp_path / "broken"
    broken.mkdir()
    YAML(typ="safe").dump({"schema_version": 1}, broken / "project.yaml")
    assert golden.main([str(broken), str(output)]) == 1
