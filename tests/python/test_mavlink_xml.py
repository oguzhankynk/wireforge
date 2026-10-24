"""MAVLink XML dialects imported as projects (`wireforge import-mavlink`), against pymavlink.

pymavlink ships the XML definitions it was generated from, so the same files are imported here: the
CRC_EXTRA byte and the field order of every message of every dialect agree with pymavlink, and every
message of common.xml encodes to the bytes pymavlink packs (with and without trailing zero bytes)
and decodes both ways. tests/models/mavlink_import is an import of common.xml; the generated-code
gates run on it, and it must stay what the importer writes.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

import pytest

from wireforge import cli, importers, mavlink_xml, service
from wireforge.codec import ReferenceCodec
from wireforge.loader import load_project
from wireforge.validate import CPP_KEYWORDS

os.environ["MAVLINK20"] = "1"
pymavlink = importlib.import_module("pymavlink")
DIALECTS = Path(str(pymavlink.__file__)).parent / "dialects" / "v20"
COMMON = DIALECTS / "common.xml"
MODEL = Path(__file__).resolve().parent.parent / "models" / "mavlink_import"
MODEL_MESSAGES = (
    "HEARTBEAT",
    "SYS_STATUS",
    "PARAM_VALUE",
    "GPS_RAW_INT",
    "ATTITUDE_QUATERNION",
    "COMMAND_LONG",
    "COMMAND_ACK",
    "STATUSTEXT",
    "ENCAPSULATED_DATA",
    "MISSION_ITEM_INT",
)
INTEGERS = {
    "uint8_t": (0, 0xFF),
    "int8_t": (-0x80, 0x7F),
    "uint16_t": (0, 0xFFFF),
    "int16_t": (-0x8000, 0x7FFF),
    "uint32_t": (0, 0xFFFFFFFF),
    "int32_t": (-0x80000000, 0x7FFFFFFF),
    "uint64_t": (0, 0xFFFFFFFFFFFFFFFF),
    "int64_t": (-0x8000000000000000, 0x7FFFFFFFFFFFFFFF),
}


def _dialect_names() -> list[str]:
    return sorted(path.stem for path in DIALECTS.glob("*.xml"))


@pytest.mark.parametrize("name", _dialect_names())
def test_crc_extra_and_field_order_agree_with_pymavlink(name: str) -> None:
    dialect = mavlink_xml.parse(DIALECTS / f"{name}.xml")
    module = importlib.import_module(f"pymavlink.dialects.v20.{name}")
    assert dialect.messages
    for identifier, message in dialect.messages.items():
        theirs = module.mavlink_map[identifier]
        assert mavlink_xml.crc_extra(message) == theirs.crc_extra, message.name
        order = [item.name for item in mavlink_xml.wire_order(message)]
        assert order == list(theirs.ordered_fieldnames), message.name


def _field_name(name: str) -> str:
    camel = mavlink_xml.camel(name, upper=False)
    return f"{camel}Value" if camel in CPP_KEYWORDS else camel


class _Numbers:
    """Deterministic xorshift numbers (the same sequence on every run)."""

    def __init__(self, seed: int) -> None:
        self.state = seed

    def between(self, low: int, high: int) -> int:
        self.state ^= (self.state << 13) & 0xFFFFFFFFFFFFFFFF
        self.state ^= self.state >> 7
        self.state ^= (self.state << 17) & 0xFFFFFFFFFFFFFFFF
        return low + self.state % (high - low + 1)


def _value(numbers: _Numbers, item: mavlink_xml.XmlField, *, zero: bool) -> object:
    def one() -> object:
        if zero:
            return "" if item.type == "char" else (0.0 if item.type in {"float", "double"} else 0)
        if item.type == "char":
            return chr(numbers.between(ord("a"), ord("z")))
        if item.type in {"float", "double"}:
            return numbers.between(-1000, 1000) / 8
        low, high = INTEGERS[item.type]
        return numbers.between(low, high)

    if item.array is None:
        return "\0" if zero and item.type == "char" else one()
    if item.type == "char":
        return "" if zero else "".join(str(one()) for _ in range(numbers.between(1, item.array)))
    return [one() for _ in range(item.array)]


@pytest.fixture(scope="module")
def common(tmp_path_factory: pytest.TempPathFactory) -> tuple[ReferenceCodec, Any]:
    location = tmp_path_factory.mktemp("mavlink") / "common"
    result = service.import_mavlink(COMMON, location, importers.Project("common"))
    assert result.ok, result.text
    assert result.data["messages"] == len(mavlink_xml.parse(COMMON).messages)
    project = load_project(location).project
    assert project is not None
    plugins = service.load_plugins(service.find_plugins(location))
    return ReferenceCodec(project, None, plugins), importlib.import_module(
        "pymavlink.dialects.v20.common"
    )


@pytest.mark.parametrize("zero_tail", [False, True], ids=["values", "zero-tail"])
def test_every_common_message_has_the_bytes_of_pymavlink(
    common: tuple[ReferenceCodec, Any], *, zero_tail: bool
) -> None:
    codec, theirs = common
    numbers = _Numbers(0x9E3779B97F4A7C15)
    for identifier, message in sorted(mavlink_xml.parse(COMMON).messages.items()):
        order = mavlink_xml.wire_order(message)
        values: dict[str, Any] = {}
        for index, item in enumerate(order):
            zero = zero_tail and index >= len(order) // 2
            values[item.name] = (
                mavlink_xml.MAVLINK_VERSION
                if item.version_field
                else _value(numbers, item, zero=zero)
            )
        cls = theirs.mavlink_map[identifier]
        packed = [
            values[name].encode() if isinstance(values[name], str) else values[name]
            for name in cls.fieldnames
        ]
        mav = theirs.MAVLink(None, srcSystem=1, srcComponent=1)
        expected = bytes(cls(*packed).pack(mav))
        ours = {
            _field_name(item.name): values[item.name] for item in order if not item.version_field
        }
        name = mavlink_xml.camel(message.name, upper=True)
        frame = codec.encode_frame("link", name, ours, {"sequence": 0})
        assert frame == expected, message.name
        assert codec.decode_frame("link", expected).message == name
        assert theirs.MAVLink(None).parse_char(frame) is not None, message.name


def test_the_test_model_is_what_the_importer_writes(tmp_path: Path) -> None:
    messages = [message for name in MODEL_MESSAGES for message in ("--message", name)]
    arguments = ["import-mavlink", str(COMMON), str(tmp_path / "model"), "--name", "mavlink_import"]
    assert cli.main([*arguments, "--namespace", "tests::mavlink", *messages]) == 0
    for path in sorted((tmp_path / "model").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            relative = path.relative_to(tmp_path / "model")
            assert (MODEL / relative).read_text(encoding="utf-8") == path.read_text(
                encoding="utf-8"
            ), f"{relative} differs: import common.xml again into tests/models/mavlink_import"


def test_selected_messages_take_only_their_enums(tmp_path: Path) -> None:
    result = service.import_mavlink(COMMON, tmp_path / "p", importers.Project("p"), ["heartbeat"])
    assert result.ok, result.text
    project = load_project(tmp_path / "p").project
    assert project is not None
    assert list(project.messages) == ["Heartbeat"]
    assert set(project.enums) == {"MAV_TYPE", "MAV_AUTOPILOT", "MAV_MODE_FLAG", "MAV_STATE"}
    header = (tmp_path / "p" / mavlink_xml.CRC_EXTRA_HEADER).read_text(encoding="utf-8")
    assert "X(0U, 50U) /* HEARTBEAT */" in header
    lua = (tmp_path / "p" / mavlink_xml.LUA_HOOK).read_text(encoding="utf-8")
    assert "local CRC_EXTRA = { [0] = 50 }" in lua


def test_an_unknown_message_is_refused(tmp_path: Path) -> None:
    result = service.import_mavlink(COMMON, tmp_path / "p", importers.Project("p"), ["NOPE"])
    assert not result.ok
    assert "not in the dialect: NOPE" in result.text


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_includes_are_read_once_and_extensions_come_last(tmp_path: Path) -> None:
    _write(tmp_path / "a.xml", "<mavlink><include>b.xml</include><messages></messages></mavlink>")
    _write(
        tmp_path / "b.xml",
        """<mavlink><include>a.xml</include><enums><enum name="E"><entry name="E_X"/>
        <entry name="E_Y"/><entry name="E_Z" value="2**4"/></enum></enums><messages>
        <message id="7" name="SAMPLE"><description>A sample.</description>
        <field type="uint8_t" name="small">small</field>
        <field type="uint32_t" name="big" enum="E">big</field>
        <field type="char[4]" name="tag">tag</field>
        <extensions/><field type="uint16_t" name="later">later</field>
        </message></messages></mavlink>""",
    )
    dialect = mavlink_xml.parse(tmp_path / "a.xml")
    assert [path.name for path in dialect.files] == ["b.xml", "a.xml"]
    (message,) = dialect.messages.values()
    assert [item.name for item in mavlink_xml.wire_order(message)] == [
        "big",
        "small",
        "tag",
        "later",
    ]
    assert dialect.enums["E"].entries == [("E_X", 0), ("E_Y", 1), ("E_Z", 16)]


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ('<!DOCTYPE m [<!ENTITY x "y">]><mavlink/>', "document type"),
        ("<mavlink><messages>", "cannot read"),
        (
            (
                '<mavlink><messages><message id="1" name="M"><field type="bool" name="f"/>'
                "</message></messages></mavlink>"
            ),
            "unknown type 'bool'",
        ),
        (
            (
                '<mavlink><messages><message id="1" name="A"/><message id="1" name="B"/>'
                "</messages></mavlink>"
            ),
            "message id 1 is both A and B",
        ),
    ],
)
def test_broken_dialects_are_refused(tmp_path: Path, text: str, problem: str) -> None:
    with pytest.raises(mavlink_xml.MavlinkImportError, match=problem):
        mavlink_xml.parse(_write(tmp_path / "d.xml", text))


def test_names(tmp_path: Path) -> None:
    assert mavlink_xml.camel("GLOBAL_POSITION_INT", upper=True) == "GlobalPositionInt"
    assert mavlink_xml.camel("time_boot_ms", upper=False) == "timeBootMs"
    _write(
        tmp_path / "k.xml",
        '<mavlink><messages><message id="3" name="K"><field type="uint8_t" name="class"/>'
        "</message></messages></mavlink>",
    )
    result = service.import_mavlink(tmp_path / "k.xml", tmp_path / "p", importers.Project("p"))
    assert result.ok, result.text
    assert "field class is called classValue" in result.text


def test_a_destination_that_is_not_empty_is_refused(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "x").write_text("", encoding="utf-8")
    with pytest.raises(service.ServiceError, match="not empty"):
        service.import_mavlink(COMMON, tmp_path / "p", importers.Project("p"))
