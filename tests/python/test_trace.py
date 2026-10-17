from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from wireforge import cli, service

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"


def _rows(example: str) -> list[dict[str, object]]:
    result = service.trace_report(EXAMPLES / example)
    assert result.ok
    rows = result.data["rows"]
    assert isinstance(rows, list)
    return rows


def test_every_element_is_traced() -> None:
    for example in ("01_basic_telemetry", "02_multi_interface_avionics", "03_edge_cases"):
        rows = _rows(example)
        for row in rows:
            assert row["source"], row
            assert row["generated"], row
        tested = [row for row in rows if row["kind"] in {"message", "interface"}]
        assert tested
        assert all(row["tests"] for row in tested), "every message and interface has tests"


def test_rows_point_at_source_and_code() -> None:
    rows = {row["element"]: row for row in _rows("01_basic_telemetry")}
    reading = rows["/messages/SensorReading"]
    assert reading["source"] == "project.yaml:41"
    assert reading["symbol"] == "demo::telemetry::SensorReading"
    temperature = rows["/messages/SensorReading/fields/2"]
    assert temperature["symbol"] == "demo::telemetry::SensorReading::temperature"
    assert "SensorReading.temperature_min" in str(temperature["tests"])
    count = rows["/messages/SensorReading/fields/4"]
    assert "(wire only)" in str(count["symbol"])
    assert (
        rows["/interfaces/sensor_to_us"]["generated"]
        == "basic_telemetry/interfaces/sensor_to_us.hpp:29"
    )


def _llrs(example: str) -> list[dict[str, object]]:
    result = service.trace_report(EXAMPLES / example)
    assert result.ok
    llrs = result.data["llrs"]
    assert isinstance(llrs, list)
    return llrs


# NavMode is declared but no message uses it, so no generated test can exercise its members.
UNUSED = {"02_multi_interface_avionics": {"LLR-NavMode.VALUES"}}


def test_llrs_have_tests() -> None:
    for example in ("01_basic_telemetry", "02_multi_interface_avionics"):
        untested = {row["id"] for row in _llrs(example) if not row["tests"]}
        runtime = {row["id"] for row in _llrs(example) if row["runtime_verified"]}
        assert untested == runtime | UNUSED.get(example, set()), example


def test_requirements_flow_from_model_to_tests() -> None:
    rows = {row["element"]: row for row in _rows("01_basic_telemetry")}
    temperature = rows["/messages/SensorReading/fields/2"]
    assert temperature["requirements"] == ("HLR-SNS-004",)
    assert "LLR-SensorReading.temperature.RANGE" in str(temperature["llrs"])
    assert rows["/messages/SensorReading"]["requirements"] == ("HLR-SNS-001",)
    llrs = {row["id"]: row for row in _llrs("01_basic_telemetry")}
    range_llr = llrs["LLR-SensorReading.temperature.RANGE"]
    assert range_llr["parents"] == ("HLR-SNS-001", "HLR-SNS-004")
    assert not range_llr["derived"]
    assert "SensorReading.temperature_below_min" in str(range_llr["tests"])
    assert llrs["LLR-Vec3.LAYOUT"]["derived"]
    assert llrs["LLR-SensorReading.SIZE"]["tests"] == ("SensorReading.WireSize",)


def test_markdown_lists_gaps() -> None:
    text = service.trace_report(EXAMPLES / "02_multi_interface_avionics").text
    assert "LLRs without tests: LLR-NavMode.VALUES." in text
    assert "LLRs verified by the runtime tests: LLR-" in text
    assert "Requirements without model elements: none." in text
    plain = service.trace_report(EXAMPLES / "03_edge_cases").text
    assert "Requirements without model elements" not in plain


def test_exports_and_cli(tmp_path: Path) -> None:
    text = service.trace_report(EXAMPLES / "03_edge_cases").text
    assert text.startswith("# Trace report edge_cases 0.0.1")
    assert "Messages and interfaces without tests: none." in text
    csv = service.trace_report(EXAMPLES / "03_edge_cases", "csv").text
    assert csv.startswith("element,kind,source,symbol,generated,requirements,llrs,tests\n")
    output = tmp_path / "trace.md"
    assert cli.main(["trace", str(EXAMPLES / "01_basic_telemetry"), "-o", str(output)]) == 0
    assert output.read_text(encoding="utf-8").startswith("# Trace report")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "project.yaml").write_text(
        "schema_version: 1\nproject: p\nmessages: {}\nx: 1\n", encoding="utf-8"
    )
    assert not service.trace_report(broken).ok


def _no_heap() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_no_heap", ROOT / "quality" / "check_no_heap.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


def test_no_heap_report() -> None:
    module = _no_heap()
    good = module.Finding("runtime span.hpp", "-O2", (), ())
    bad = module.Finding(
        "x", "-O0", ("operator new(unsigned long)",), ("operator new(unsigned long)",)
    )
    text = module.report([good, bad])
    assert "| runtime span.hpp | -O2 | none | pass |" in text
    assert "FAIL: operator new(unsigned long)" in text
    assert text.endswith("2 objects checked, 1 failed.\n")
