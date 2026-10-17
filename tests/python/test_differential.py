from __future__ import annotations

import math
import stat
import struct
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wireforge import differential, golden
from wireforge.codec import ReferenceCodec, WireNaN
from wireforge.loader import load_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
BASIC = EXAMPLES / "01_basic_telemetry"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"
EDGE = EXAMPLES / "03_edge_cases"
PLUGINS = golden.load_plugins(AVIONICS / "plugins" / "vendor_sum.py")


def _project(location: Path) -> Project:
    result = load_project(location)
    assert result.project is not None
    return result.project


def _oracle_harness(codec: ReferenceCodec) -> differential.Harness:
    """Return a harness that answers like the reference codec (no mismatches expected)."""
    oracle = differential.Oracle(codec)

    def run(requests: Sequence[differential.Request]) -> list[str]:
        answers = []
        for request in requests:
            expected = oracle.expect(request)
            answers.append(expected.answer if expected.exact else "0 1 -")
        return answers

    return run


def test_rng_is_deterministic_and_bounded() -> None:
    first = differential.Rng(5)
    second = differential.Rng(5)
    values = [first.next() for _ in range(5)]
    assert values == [second.next() for _ in range(5)]
    rng = differential.Rng(0)
    assert all(3 <= rng.randint(3, 9) <= 9 for _ in range(200))
    assert all(0.0 <= rng.random() < 1.0 for _ in range(200))
    assert rng.randrange(0) == 0
    assert rng.choice("ab") in "ab"
    assert -1.0 <= rng.uniform(-1.0, 1.0) <= 1.0


def test_random_values_are_valid_in_every_profile() -> None:
    project = _project(AVIONICS)
    for profile in project.profiles:
        codec = ReferenceCodec(project, profile, PLUGINS)
        sampler = differential.RandomSampler(codec, differential.Rng(3))
        for message in project.messages.values():
            for _ in range(30):
                payload = codec.encode_message(message.name, sampler.struct(message.fields))
                assert codec.decode_message(message.name, payload)
    edge = _project(EDGE)
    codec = ReferenceCodec(edge)
    sampler = differential.RandomSampler(codec, differential.Rng(9))
    lengths = {len(sampler.struct(edge.messages["Everything"].fields)["name"]) for _ in range(60)}
    assert len(lengths) > 3, "random string lengths"


def test_oracle_answers() -> None:
    project = _project(EDGE)
    oracle = differential.Oracle(ReferenceCodec(project))
    payload = bytes.fromhex(golden.vectors(project)[1]["payload"])
    assert oracle.message("Everything", payload).answer == f"Ok Ok {payload.hex()}"
    assert oracle.message("Everything", payload[:-1]).answer == "Truncated - -"
    frame = oracle.frame("loopback", payload)
    assert frame.exact
    assert frame.answer == f"1 0 {payload.hex()}"
    rejected = oracle.frame("loopback", payload + b"\x00")
    assert not rejected.exact
    assert differential.matches(rejected, "0 1 -")
    assert differential.matches(rejected, "1 1 00"), "ambiguous: a part was rejected"
    assert not differential.matches(rejected, "1 0 00"), "accepted cleanly"


def test_requests_and_regressions(tmp_path: Path) -> None:
    request = differential.Request("M", "Everything", b"\x01\x02")
    assert request.line() == "M Everything 0102"
    assert differential.Request.parse("F loopback -").data == b""
    stored = tmp_path / "regressions"
    mismatch = differential.Mismatch(request, "Ok", "Truncated")
    path = differential.write_regressions(stored, [mismatch, mismatch])
    assert path is not None
    assert differential.write_regressions(stored, [mismatch]) == path
    assert path.read_text(encoding="utf-8") == "M Everything 0102\n", "stored once"
    (stored / "notes.txt").write_text("# comment\n\nF loopback 00 # trailing\n", encoding="utf-8")
    assert [item.line() for item in differential.read_regressions(stored)] == [
        "M Everything 0102",
        "F loopback 00",
    ]
    assert differential.read_regressions(tmp_path / "missing") == []
    assert differential.write_regressions(stored, []) is None


def test_runs_against_a_reference_harness() -> None:
    project = _project(AVIONICS)
    for profile in project.profiles:
        codec = ReferenceCodec(project, profile, PLUGINS)
        options = differential.Options(seed=2, profile=profile, values=10, frames=5)
        count, mismatches = differential.run(project, _oracle_harness(codec), options, PLUGINS)
        assert count > 300
        assert mismatches == []


def test_detects_and_stores_mismatches(tmp_path: Path) -> None:
    project = _project(BASIC)

    def broken(requests: Sequence[differential.Request]) -> list[str]:
        return ["Ok Ok 00" for _ in requests]

    options = differential.Options(values=3, frames=2, mutations=2, regressions=tmp_path)
    count, mismatches = differential.run(project, broken, options)
    assert 0 < len(mismatches) <= count
    differential.write_regressions(tmp_path, mismatches)
    again, _ = differential.run(project, broken, options)
    assert again == count + len({item.request.line() for item in mismatches}), "replayed"


def test_signaling_nan_round_trips_bit_exactly() -> None:
    project = _project(BASIC)
    codec = ReferenceCodec(project)
    payload = bytes.fromhex("2003bf2b715a7faf7305c3203168c41fdd1900")
    value = codec.decode_message("SensorReading", payload)
    assert isinstance(value["accel"]["x"], WireNaN)
    assert math.isnan(value["accel"]["x"])
    assert codec.encode_message("SensorReading", value) == payload
    little = WireNaN(struct.pack(">d", float("nan")))
    assert math.isnan(little)


def test_fuzz_seeds(tmp_path: Path) -> None:
    project = _project(AVIONICS)
    written = differential.write_fuzz_seeds(project, tmp_path, PLUGINS)
    assert written == 2 * len(golden.vectors(project, PLUGINS))
    seed = tmp_path / "platformA_our_to_nodeC_Command_pattern_payload"
    assert seed.read_bytes()[0] == list(project.messages).index("Command")
    frame = tmp_path / "platformA_our_to_nodeC_Command_pattern_frame"
    assert frame.read_bytes()[0] == len(project.messages) + 2, "third interface by name"


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    harness = tmp_path / "silent.sh"
    harness.write_text("#!/bin/sh\ncat > /dev/null\n", encoding="utf-8")
    harness.chmod(harness.stat().st_mode | stat.S_IXUSR)
    stored = tmp_path / "stored"
    args = [str(EDGE), "--harness", str(harness), "--values", "2", "--frames", "1"]
    assert differential.main([*args, "--regressions", str(stored)]) == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out
    assert "<no answer>" in out
    assert (stored / "found.txt").exists()
    seeds = tmp_path / "seeds"
    assert differential.main([str(EDGE), "--fuzz-seeds", str(seeds)]) == 0
    assert any(seeds.iterdir())
    failing = tmp_path / "failing.sh"
    failing.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    failing.chmod(failing.stat().st_mode | stat.S_IXUSR)
    runner = differential.process_harness(failing)
    with pytest.raises(RuntimeError, match="failed with 3"):
        runner([differential.Request("M", "Everything", b"")])
