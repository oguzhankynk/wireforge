"""The UBX protocol model against pyubx2, an independent implementation of the same protocol.

For every message the frame WireForge encodes must equal the frame pyubx2 builds from the same
payload, pyubx2 must read the payload as the fields of the model (flags, scaled angles, strings),
and a broken checksum must be refused by both.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from wireforge import scaling, service
from wireforge.codec import CodecError, ReferenceCodec
from wireforge.loader import load_project

# pyubx2 publishes no type information, so it is used as a dynamic module.
pyubx2 = importlib.import_module("pyubx2")
GET, SET, VALCKSUM = pyubx2.GET, pyubx2.SET, pyubx2.VALCKSUM
UBXMessage, UBXParseError, UBXReader = pyubx2.UBXMessage, pyubx2.UBXParseError, pyubx2.UBXReader
PROTOCOL = Path(__file__).resolve().parents[2] / "protocols" / "ubx"
INTERFACE = "receiver"
NAV_PVT_BYTES = 92
SEED = 7


@pytest.fixture(scope="module")
def codec() -> ReferenceCodec:
    project = load_project(PROTOCOL).project
    assert project is not None
    return ReferenceCodec(project, None, service.load_plugins(service.find_plugins(PROTOCOL)))


def _bytes(count: int, salt: int) -> bytes:
    return bytes((index * 37 + salt * 11 + SEED) % 256 for index in range(count))


def _frame(codec: ReferenceCodec, message: str, value: dict[str, Any]) -> bytes:
    return codec.encode_frame(INTERFACE, message, value)


def test_the_example_frame_of_the_checksum() -> None:
    theirs = UBXMessage("CFG", "CFG-RATE", SET, measRate=1000, navRate=1, timeRef=1)
    assert theirs.serialize() == bytes.fromhex("b56206080600e803010001000139")


def test_cfg_rate_has_the_same_bytes(codec: ReferenceCodec) -> None:
    for rate, nav, ref in ((1000, 1, 1), (100, 10, 0), (65535, 65535, 65535)):
        ours = _frame(codec, "CfgRate", {"measRate": rate, "navRate": nav, "timeRef": ref})
        theirs = UBXMessage("CFG", "CFG-RATE", SET, measRate=rate, navRate=nav, timeRef=ref)
        assert ours == theirs.serialize()


@pytest.mark.parametrize(("message", "name"), [("AckAck", "ACK-ACK"), ("AckNak", "ACK-NAK")])
def test_the_acknowledgements_have_the_same_bytes(
    codec: ReferenceCodec, message: str, name: str
) -> None:
    for cls, msg in ((0x06, 0x08), (0x0A, 0x04), (0xFF, 0x00)):
        ours = _frame(codec, message, {"clsId": cls, "msgId": msg})
        theirs = UBXMessage("ACK", name, GET, clsID=cls, msgID=msg)
        assert ours == theirs.serialize()


def test_nav_pvt_has_the_same_bytes_and_the_same_fields(codec: ReferenceCodec) -> None:
    for salt in range(20):
        decoded = codec.decode_message("NavPvt", _bytes(NAV_PVT_BYTES, salt), INTERFACE)
        payload = codec.encode_message("NavPvt", decoded, INTERFACE)
        theirs = UBXMessage("NAV", "NAV-PVT", GET, payload=payload)
        assert _frame(codec, "NavPvt", decoded) == theirs.serialize()
        project = codec.project
        shown = scaling.physical_values(project, project.messages["NavPvt"], decoded)
        for ours_name, their_name in (
            ("iTow", "iTOW"), ("year", "year"), ("month", "month"), ("day", "day"),
            ("hour", "hour"), ("min", "min"), ("sec", "second"), ("tAcc", "tAcc"),
            ("nano", "nano"), ("fixType", "fixType"), ("numSv", "numSV"), ("height", "height"),
            ("hMsl", "hMSL"), ("hAcc", "hAcc"), ("vAcc", "vAcc"), ("velN", "velN"),
            ("velE", "velE"), ("velD", "velD"), ("gSpeed", "gSpeed"), ("sAcc", "sAcc"),
        ):  # fmt: skip
            assert decoded[ours_name] == getattr(theirs, their_name), ours_name
        for ours_name, their_name in (
            ("lon", "lon"), ("lat", "lat"), ("headMot", "headMot"), ("headAcc", "headAcc"),
            ("pDop", "pDOP"), ("headVeh", "headVeh"), ("magDec", "magDec"), ("magAcc", "magAcc"),
        ):  # fmt: skip
            assert shown[ours_name] == pytest.approx(getattr(theirs, their_name)), ours_name
        for group, names in (
            ("valid", ("validDate", "validTime", "fullyResolved", "validMag")),
            ("flags", ("gnssFixOk", "diffSoln", "psmState", "headVehValid", "carrSoln")),
            ("flags2", ("confirmedAvai", "confirmedDate", "confirmedTime")),
        ):
            for name in names:
                assert int(decoded[group][name]) == getattr(theirs, name), name


def test_mon_ver_has_the_same_bytes_and_the_same_strings(codec: ReferenceCodec) -> None:
    value = {
        "swVersion": "EXT CORE 1.00 (61b2dd)",
        "hwVersion": "00190000",
        "extensions": [{"text": "ROM BASE 0x118B2060"}, {"text": "GPS;GLO;GAL;BDS"}],
    }
    payload = codec.encode_message("MonVer", value, INTERFACE)
    theirs = UBXMessage("MON", "MON-VER", GET, payload=payload)
    assert _frame(codec, "MonVer", value) == theirs.serialize()
    assert theirs.swVersion.rstrip(b"\0").decode() == value["swVersion"]
    assert theirs.hwVersion.rstrip(b"\0").decode() == value["hwVersion"]
    assert theirs.extension_01.rstrip(b"\0").decode() == "ROM BASE 0x118B2060"
    assert theirs.extension_02.rstrip(b"\0").decode() == "GPS;GLO;GAL;BDS"
    assert (
        codec.decode_frame(INTERFACE, theirs.serialize()).value["extensions"] == value["extensions"]
    )


def test_both_refuse_a_broken_checksum(codec: ReferenceCodec) -> None:
    frame = bytearray(_frame(codec, "CfgRate", {"measRate": 200, "navRate": 5, "timeRef": 1}))
    frame[8] ^= 0x01
    with pytest.raises(UBXParseError):
        UBXReader.parse(bytes(frame), validate=VALCKSUM)
    with pytest.raises(CodecError) as caught:
        codec.decode_frame(INTERFACE, bytes(frame))
    assert caught.value.status == "ChecksumMismatch"


def test_both_read_what_the_other_wrote(codec: ReferenceCodec) -> None:
    theirs = UBXMessage("CFG", "CFG-RATE", SET, measRate=250, navRate=2, timeRef=3).serialize()
    decoded = codec.decode_frame(INTERFACE, theirs)
    assert decoded.message == "CfgRate"
    assert decoded.value == {"measRate": 250, "navRate": 2, "timeRef": 3}
    read = UBXReader.parse(_frame(codec, "CfgRate", decoded.value), validate=VALCKSUM)
    assert (read.measRate, read.navRate, read.timeRef) == (250, 2, 3)
