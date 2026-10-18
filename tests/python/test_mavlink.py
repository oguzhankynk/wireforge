"""The MAVLink 1 protocol model against pymavlink, an independent implementation of it.

pymavlink packs the same messages to the same frames (including the CRC_EXTRA byte of each
message), reads the frames the model writes as the same field values, and refuses a broken
checksum like the model does.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, TypeAlias

import pytest

from wireforge import service
from wireforge.codec import CodecError, ReferenceCodec
from wireforge.loader import load_project

# pymavlink has no type information, so it is used as a dynamic module.
common = importlib.import_module("pymavlink.dialects.v10.common")
PROTOCOL = Path(__file__).resolve().parents[2] / "protocols" / "mavlink1"
ROUNDS = 12
Mav: TypeAlias = Any  # the pymavlink object has no type information


@pytest.fixture(scope="module")
def codec() -> ReferenceCodec:
    project = load_project(PROTOCOL).project
    assert project is not None
    return ReferenceCodec(project, None, service.load_plugins(service.find_plugins(PROTOCOL)))


def _mav(system: int, component: int, sequence: int) -> Mav:
    mav = common.MAVLink(None, srcSystem=system, srcComponent=component)
    mav.seq = sequence
    return mav


ATTITUDE = ("timeBootMs", "roll", "pitch", "yaw", "rollspeed", "pitchspeed", "yawspeed")
PARAMS = tuple(f"param{n}" for n in range(1, 8))
POSITION = ("timeBootMs", "lat", "lon", "alt", "relativeAlt", "vx", "vy", "vz", "hdg")
COMMAND = ("targetSystem", "targetComponent", "command", "confirmation", *PARAMS)


def _values(value: dict[str, Any], names: tuple[str, ...]) -> list[Any]:
    return [value[name] for name in names]


def _cases(step: int) -> list[tuple[str, dict[str, Any], Any]]:
    """Return (our message, our value, pymavlink's message builder) for one step."""
    quarter = (step * 3 - 20) / 8.0
    return [
        (
            "Heartbeat",
            {
                "customMode": step * 100003,
                "type": step % 30,
                "autopilot": step % 20,
                "baseMode": (step * 17) % 256,
                "systemStatus": step % 9,
            },
            lambda mav, v: mav.heartbeat_encode(
                v["type"], v["autopilot"], v["baseMode"], v["customMode"], v["systemStatus"], 3
            ),
        ),
        (
            "Attitude",
            {
                "timeBootMs": step * 1000,
                "roll": quarter,
                "pitch": -quarter / 2,
                "yaw": quarter * 2,
                "rollspeed": 0.125 * step,
                "pitchspeed": -0.5,
                "yawspeed": 0.0,
            },
            lambda mav, v: mav.attitude_encode(*_values(v, ATTITUDE)),
        ),
        (
            "GlobalPositionInt",
            {
                "timeBootMs": step * 77,
                "lat": 473977418 - step,
                "lon": -1000000 * step,
                "alt": 500000 + step,
                "relativeAlt": -step * 10,
                "vx": step * 5,
                "vy": -step,
                "vz": 3,
                "hdg": (step * 1234) % 36000,
            },
            lambda mav, v: mav.global_position_int_encode(*_values(v, POSITION)),
        ),
        (
            "CommandLong",
            {
                **{f"param{n}": quarter * n for n in range(1, 8)},
                "command": 400 + step,
                "targetSystem": step % 250,
                "targetComponent": 1,
                "confirmation": step % 2,
            },
            lambda mav, v: mav.command_long_encode(*_values(v, COMMAND)),
        ),
    ]


def test_the_frames_have_the_same_bytes(codec: ReferenceCodec) -> None:
    for step in range(1, ROUNDS + 1):
        system, component, sequence = 1 + step, 2 + step % 5, step * 21 % 256
        for name, value, build in _cases(step):
            theirs = build(_mav(system, component, sequence), value).pack(
                _mav(system, component, sequence)
            )
            header = {"sequence": sequence, "systemId": system, "componentId": component}
            assert codec.encode_frame("link", name, value, header) == bytes(theirs), name


def test_pymavlink_reads_what_the_model_writes(codec: ReferenceCodec) -> None:
    reader = common.MAVLink(None)
    for step in range(1, ROUNDS + 1):
        for name, value, _ in _cases(step):
            frame = codec.encode_frame("link", name, value, {"sequence": step})
            parsed = reader.parse_char(frame)
            assert parsed is not None
            info = codec.project.messages[name].message
            assert info is not None
            assert parsed.get_msgId() == info.id
            assert parsed.get_seq() == step
            theirs = parsed.to_dict()
            for field in codec.project.messages[name].fields:
                if field.const is not None:
                    continue
                assert pytest.approx(theirs[_snake(field.name)]) == value[field.name], field.name


def _snake(name: str) -> str:
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in name)


def test_the_model_reads_what_pymavlink_writes(codec: ReferenceCodec) -> None:
    for step in range(1, ROUNDS + 1):
        for name, value, build in _cases(step):
            mav = _mav(5, 6, step)
            decoded = codec.decode_frame("link", bytes(build(mav, value).pack(mav)))
            assert decoded.message == name
            for key, expected in value.items():
                assert pytest.approx(decoded.value[key]) == expected, key
            assert decoded.header["systemId"] == 5


def test_both_refuse_a_broken_checksum(codec: ReferenceCodec) -> None:
    name, value, _ = _cases(3)[1]
    frame = bytearray(codec.encode_frame("link", name, value, {"sequence": 1}))
    frame[9] ^= 0x01
    with pytest.raises(Exception, match="invalid MAVLink CRC"):
        common.MAVLink(None).parse_char(bytes(frame))
    with pytest.raises(CodecError) as caught:
        codec.decode_frame("link", bytes(frame))
    assert caught.value.status == "ChecksumMismatch"
