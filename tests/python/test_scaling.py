"""Scale and offset: the physical value of a field, the limits of its setter, the decode result."""

from __future__ import annotations

from pathlib import Path

import pytest

from wireforge import scaling, service
from wireforge.loader import load_project
from wireforge.model import Field, Project, Source

MODEL = Path(__file__).resolve().parents[2] / "tests" / "models" / "scaling"
SOURCE = Source(file="x", line=1, pointer="/")


def _field(
    type_name: str,
    bits: int | None = None,
    scale: float | None = None,
    offset: float | None = None,
) -> Field:
    return Field(name="f", type=type_name, source=SOURCE, bits=bits, scale=scale, offset=offset)


def _project() -> Project:
    project = load_project(MODEL).project
    assert project is not None
    return project


def test_defaults_and_the_physical_value() -> None:
    item = _field("u8", scale=0.5)
    assert (scaling.scale_of(item), scaling.offset_of(item)) == (0.5, 0.0)
    assert scaling.physical(_field("u8", offset=-40), 100) == 60
    assert scaling.is_scaled(item)
    assert not scaling.is_scaled(_field("u8"))


@pytest.mark.parametrize(
    ("type_name", "bits", "limits"),
    [
        ("u8", None, (0, 255)),
        ("i8", None, (-128, 127)),
        ("u16", 12, (0, 4095)),
        ("i32", 31, (-(1 << 30), (1 << 30) - 1)),
        ("u64", None, (0, (1 << 64) - 1)),
    ],
)
def test_raw_limits(type_name: str, bits: int | None, limits: tuple[int, int]) -> None:
    assert scaling.raw_limits(_field(type_name, bits)) == limits


def test_raw_limits_need_an_integer() -> None:
    with pytest.raises(ValueError, match="no integer limits"):
        scaling.raw_limits(_field("f32"))


def test_the_checks_round_to_the_nearest_raw_value() -> None:
    narrow = _field("u8")
    assert scaling.lower_check(narrow) == "raw > -0.5"
    assert scaling.upper_check(narrow) == "raw < 255.5"
    wide = _field("i64")
    assert scaling.lower_check(wide) == f"raw >= {float(-(1 << 63))!r}"
    assert scaling.upper_check(wide) == f"raw < {float(1 << 63)!r}"


def test_the_probe_stays_inside_the_exact_doubles_and_leaves_the_range() -> None:
    wide = scaling.probe(_field("u64"))
    assert (wide.low, wide.high) == (0, scaling.SAFE_RAW)
    assert wide.below < 0
    assert wide.above > (1 << 64) - 1
    narrow = scaling.probe(_field("i8"))
    assert (narrow.low, narrow.high, narrow.below, narrow.above) == (-128, 127, -258, 256)


def test_literals() -> None:
    assert scaling.number(-0.5) == "(-0.5)"
    assert scaling.number(2) == "2.0"
    assert scaling.widened(_field("f64"), "x") == "x"
    assert scaling.widened(_field("f32"), "x") == "static_cast<double>(x)"
    assert scaling.function_names("Msg", _field("u8")) == ("physical_Msg_f", "set_physical_Msg_f")


def test_physical_values_of_a_decoded_message() -> None:
    project = _project()
    message = project.messages["Climate"]
    value = {
        "temperature": 100,
        "pressure": 10,
        "counter": 5,
        "gain": 1.5,
        "ratio": 2.0,
        "pair": {"low": 4, "high": -1},
    }
    assert scaling.physical_values(project, message, value) == {
        "temperature": 21.0,
        "pressure": 1.0,
        "counter": 1005.0,
        "gain": 4.0,
        "ratio": -0.5,
        "pair": {"low": -18.0, "high": -4.0},
    }
    assert scaling.physical_values(project, project.messages["Nibbles"], {}) == {}


def test_decode_reports_the_physical_values_only_when_there_are_some() -> None:
    target = service.CodecTarget(message="Nibbles", profile="basic")
    result = service.decode(MODEL, bytes([0x2B]), target)
    assert result.data["physical"] == {"a": 5.5, "b": -3.0}
    encoded = service.encode(PLAIN, {}, service.CodecTarget(message="SensorReading"))
    plain = service.decode(
        PLAIN, bytes.fromhex(encoded.data["payload"]), service.CodecTarget(message="SensorReading")
    )
    assert plain.ok
    assert "physical" not in plain.data


PLAIN = Path(__file__).resolve().parents[2] / "examples" / "01_basic_telemetry"
