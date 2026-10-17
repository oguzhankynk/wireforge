from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING

from wireforge import layout
from wireforge.loader import load_project

if TYPE_CHECKING:
    import pytest

    from wireforge.model import Project

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
BASIC = EXAMPLES / "01_basic_telemetry"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"
EDGE = EXAMPLES / "03_edge_cases"


def _project(location: Path) -> Project:
    result = load_project(location)
    assert result.project is not None
    return result.project


def _message(profile: layout.ProfileLayout, name: str) -> layout.MessageLayout:
    return next(item for item in profile.messages if item.name == name)


def _interface(profile: layout.ProfileLayout, name: str) -> layout.InterfaceLayout:
    return next(item for item in profile.interfaces if item.name == name)


def _row(message: layout.MessageLayout, path: str) -> layout.FieldLayout:
    return next(item for item in message.fields if item.path == path)


def test_basic_telemetry_sizes_and_offsets() -> None:
    (profile,) = layout.analyze(_project(BASIC)).profiles
    reading = _message(profile, "SensorReading")
    # sensorId 1 + state 1 + temperature 4 + accel 3*4 + sampleCount 1 + samples 0..4*2
    assert (reading.size_min, reading.size_max) == (19, 27)
    offsets = {row.path: (row.offset_min, row.size_min, row.size_max) for row in reading.fields}
    assert offsets["temperature"] == (2, 4, 4)
    assert offsets["accel.y"] == (10, 4, 4)
    assert offsets["sampleCount"] == (18, 1, 1)
    assert offsets["samples"] == (19, 0, 8)
    assert _row(reading, "samples").occurs == "0..4, count sampleCount"
    assert _row(reading, "temperature").units == "degC"
    link = _interface(profile, "sensor_to_us")
    assert (link.header_size, link.trailer_size, link.max_frame) == (6, 4, 64)
    assert (link.rx_buffer, link.tx_buffer, link.link_ram) == (128, 64, 256)
    assert (link.messages[0].frame_min, link.messages[0].frame_max) == (29, 37)
    assert link.trailer[0].role == "checksum frameCrc"
    assert profile.link_ram == 256


def test_avionics_profiles() -> None:
    report = layout.analyze(_project(AVIONICS))
    small, large = report.profiles
    assert (small.profile, large.profile) == ("platformA", "platformB")
    # flags 1 + hasAlt 1 + alt 0..2 + lat 8 + lon 8 + trackCount 1 + tracks 0..N*3 + ratio 4
    # (+ extra 1 on platformB)
    assert (_message(small, "NavSolution").size_min, _message(small, "NavSolution").size_max) == (
        23,
        34,
    )
    nav = _message(large, "NavSolution")
    assert (nav.size_min, nav.size_max) == (24, 41)
    assert _row(nav, "lat").offset_min == 2
    assert _row(nav, "lat").offset_max == 4, "after the optional alt"
    assert _row(nav, "tracks[].quality").offset_min == 21
    assert "extra" not in {row.path for row in _message(small, "NavSolution").fields}
    status = _message(small, "NodeBStatus")
    assert (status.size_min, status.size_max) == (10, 26)
    assert _row(status, "diag").occurs == "fill, 0..16"
    assert _interface(small, "nodeB_to_our").link_ram == 26 * 4
    assert _interface(small, "our_to_nodeA").max_frame == 128, "APP_MAX_FRAME"
    assert _interface(large, "our_to_nodeA").max_frame == 192, "profile value of APP_MAX_FRAME"
    node_c = _interface(large, "our_to_nodeC")
    assert node_c.messages[0].id == 0x30, "per-interface id override"
    assert (node_c.messages[0].payload_max, node_c.max_frame) == (5, 32)
    assert large.defines["MAX_TRACKS"] == 5
    assert _row(nav, "trackCount").range == "..MAX_TRACKS = 5"


def test_bit_maps_follow_the_bit_order() -> None:
    nav = _message(layout.analyze(_project(AVIONICS), "platformA").profiles[0], "NavSolution")
    lsb = {row.path: (row.bit_high, row.bit_low, row.group_size) for row in nav.fields}
    assert lsb["flags.valid"] == (0, 0, 1)
    assert lsb["flags.mode"] == (3, 1, 1)
    assert lsb["flags.spare"] == (7, 4, 1)
    everything = _message(layout.analyze(_project(EDGE)).profiles[0], "Everything")
    # magic 4 + big 8 + packed 2 + inner 1..5 + n 1 + maybeEmpty 0..16 + nameLen 1 + name 0..16
    assert (everything.size_min, everything.size_max) == (17, 53)
    msb = {row.path: (row.bit_high, row.bit_low) for row in everything.fields}
    assert msb["packed.delta"] == (15, 11)
    assert msb["packed.level"] == (10, 9)
    assert msb["packed.flag"] == (8, 8)
    assert msb["packed.spare"] == (7, 0)
    assert _row(everything, "magic").role == "const"
    assert _row(everything, "packed.spare").role == "reserved"


def test_exports() -> None:
    report = layout.analyze(_project(AVIONICS))
    text = layout.to_markdown(report)
    assert text.startswith("# ICD avionics_demo 2.1.0\n")
    assert "## Profile platformB" in text
    assert "| flags.mode | u8 | 0 | 1 | 3..1 |" in text
    assert "| lat | f64 | 2..4 | 8 |" in text
    assert "= **768 bytes**" in text
    rows = list(csv.DictReader(io.StringIO(layout.to_csv(report))))
    assert tuple(rows[0]) == layout.CSV_COLUMNS
    crc = next(row for row in rows if row["owner"] == "our_to_nodeA/trailer")
    assert crc["role"] == "checksum crc16ccitt"
    data = json.loads(layout.to_json(report))
    assert data["profiles"][1]["interfaces"][1]["link_ram"] == 768


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "icd.csv"
    assert layout.main([str(BASIC), "--format", "csv", "-o", str(output)]) == 0
    assert output.read_text(encoding="utf-8").startswith("profile,owner,path")
    assert layout.main([str(EDGE)]) == 0
    assert "### Message Everything" in capsys.readouterr().out
    assert layout.main([str(AVIONICS), "--profile", "nope"]) == 1
    assert "unknown profile" in capsys.readouterr().out
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "project.yaml").write_text(
        "schema_version: 1\nproject: p\nmessages:\n  M:\n    fields:\n"
        "      - { name: a, type: u8, default: 300 }\n",
        encoding="utf-8",
    )
    assert layout.main([str(broken)]) == 1
    assert "SR-6" in capsys.readouterr().out
