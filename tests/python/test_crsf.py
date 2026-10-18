"""The CRSF protocol model against crsf-parser, an independent implementation of it.

crsf-parser builds the same frames (length, type, payload and CRC-8) from the same values, reads the
frames the model writes as the same values, and finds the same CRC when a byte is changed.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wireforge.codec import CodecError, ReferenceCodec
from wireforge.loader import load_project

# crsf-parser has no type information, so it is used as a dynamic module.
handling = importlib.import_module("crsf_parser.handling")
payloads = importlib.import_module("crsf_parser.payloads")
PROTOCOL = Path(__file__).resolve().parents[2] / "protocols" / "crsf"
STEPS = 25
ANTENNAS = ("antenna_1", "antenna_2")
RF_MODES = ("fps_4", "fps_50", "fps_150")
POWERS = ("mw_0", "mw_10", "mw_25", "mw_100", "mw_500", "mw_1000", "mw_2000")


@pytest.fixture(scope="module")
def codec() -> ReferenceCodec:
    project = load_project(PROTOCOL).project
    assert project is not None
    return ReferenceCodec(project, None)


def _theirs_heartbeat(step: int) -> tuple[str, dict[str, Any], bytes]:
    value = {"originDeviceAddress": (step * 29) % 256}
    frame = handling.crsf_build_frame(
        payloads.PacketsTypes.HEARTBEAT, {"origin_device_address": value["originDeviceAddress"]}
    )
    return "heartbeat", value, bytes(frame)


def _theirs_battery(step: int) -> tuple[str, dict[str, Any], bytes]:
    value = {
        "voltage": (step * 977) % 65536,
        "current": (step * 4001) % 65536,
        "capacity": (step * 654321) % (1 << 24),
        "remaining": step * 4 % 101,
    }
    frame = handling.crsf_build_frame(payloads.PacketsTypes.BATTERY_SENSOR, value)
    return "battery_sensor", value, bytes(frame)


def _theirs_link(step: int) -> tuple[str, dict[str, Any], bytes]:
    value = {
        "uplinkRssiAnt1": 40 + step,
        "uplinkRssiAnt2": 41 + step,
        "uplinkLinkQuality": step * 4 % 101,
        "uplinkSnr": step - 12,
        "diversityActiveAntenna": ANTENNAS[step % 2],
        "rfMode": RF_MODES[step % 3],
        "uplinkTxPower": POWERS[step % 7],
        "downlinkRssi": 50 + step,
        "downlinkLinkQuality": 100 - step,
        "downlinkSnr": -step,
    }
    theirs = {
        "uplink_rssi_ant_1": value["uplinkRssiAnt1"],
        "uplink_rssi_ant_2": value["uplinkRssiAnt2"],
        "uplink_link_quality": value["uplinkLinkQuality"],
        "uplink_snr": value["uplinkSnr"],
        "diversity_active_antenna": ANTENNAS[step % 2].upper(),
        "rf_mode": {"fps_4": "RF_4_FPS", "fps_50": "RF_50_FPS", "fps_150": "RF_150_FPS"}[
            RF_MODES[step % 3]
        ],
        "uplink_tx_power": f"TX_POWER_{POWERS[step % 7][3:]}_MW",
        "downlink_rssi": value["downlinkRssi"],
        "downlink_link_quality": value["downlinkLinkQuality"],
        "downlink_snr": value["downlinkSnr"],
    }
    frame = handling.crsf_build_frame(payloads.PacketsTypes.LINK_STATISTICS, theirs)
    return "link_statistics", value, bytes(frame)


MESSAGES = {
    "heartbeat": "Heartbeat",
    "battery_sensor": "BatterySensor",
    "link_statistics": "LinkStatistics",
}
Builder = Callable[[int], tuple[str, dict[str, Any], bytes]]
BUILDERS = (_theirs_heartbeat, _theirs_battery, _theirs_link)


@pytest.mark.parametrize("build", BUILDERS)
def test_the_frames_have_the_same_bytes_and_values(codec: ReferenceCodec, build: Builder) -> None:
    for step in range(1, STEPS + 1):
        interface, value, theirs = build(step)
        message = MESSAGES[interface]
        assert codec.encode_frame(interface, message, value) == theirs
        decoded = codec.decode_frame(interface, theirs)
        assert decoded.message == message
        assert {key: decoded.value[key] for key in value} == value
        parsed = handling.crsf_frame.parse(theirs)
        assert parsed.header.type == codec.project.messages[message].fields[0].const


@pytest.mark.parametrize("build", BUILDERS)
def test_both_find_the_same_crc_error(codec: ReferenceCodec, build: Builder) -> None:
    interface, _, theirs = build(7)
    broken = bytearray(theirs)
    broken[3] ^= 0x01
    assert handling.crsf_frame_crc(bytes(broken)) != broken[-1]
    with pytest.raises(CodecError) as caught:
        codec.decode_frame(interface, bytes(broken))
    assert caught.value.status == "ChecksumMismatch"
