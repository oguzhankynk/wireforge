"""Generated C: snapshots, what is refused, names, and a real compile-and-run against the codec."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wireforge import bundle, cli, expr, generate, test_project
from wireforge.c_model import CModel, checksum_size, encoding_initializer
from wireforge.c_names import CGenerationError, CNames, prefix_of
from wireforge.c_tests import status_constant
from wireforge.codec import ReferenceCodec
from wireforge.generate import Banner
from wireforge.loader import load_project

if TYPE_CHECKING:
    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime" / "include"
EXAMPLES = sorted(
    path.parent
    for folder in ("examples", "protocols")
    for path in (ROOT / folder).glob("*/project.yaml")
)
SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = os.environ.get("WIREFORGE_UPDATE_SNAPSHOTS") == "1"
COMPILER = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def _project(directory: Path) -> Project:
    result = load_project(directory)
    assert result.project is not None, [str(issue) for issue in result.issues]
    return result.project


def _render(example: Path) -> dict[str, str]:
    content_hash = bundle.build_bundle(example)["bundle"]["content_hash"]
    return generate.render(_project(example), content_hash, "c")


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_generated_c_matches_snapshots(example: Path) -> None:
    try:
        files = _render(example)
    except CGenerationError:
        pytest.skip("generated C does not support this model yet")
    for relative, text in files.items():
        snapshot = SNAPSHOTS / (relative + ".snap")
        if UPDATE:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(text, encoding="utf-8")
        assert snapshot.read_text(encoding="utf-8") == text, (
            f"{relative} changed; review and rerun with WIREFORGE_UPDATE_SNAPSHOTS=1"
        )


def test_the_c_output_is_deterministic() -> None:
    example = ROOT / "examples" / "01_basic_telemetry"
    assert _render(example) == _render(example)


def test_the_files_of_c_are_headers_and_sources() -> None:
    files = _render(ROOT / "examples" / "01_basic_telemetry")
    assert sorted(files) == [
        "basic_telemetry/interfaces/sensor_to_us.c",
        "basic_telemetry/interfaces/sensor_to_us.h",
        "basic_telemetry/model.c",
        "basic_telemetry/model.h",
    ]


def test_user_types_are_refused() -> None:
    with pytest.raises(CGenerationError, match="user types"):
        _render(ROOT / "examples" / "04_user_type")


def test_a_custom_checksum_needs_a_c_function(tmp_path: Path) -> None:
    text = (ROOT / "examples" / "02_multi_interface_avionics" / "project.yaml").read_text()
    source = ROOT / "examples" / "02_multi_interface_avionics"
    shutil.copytree(source, tmp_path / "x")
    root = tmp_path / "x" / "project.yaml"
    root.write_text(
        text.replace("    c_impl: vendor_sum_c\n    c_include: vendor_sum.h\n", ""),
        encoding="utf-8",
    )
    with pytest.raises(CGenerationError, match="c_impl"):
        CModel(_project(tmp_path / "x"), Banner("x", None, "hash"))


def test_a_word_of_the_c_language_is_refused(tmp_path: Path) -> None:
    (tmp_path / "project.yaml").write_text(
        "schema_version: 1\nproject: p\nversion: '1.0.0'\n"
        "messages:\n  M:\n    id: 1\n    fields:\n      - { name: restrict, type: u8 }\n",
        encoding="utf-8",
    )
    with pytest.raises(CGenerationError, match="word of the C language"):
        CModel(_project(tmp_path), Banner("p", None, "hash"))


def test_generate_through_the_command_line(tmp_path: Path) -> None:
    code = cli.main(
        [
            "generate",
            str(ROOT / "examples" / "01_basic_telemetry"),
            "-o",
            str(tmp_path),
            "--language",
            "c",
        ]
    )
    assert code == cli.EXIT_OK
    assert (tmp_path / "basic_telemetry" / "model.c").exists()
    assert not (tmp_path / "basic_telemetry" / "model.hpp").exists()
    refused = cli.main(
        [
            "generate",
            str(ROOT / "examples" / "04_user_type"),
            "-o",
            str(tmp_path),
            "--language",
            "c",
        ]
    )
    assert refused != cli.EXIT_OK


def test_the_test_project_of_c_is_written(tmp_path: Path) -> None:
    issues = generate.generate(
        ROOT / "examples" / "01_basic_telemetry",
        tmp_path,
        test_project.TestOptions(language="c"),
    )
    assert issues == []
    tests = tmp_path / "basic_telemetry" / "generated_tests"
    cmake = (tests / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "LANGUAGES C CXX" in cmake
    assert '--filter "basic_telemetry/model[.]c"' in cmake
    for name in ("api.hpp", "test_support.hpp", "message_test.cpp", "golden_test.cpp"):
        assert (tests / name).exists()
    assert "tidy" not in (tests / "CMakePresets.json").read_text(encoding="utf-8")


def test_names_and_literals() -> None:
    project = _project(ROOT / "examples" / "02_multi_interface_avionics")
    names = CNames(project)
    assert prefix_of(project) == names.prefix
    assert names.tname("Foo") == f"{names.prefix}_Foo"
    assert names.checksum_function("crc") == f"{names.prefix}_checksum_crc"
    assert names.capacity(4) == "4U"
    assert names.capacity("MAX_ITEMS").startswith("((size_t)(")
    assert "INT64_C(" in names.value("2 * 3")


def test_status_constants() -> None:
    assert status_constant("Ok") == "WF_OK"
    assert status_constant("OutOfRange") == "WF_OUT_OF_RANGE"
    assert status_constant("BufferTooSmall") == "WF_BUFFER_TOO_SMALL"


def test_expressions_in_c() -> None:
    node = expr.parse("min(a, 3) + ceil_div(b, 2) * (c > 1 ? 1 : 0)")
    text = expr.to_c(node)
    assert "wf_expr_min(" in text
    assert "wf_expr_ceil_div(" in text
    assert text.count("INT64_C(") >= 4
    assert expr.to_c(expr.parse("!a && b")).count("int64_t") >= 2
    assert expr.to_c(expr.parse("-a")).startswith("(-")
    with pytest.raises(expr.ExprError):
        expr.to_c(expr.parse_condition("defined(X)"))


def test_checksum_sizes_and_encodings() -> None:
    project = _project(ROOT / "examples" / "02_multi_interface_avionics")
    assert {name: checksum_size(item) for name, item in project.checksums.items()} == {
        "crc16ccitt": 2,
        "vendorSum": 2,
    }
    message = next(iter(project.messages.values()))
    interface = next(iter(project.interfaces.values()))
    assert encoding_initializer(project, message, interface).startswith("{WF_")


MAIN = r"""
#include <stdio.h>
#include "basic_telemetry/model.h"

int main(void)
{
    demo_telemetry_SensorReading value;
    uint8_t buffer[64];
    size_t size = 0U;
    demo_telemetry_SensorReading_init(&value);
    value.sensorId = 7U;
    value.state = demo_telemetry_SensorState_fault;
    value.temperature = -12.5F;
    value.accel.x = 1.0F;
    value.accel.y = 2.0F;
    value.accel.z = -3.0F;
    value.samples_count = 3U;
    value.samples[0] = 1U;
    value.samples[1] = 0x1234U;
    value.samples[2] = 65535U;
    if (demo_telemetry_SensorReading_validate(&value) != WF_OK) {
        return 2;
    }
    if (demo_telemetry_SensorReading_serialize(&value, buffer, sizeof(buffer), &size) != WF_OK) {
        return 3;
    }
    for (size_t index = 0U; index < size; ++index) {
        printf("%02x", buffer[index]);
    }
    printf("\n");
    demo_telemetry_SensorReading again;
    demo_telemetry_SensorReading_init(&again);
    if (demo_telemetry_SensorReading_deserialize(buffer, size, &again) != WF_OK) {
        return 4;
    }
    if (again.samples_count != 3U || again.samples[1] != 0x1234U ||
        again.state != value.state) {
        return 5;
    }
    buffer[1] = 9U;
    wf_status status = demo_telemetry_SensorReading_deserialize(buffer, size, &again);
    return status == WF_OUT_OF_RANGE ? 0 : 6;
}
"""


@pytest.mark.skipif(COMPILER is None, reason="no C compiler")
def test_the_generated_c_compiles_and_agrees_with_the_reference_codec(tmp_path: Path) -> None:
    example = ROOT / "examples" / "01_basic_telemetry"
    project = _project(example)
    assert generate.generate(example, tmp_path, language="c") == []
    (tmp_path / "main.c").write_text(MAIN, encoding="utf-8")
    program = tmp_path / "program"
    compiler = COMPILER or "cc"
    build = subprocess.run(
        [
            compiler,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Wconversion",
            "-Werror",
            f"-I{RUNTIME}",
            f"-I{tmp_path}",
            str(tmp_path / "main.c"),
            str(tmp_path / "basic_telemetry" / "model.c"),
            "-o",
            str(program),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(program)], capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stdout
    value = {
        "sensorId": 7,
        "state": "fault",
        "temperature": -12.5,
        "accel": {"x": 1.0, "y": 2.0, "z": -3.0},
        "samples": [1, 0x1234, 65535],
    }
    expected = ReferenceCodec(project, None).encode_message("SensorReading", value)
    assert run.stdout.strip() == expected.hex()


LINK_MAIN = r"""
#include <stdio.h>
#include <string.h>
#include "basic_telemetry/interfaces/sensor_to_us.h"

typedef struct {
    uint8_t bytes[256];
    size_t size;
    size_t at;
} loopback;

static size_t loop_send(void *context, const uint8_t *bytes, size_t length)
{
    loopback *loop = (loopback *)context;
    memcpy(&loop->bytes[loop->size], bytes, length);
    loop->size += length;
    return length;
}

static size_t loop_receive(void *context, uint8_t *buffer, size_t capacity)
{
    loopback *loop = (loopback *)context;
    size_t count = loop->size - loop->at;
    count = count < capacity ? count : capacity;
    memcpy(buffer, &loop->bytes[loop->at], count);
    loop->at += count;
    return count;
}

typedef struct {
    int received;
    uint8_t sensor;
} seen;

static void on_reading(void *context, const demo_telemetry_SensorReading *message)
{
    seen *state = (seen *)context;
    state->sensor = message->sensorId;
    ++state->received;
}

int main(void)
{
    static demo_telemetry_sensor_to_us_link link;
    static loopback loop;
    seen state = {0, 0U};
    const wf_transport transport = {&loop, loop_send, loop_receive};
    demo_telemetry_sensor_to_us_handler handler;
    demo_telemetry_SensorReading value;
    demo_telemetry_sensor_to_us_header header;
    memset(&handler, 0, sizeof(handler));
    handler.context = &state;
    handler.on_SensorReading_received = on_reading;
    demo_telemetry_sensor_to_us_link_init(&link, &transport, &handler);
    demo_telemetry_SensorReading_init(&value);
    value.sensorId = 9U;
    value.samples_count = 2U;
    value.samples[0] = 0x0102U;
    value.samples[1] = 0x0304U;
    demo_telemetry_sensor_to_us_header_init(&header);
    if (demo_telemetry_sensor_to_us_link_send_SensorReading(&link, &value, &header) != WF_OK) {
        return 2;
    }
    for (size_t index = 0U; index < loop.size; ++index) {
        printf("%02x", loop.bytes[index]);
    }
    printf("\n");
    const wf_poll_result polled = demo_telemetry_sensor_to_us_link_poll(&link);
    return (polled.frames == 1U && polled.errors == 0U && state.received == 1 &&
            state.sensor == 9U) ? 0 : 3;
}
"""


@pytest.mark.skipif(COMPILER is None, reason="no C compiler")
def test_a_link_in_c_sends_and_receives_the_frame_of_the_reference_codec(tmp_path: Path) -> None:
    example = ROOT / "examples" / "01_basic_telemetry"
    project = _project(example)
    assert generate.generate(example, tmp_path, language="c") == []
    (tmp_path / "main.c").write_text(LINK_MAIN, encoding="utf-8")
    program = tmp_path / "program"
    compiler = COMPILER or "cc"
    build = subprocess.run(
        [
            compiler,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Werror",
            f"-I{RUNTIME}",
            f"-I{tmp_path}",
            str(tmp_path / "main.c"),
            str(tmp_path / "basic_telemetry" / "model.c"),
            str(tmp_path / "basic_telemetry" / "interfaces" / "sensor_to_us.c"),
            "-o",
            str(program),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(program)], capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stdout
    value = {
        "sensorId": 9,
        "state": "ready",
        "temperature": 20.0,
        "accel": {},
        "samples": [258, 772],
    }
    frame = ReferenceCodec(project, None).encode_frame("sensor_to_us", "SensorReading", value)
    assert run.stdout.strip() == frame.hex()
