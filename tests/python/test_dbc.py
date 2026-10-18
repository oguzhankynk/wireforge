"""The DBC importer: what it reads, what it refuses, and the bytes against cantools.

cantools is an independent implementation of DBC files. For every message of two databases the
frames WireForge encodes from the imported project must equal the frames cantools encodes from the
same raw values, and both must decode them to the same values.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cantools
import pytest
from fastapi.testclient import TestClient

from wireforge import cli, dbc, importers, service
from wireforge.codec import ReferenceCodec
from wireforge.loader import load_project
from wireforge.server.app import create_app

if TYPE_CHECKING:
    from cantools.database.can import Database as CanDatabase

    from wireforge.model import Field, Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "07_can_dbc"
EDGE = Path(__file__).resolve().parent / "dbc" / "edge.dbc"
OPTIONS = importers.Project("net", "net")
SEED = 20260101
ROUNDS = 40
Value = int | float | str


class _Numbers:
    """A small deterministic generator (the same values on every run and platform)."""

    def __init__(self, seed: int) -> None:
        self.state = seed

    def randint(self, low: int, high: int) -> int:
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return low + (self.state >> 11) % (high - low + 1)

    def choice(self, items: list[int]) -> int:
        return items[self.randint(0, len(items) - 1)]


def _load(path: Path) -> CanDatabase:
    database = cantools.database.can.Database()
    database.add_dbc_file(str(path))
    return database


def _import(tmp_path: Path, source: Path, node: str | None = None) -> Project:
    result = service.import_dbc(source, tmp_path / "net", OPTIONS, node)
    assert result.ok, result.text
    project = load_project(tmp_path / "net").project
    assert project is not None
    return project


def _random_raw(project: Project, field: Field, rng: _Numbers) -> Value:
    if field.reserved:
        return 0
    if field.type in {"f32", "f64"}:
        return rng.randint(-4000, 4000) / 4.0
    if field.type in project.enums:
        return rng.choice(list(project.enums[field.type].values.values()))
    bits = field.bits or int(field.type[1:])
    low, high = (
        (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
        if field.type.startswith("i")
        else (0, (1 << bits) - 1)
    )
    low = low if field.min is None else max(low, int(field.min))
    high = high if field.max is None else min(high, int(field.max))
    return rng.randint(low, high)


def _names(project: Project, field: Field, raw: Value) -> Value:
    """Return the value the way the reference codec takes it (enums by member name)."""
    if field.type not in project.enums:
        return raw
    return next(name for name, value in project.enums[field.type].values.items() if value == raw)


def _compare(project: Project, database: CanDatabase, interface: str) -> int:
    codec = ReferenceCodec(project, None)
    rng = _Numbers(SEED)
    compared = 0
    for name in project.interfaces[interface].messages:
        message = project.messages[name.name]
        reference = database.get_message_by_name(message.name)
        for _ in range(ROUNDS):
            raw = {
                field.name: _random_raw(project, field, rng)
                for field in message.fields
                if not field.reserved
            }
            ours_value = {
                field.name: _names(project, field, raw[field.name])
                for field in message.fields
                if not field.reserved
            }
            expected = reference.encode(raw, scaling=False, padding=False, strict=True)
            actual = codec.encode_message(message.name, ours_value, interface)
            assert actual == expected, f"{message.name} {raw}"
            decoded = codec.decode_message(message.name, expected, interface)
            for field in message.fields:
                if not field.reserved:
                    assert decoded[field.name] == ours_value[field.name], (
                        f"{message.name}.{field.name}"
                    )
            compared += 1
    return compared


def test_the_example_agrees_with_cantools() -> None:
    project = load_project(EXAMPLE).project
    assert project is not None
    database = _load(EXAMPLE / "vehicle.dbc")
    assert _compare(project, database, "Gateway_rx") + _compare(project, database, "Gateway_tx")


def test_the_edge_cases_agree_with_cantools(tmp_path: Path) -> None:
    project = _import(tmp_path, EDGE)
    database = _load(EDGE)
    assert len(project.messages) == len(database.messages)
    assert _compare(project, database, "can") == len(project.messages) * ROUNDS


def test_physical_values_agree_with_cantools(tmp_path: Path) -> None:
    project = _import(tmp_path, EDGE)
    database = _load(EDGE)
    codec = ReferenceCodec(project, None)
    frame = database.get_message_by_name("MotorolaMix").encode(
        {"Across": 1234, "Negative": -17.75, "Flag": 3}
    )
    value = codec.decode_message("MotorolaMix", frame, "can")
    shown = service.decode(
        tmp_path / "net",
        frame,
        service.CodecTarget(message="MotorolaMix", interface="can"),
    ).data["physical"]
    assert value["Across"] == 1234
    assert shown == {"Negative": pytest.approx(-17.75)}


def test_the_float_bytes_are_ieee(tmp_path: Path) -> None:
    project = _import(tmp_path, EDGE)
    codec = ReferenceCodec(project, None)
    frame = codec.encode_message("Double", {"Precise": 1.5}, "can")
    assert struct.unpack("<d", frame)[0] == 1.5


def test_the_example_project_is_what_the_importer_writes(tmp_path: Path) -> None:
    options = importers.Project("can_dbc", "examples::can_dbc")
    result = service.import_dbc(EXAMPLE / "vehicle.dbc", tmp_path / "x", options, "Gateway")
    assert result.ok, result.text
    expected = load_project(EXAMPLE).project
    actual = load_project(tmp_path / "x").project
    assert expected is not None
    assert actual is not None
    assert expected.messages.keys() == actual.messages.keys()
    for name, message in expected.messages.items():
        assert [item.name for item in message.fields] == [
            item.name for item in actual.messages[name].fields
        ]
    assert expected.interfaces.keys() == actual.interfaces.keys()


def _document(text: str, *, node: str | None = None, skip: bool = False) -> dict[str, Any]:
    return dbc.from_dbc(text, OPTIONS, node, skip_unsupported=skip)[0]


def _error(text: str, *, node: str | None = None) -> str:
    with pytest.raises(dbc.DbcImportError) as caught:
        dbc.from_dbc(text, OPTIONS, node, source="n.dbc")
    return "; ".join(str(issue) for issue in caught.value.issues)


BASE = """BU_: A B
BO_ 16 First: 4 A
 SG_ X : 0|8@1+ (1,0) [0|0] "" B
"""


def test_a_multiplexed_signal_is_refused_with_its_line() -> None:
    text = BASE + ' SG_ Mode M : 8|8@1+ (1,0) [0|0] "" B\n SG_ Part m1 : 16|8@1+ (1,0) [0|0] "" B\n'
    problem = _error(text)
    assert "n.dbc:4:" in problem
    assert "message First, signal Mode: multiplexed signals are not supported" in problem


def test_unsupported_messages_can_be_left_out() -> None:
    text = (
        BASE
        + "BO_ 17 Long: 12 A\n"
        + ' SG_ Y : 0|8@1+ (1,0) [0|0] "" B\n'
        + "BO_ 18 Mixed: 4 A\n"
        + ' SG_ I : 0|8@1+ (1,0) [0|0] "" B\n'
        + ' SG_ M : 23|8@0+ (1,0) [0|0] "" B\n'
    )
    document, notes = dbc.from_dbc(text, OPTIONS, skip_unsupported=True, source="n.dbc")
    assert list(document["messages"]) == ["First"]
    assert any("Long" in note and "12 data bytes" in note for note in notes)
    assert any("Mixed" in note and "Intel and Motorola" in note for note in notes)
    assert "Long" in _error(text)


def test_overlap_and_outside_signals_are_refused() -> None:
    overlap = BASE + ' SG_ Z : 4|8@1+ (1,0) [0|0] "" B\n'
    assert "overlaps another signal" in _error(overlap)
    outside = BASE + ' SG_ Z : 28|8@1+ (1,0) [0|0] "" B\n'
    assert "does not fit in 4 bytes" in _error(outside)


def test_other_unsupported_input_is_refused() -> None:
    assert "the factor is 0" in _error(BASE.replace("(1,0)", "(0,0)"))
    assert "must fill whole bytes" in _error(BASE + "SIG_VALTYPE_ 16 X : 1;\n")
    assert "extended multiplexing" in _error(BASE + "SG_MUL_VAL_ 16 Part Mode 1-1;\n")
    assert "cannot read the signal" in _error(BASE + " SG_ broken\n")
    assert "cannot read the message" in _error("BO_ x y\n")
    assert "already used" in _error(BASE + BASE.split("\n", 1)[1])
    assert "unknown signal" in _error(BASE + 'VAL_ 16 Nope 1 "One";\n')
    assert "no message could be imported" in _error("BU_: A\n")
    assert "does not send or receive" in _error(BASE, node="C")
    assert "outside of a message" in _error(' SG_ X : 0|8@1+ (1,0) [0|0] "" B\n')
    assert "numbers" in _error(BASE.replace("(1,0)", "(a,0)"))


def test_nodes_select_the_interfaces() -> None:
    text = BASE + "BO_ 32 Second: 2 B\n" + ' SG_ Q : 0|8@1+ (1,0) [0|0] "" A\n'
    both = _document(text)
    assert list(both["interfaces"]) == ["can"]
    assert both["interfaces"]["can"]["direction"] == "both"
    for node, sent, received in (("A", "First", "Second"), ("B", "Second", "First")):
        interfaces = _document(text, node=node)["interfaces"]
        assert interfaces[f"{node}_tx"]["messages"] == [sent]
        assert interfaces[f"{node}_rx"]["messages"] == [received]
    only_sender = _document(BASE, node="A")["interfaces"]
    assert list(only_sender) == ["A_tx"]


def test_signals_become_fields() -> None:
    text = (
        'BO_ 3 Frame: 4 A\n SG_ Temp : 4|12@1+ (0.5,-40) [-40|300] "degC" B\n'
        ' SG_ Mode : 16|2@1+ (1,0) [0|3] "" B\n'
        'CM_ BO_ 3 "A frame.";\nCM_ SG_ 3 Temp "Temperature.";\n'
        'VAL_ 3 Mode 1 "On" 2 "Off" 3 "Fault";\n'
        'BA_ "GenMsgCycleTime" BO_ 3 100;\nBA_ "GenSigStartValue" SG_ 3 Mode 2;\n'
        'BA_ "GenSigStartValue" SG_ 3 Temp 20;\nBA_DEF_ BO_ "GenMsgCycleTime" INT 0 65535;\n'
    )
    document, notes = dbc.from_dbc(text, OPTIONS, source="n.dbc")
    message = document["messages"]["Frame"]
    assert message["description"] == "A frame. Cycle time 100 ms."
    temp, mode = message["fields"][1], message["fields"][2]
    assert temp == {
        "name": "Temp",
        "type": "u16",
        "bits": 12,
        "scale": 0.5,
        "offset": -40,
        "min": 0,
        "max": 680,
        "default": 120,
        "units": "degC",
        "description": "Temperature.",
    }
    assert mode["type"] == "Frame_Mode"
    assert mode["default"] == "Off"
    assert document["enums"]["Frame_Mode"] == {
        "type": "u8",
        "values": {"On": 1, "Off": 2, "Fault": 3},
    }
    assert notes == []


def test_a_value_table_that_is_not_an_enum_stays_in_the_description() -> None:
    text = (
        'BO_ 3 Frame: 1 A\n SG_ Mode : 0|2@1+ (1,0) [0|0] "" B\n'
        ' SG_ Gear : 2|2@1+ (2,0) [0|0] "" B\n'
        'VAL_ 3 Mode 0 "a b" 1 "a-b";\nVAL_ 3 Gear 0 "Low" 1 "High";\n'
    )
    fields = _document(text)["messages"]["Frame"]["fields"]
    assert "Values: 0 = a b; 1 = a-b." in fields[0]["description"]
    assert fields[0]["type"] == "u8"
    assert "Values: 0 = Low; 1 = High." in fields[1]["description"]


def test_limits_that_are_not_whole_raw_values_are_narrowed() -> None:
    text = 'BO_ 3 Frame: 1 A\n SG_ Value : 0|8@1+ (0.3,0) [0.4|60] "" B\n'
    document, notes = dbc.from_dbc(text, OPTIONS, source="n.dbc")
    assert (document["messages"]["Frame"]["fields"][0]["min"],) == (2,)
    assert any("narrowed" in note for note in notes)


def test_import_dbc_through_the_cli_and_the_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [
            "import-dbc",
            str(EXAMPLE / "vehicle.dbc"),
            str(tmp_path / "cli"),
            "--name",
            "net",
            "--node",
            "Gateway",
            "--skip-unsupported",
        ]
    )
    assert code == 0
    assert "imported vehicle.dbc" in capsys.readouterr().out
    client = TestClient(create_app(tmp_path / "workspace"))
    body = {
        "id": "net",
        "name": "net",
        "format": "dbc",
        "content": (EXAMPLE / "vehicle.dbc").read_text(encoding="utf-8"),
        "node": "Dashboard",
    }
    response = client.post("/api/import", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["ok"] is True
    again = client.post("/api/import", json={**body, "id": "other", "node": "Nobody"})
    assert again.json()["ok"] is False
    assert json.dumps(again.json())


def test_a_database_with_comments_symbols_and_attributes_is_read() -> None:
    text = (EXAMPLE / "vehicle.dbc").read_text(encoding="utf-8")
    db = dbc.parse(text)
    assert [message.name for message in db.messages.values()][:2] == ["EngineData", "BrakeStatus"]
    assert db.notes == []
    assert db.messages[256].cycle_time == 10
