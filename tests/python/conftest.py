"""Fixtures shared by the Python tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

GTEST_OK = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="1" failures="0" name="AllTests">
  <testsuite name="HlrVector" tests="1" failures="0" skipped="0">
    <testcase name="nominal" status="run" result="completed" classname="HlrVector">
      <properties>
        <property name="requirements" value="HLR-1,LLR-M.LAYOUT"/>
      </properties>
    </testcase>
  </testsuite>
</testsuites>
"""
GTEST_BAD = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="2" failures="1" name="AllTests">
  <testsuite name="M" tests="2" failures="1" skipped="1">
    <testcase name="Broken" status="run" result="completed" classname="M">
      <failure message="Expected equality&#10;of these values" type=""/>
    </testcase>
    <testcase name="Skipped" status="run" result="skipped" classname="M">
      <skipped message="not today"/>
    </testcase>
  </testsuite>
</testsuites>
"""
CTEST = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="(empty)" tests="3" failures="1">
  <testcase name="HlrVector.nominal" classname="HlrVector.nominal" status="run"/>
  <testcase name="demo_selftest" classname="demo_selftest" status="run"/>
  <testcase name="demo_selftest_b" classname="demo_selftest_b" status="run">
    <failure message="FAIL x"/>
  </testcase>
</testsuite>
"""


def _lines(covered: list[int], missed: list[tuple[int, str]]) -> dict[str, object]:
    lines = [{"line_number": n, "count": 3, "function_name": "f", "branches": []} for n in covered]
    lines += [
        {
            "line_number": n,
            "count": 0,
            "function_name": name,
            "branches": [{"count": 0}, {"count": 1}],
        }
        for n, name in missed
    ]
    lines.append({"line_number": 1, "count": 0, "gcovr/noncode": True})
    return {"lines": lines}


def write_run(root: Path, *, runtime: bool = True, bad: bool = False, stack: bool = True) -> Path:
    """Write a small result tree below `root` and return the build directory."""
    out = root / "results"
    (out / "junit" / "demo_tests").mkdir(parents=True)
    (out / "junit" / "demo_tests" / "HlrVector.nominal.xml").write_text(GTEST_OK, encoding="utf-8")
    if bad:
        (out / "junit" / "demo_tests" / "M.Broken.xml").write_text(GTEST_BAD, encoding="utf-8")
    (out / "ctest.xml").write_text(CTEST, encoding="utf-8")
    generated = {"files": [{"file": "demo/model.hpp", **_lines([10, 11], [])}]}
    (out / "coverage.json").write_text(json.dumps(generated), encoding="utf-8")
    if runtime:
        gaps = [(118, "wireforge::Ring<1>::push(int)"), (119, "wireforge::Ring<1>::push(int)")]
        files = [
            {"file": "wireforge/ring.hpp", **_lines([5], gaps)},
            {"file": "wireforge/span.hpp", **_lines([5], [(30, "wireforge::Span::end()")])},
        ]
        (out / "coverage_runtime.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    toolchain = {"cxx_compiler_id": "GNU", "cxx_compiler_version": "12.3.0", "build_type": "Debug"}
    (out / "toolchain.json").write_text(json.dumps(toolchain), encoding="utf-8")
    if stack:
        objects = root / "CMakeFiles" / "demo_tests.dir"
        objects.mkdir(parents=True)
        (objects / "a.cpp.su").write_text(
            "/home/x/include/wireforge/link.hpp:70:5:void wireforge::Link::poll()\t48\tstatic\n"
            "/home/x/gen/demo/model.hpp:20:1:void demo::encode(int)\t64\tdynamic\n"
            "/usr/include/c++/12/new:174:33:void* operator new(unsigned long)\t16\tstatic\n",
            encoding="utf-8",
        )
        (objects / "b.cpp.su").write_text(
            "/other/include/wireforge/link.hpp:70:5:void wireforge::Link::poll()\t96\tstatic\n",
            encoding="utf-8",
        )
    return root


@pytest.fixture
def make_results() -> Callable[..., Path]:
    """Return the function that writes a small result tree (`root`, `runtime`, `bad`, `stack`)."""
    return write_run
