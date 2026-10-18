"""The J1939 protocol model against cantools: the identifier word and the data of the groups."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

import cantools
import pytest

from wireforge import scaling
from wireforge.codec import ReferenceCodec
from wireforge.loader import load_project

if TYPE_CHECKING:
    from cantools.database.can import Database

    from wireforge.model import Project

# cantools.j1939 has no type information, so it is used as a dynamic module.
j1939 = importlib.import_module("cantools.j1939")

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "protocols" / "j1939"
DBC = Path(__file__).resolve().parent / "dbc" / "j1939.dbc"
EXTENDED = 0x80
PRIORITY_SHIFT = 2
PGNS = {"Eec1": (0xF0, 0x04, "EEC1"), "Ccvs": (0xFE, 0xF1, "CCVS"), "Et1": (0xFE, 0xEE, "ET1")}
ROUNDS = 25


def _database() -> Database:
    database = cantools.database.can.Database()
    database.add_dbc_file(str(DBC))
    return database


def _raw(project: Project, message: str, step: int) -> dict[str, int]:
    """Raw values inside the limits of the model, different for every step."""
    result: dict[str, int] = {}
    for index, field in enumerate(project.messages[message].fields):
        if field.reserved:
            continue
        low, high = scaling.raw_limits(field)
        low = low if field.min is None else int(field.min)
        high = high if field.max is None else int(field.max)
        result[field.name] = low + (step * 37 + index * 11 + 5) % (high - low + 1)
    return result


@pytest.fixture(scope="module")
def codec() -> ReferenceCodec:
    project = load_project(PROTOCOL).project
    assert project is not None
    return ReferenceCodec(project, None)


@pytest.mark.parametrize("message", sorted(PGNS))
def test_the_data_has_the_same_bytes(codec: ReferenceCodec, message: str) -> None:
    reference = _database().get_message_by_name(PGNS[message][2])
    ours_names = [field.name for field in codec.project.messages[message].fields]
    theirs_names = [signal.name for signal in reference.signals]
    assert len([name for name in ours_names if not name.startswith("reserved")]) == len(
        theirs_names
    )
    for step in range(ROUNDS):
        raw = _raw(codec.project, message, step)
        renamed = dict(zip(theirs_names, raw.values(), strict=True))
        expected = reference.encode(renamed, scaling=False, padding=True, strict=True)
        assert codec.encode_message(message, raw, "can") == expected
        decoded = codec.decode_message(message, expected, "can")
        assert {name: decoded[name] for name in raw} == raw


@pytest.mark.parametrize("message", sorted(PGNS))
def test_the_identifier_word_is_the_one_cantools_packs(codec: ReferenceCodec, message: str) -> None:
    pdu_format, pdu_specific, _ = PGNS[message]
    for priority, address in ((0, 0), (3, 0x11), (6, 0x2A), (7, 0xFE)):
        control = EXTENDED | priority << PRIORITY_SHIFT
        header = {"control": control, "sourceAddress": address}
        value = _raw(codec.project, message, 3)
        frame = codec.encode_frame("can", message, value, header)
        packed = j1939.frame_id_pack(priority, 0, 0, pdu_format, pdu_specific, address)
        assert int.from_bytes(frame[:4], "big") == packed | 0x80000000
        assert frame[4] == 8
        decoded = codec.decode_frame("can", frame)
        assert decoded.message == message
        assert decoded.header["sourceAddress"] == address
        assert {name: decoded.value[name] for name in value} == value


def test_the_values_above_250_are_refused(codec: ReferenceCodec) -> None:
    value = _raw(codec.project, "Et1", 1)
    value["coolantTemperature"] = 251
    with pytest.raises(Exception, match="OutOfRange"):
        codec.encode_message("Et1", value, "can")
