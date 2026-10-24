"""The MAVLink 2 protocol model against pymavlink, an independent implementation of it.

pymavlink packs the same messages to the same frames (the trailing zero bytes of a payload left
out, the CRC_EXTRA byte of each message in the checksum), reads the frames the model writes as the
same field values, and refuses a broken checksum like the model does. The extension fields of a
message are zero when a sender leaves them out, and signed frames are refused.
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
common = importlib.import_module("pymavlink.dialects.v20.common")
PROTOCOL = Path(__file__).resolve().parents[2] / "protocols" / "mavlink2"
ROUNDS = 12
HEADER_AND_CHECKSUM = 12
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
GPS = (
    "timeUsec",
    "fixType",
    "lat",
    "lon",
    "alt",
    "eph",
    "epv",
    "vel",
    "cog",
    "satellitesVisible",
    "altEllipsoid",
    "hAcc",
    "vAcc",
    "velAcc",
    "hdgAcc",
    "yaw",
)
ACK = ("command", "result", "progress", "resultParam2", "targetSystem", "targetComponent")


def _values(value: dict[str, Any], names: tuple[str, ...]) -> list[Any]:
    return [value[name] for name in names]


def _cases(step: int) -> list[tuple[str, dict[str, Any], Any]]:
    """Return (our message, our value, pymavlink's message builder) for one step.

    Every other step leaves the fields at the end of a message zero, so the frames lose their
    trailing zero bytes.
    """
    quarter = (step * 3 - 20) / 8.0
    tail = step % 2
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
            "GpsRawInt",
            {
                "timeUsec": step * 1_000_003,
                "fixType": step % 7,
                "lat": 473977418 - step,
                "lon": -1000000 * step,
                "alt": 500000 + step,
                "eph": 100 + step,
                "epv": 200 + step,
                "vel": step * 3,
                "cog": (step * 1234) % 36000,
                "satellitesVisible": step % 20,
                "altEllipsoid": tail * (510000 + step),
                "hAcc": tail * 1500,
                "vAcc": tail * 2500,
                "velAcc": tail * 300,
                "hdgAcc": tail * 4000,
                "yaw": tail * (step * 100 + 1),
            },
            lambda mav, v: mav.gps_raw_int_encode(*_values(v, GPS)),
        ),
        (
            "Attitude",
            {
                "timeBootMs": step * 1000,
                "roll": quarter,
                "pitch": -quarter / 2,
                "yaw": quarter * 2,
                "rollspeed": 0.125 * step * tail,
                "pitchspeed": -0.5 * tail,
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
                "vz": 3 * tail,
                "hdg": tail * ((step * 1234) % 36000),
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
        (
            "CommandAck",
            {
                "command": 400 + step,
                "result": step % 5,
                "progress": tail * step,
                "resultParam2": tail * -step,
                "targetSystem": tail * 255,
                "targetComponent": tail * 190,
            },
            lambda mav, v: mav.command_ack_encode(*_values(v, ACK)),
        ),
    ]


def test_the_frames_have_the_same_bytes(codec: ReferenceCodec) -> None:
    trimmed = 0
    for step in range(1, ROUNDS + 1):
        system, component, sequence = 1 + step, 2 + step % 5, step * 21 % 256
        for name, value, build in _cases(step):
            theirs = build(_mav(system, component, sequence), value).pack(
                _mav(system, component, sequence)
            )
            header = {"sequence": sequence, "systemId": system, "componentId": component}
            ours = codec.encode_frame("link", name, value, header)
            assert ours == bytes(theirs), name
            full = len(codec.encode_message(name, value, "link"))
            trimmed += len(ours) < HEADER_AND_CHECKSUM + full
    assert trimmed > ROUNDS, "the cases must include frames without their trailing zero bytes"


def _snake(name: str) -> str:
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in name)


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


def test_the_model_reads_what_pymavlink_writes(codec: ReferenceCodec) -> None:
    for step in range(1, ROUNDS + 1):
        for name, value, build in _cases(step):
            mav = _mav(5, 6, step)
            decoded = codec.decode_frame("link", bytes(build(mav, value).pack(mav)))
            assert decoded.message == name
            for key, expected in value.items():
                assert pytest.approx(decoded.value[key]) == expected, key
            assert decoded.header["systemId"] == 5
            assert decoded.header["componentId"] == 6


def test_extension_fields_are_zero_when_not_sent(codec: ReferenceCodec) -> None:
    """A sender that knows only the base fields of COMMAND_ACK sends three payload bytes."""
    frame = codec.encode_frame("link", "CommandAck", {"command": 400, "result": 4})
    assert len(frame) == HEADER_AND_CHECKSUM + 3
    decoded = codec.decode_frame("link", frame)
    assert decoded.value["command"] == 400
    assert decoded.value["targetSystem"] == 0
    assert decoded.value["resultParam2"] == 0


def test_extension_fields_this_model_does_not_know_are_refused(codec: ReferenceCodec) -> None:
    """ATTITUDE with four more payload bytes (an extension of a newer definition) is refused."""
    frame = codec.encode_frame("link", "Attitude", {"timeBootMs": 7, "yawspeed": 1.5})
    payload = frame[10:-2] + bytes([1, 2, 3, 4])
    longer = bytes([frame[0], len(payload)]) + frame[2:10] + payload
    plugin = service.load_plugins(service.find_plugins(PROTOCOL))
    assert plugin is not None
    checksum = plugin["mav2sum::Crc"](longer)
    with pytest.raises(CodecError) as caught:
        codec.decode_frame("link", longer + checksum.to_bytes(2, "little"))
    assert caught.value.status == "TrailingBytes"


def test_both_refuse_a_broken_checksum(codec: ReferenceCodec) -> None:
    name, value, _ = _cases(3)[2]
    frame = bytearray(codec.encode_frame("link", name, value, {"sequence": 1}))
    frame[12] ^= 0x01
    with pytest.raises(Exception, match="invalid MAVLink CRC"):
        common.MAVLink(None).parse_char(bytes(frame))
    with pytest.raises(CodecError) as caught:
        codec.decode_frame("link", bytes(frame))
    assert caught.value.status == "ChecksumMismatch"


def test_a_signed_frame_is_refused(codec: ReferenceCodec) -> None:
    mav = _mav(1, 1, 1)
    mav.signing.secret_key = bytes(range(32))
    mav.signing.link_id = 1
    mav.signing.sign_outgoing = True
    mav.signing.timestamp = 1
    frame = bytes(mav.heartbeat_encode(2, 3, 81, 0, 4, 3).pack(mav))
    assert frame[2] & 0x01, "pymavlink set the signed flag"
    without_signature = frame[: len(frame) - 13]
    with pytest.raises(CodecError) as caught:
        codec.decode_frame("link", without_signature)
    assert caught.value.status == "ConstMismatch"
