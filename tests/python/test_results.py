from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from wireforge import results

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    MakeResults = Callable[..., Path]


def test_load_reads_every_file(make_results: MakeResults, tmp_path: Path) -> None:
    loaded = results.load(make_results(tmp_path, bad=True))
    outcomes = {(item.id, item.executable): item.outcome for item in loaded.tests}
    assert outcomes == {
        ("HlrVector.nominal", "demo_tests"): "passed",
        ("M.Broken", "demo_tests"): "failed",
        ("M.Skipped", "demo_tests"): "skipped",
    }
    nominal = next(item for item in loaded.tests if item.id == "HlrVector.nominal")
    assert nominal.requirements == ("HLR-1", "LLR-M.LAYOUT")
    broken = next(item for item in loaded.tests if item.id == "M.Broken")
    assert broken.message == "Expected equality"
    assert [(item.id, item.outcome) for item in loaded.selftests] == [
        ("demo_selftest", "passed"),
        ("demo_selftest_b", "failed"),
    ]
    assert loaded.toolchain["cxx_compiler_id"] == "GNU"


def test_coverage_counts_statements_and_branches(make_results: MakeResults, tmp_path: Path) -> None:
    loaded = results.load(make_results(tmp_path))
    (generated,) = loaded.generated
    assert (generated.lines_total, generated.lines_covered) == (2, 2)
    assert loaded.runtime is not None
    ring = loaded.runtime[0]
    assert (ring.lines_total, ring.lines_covered) == (3, 1)
    assert ring.uncovered == (
        (118, "wireforge::Ring<1>::push(int)"),
        (119, "wireforge::Ring<1>::push(int)"),
    )
    assert (ring.branches_total, ring.branches_covered) == (4, 2)


def test_runtime_coverage_is_optional(make_results: MakeResults, tmp_path: Path) -> None:
    assert results.load(make_results(tmp_path, runtime=False)).runtime is None


def test_stack_keeps_the_largest_value_of_project_and_runtime_headers(
    make_results: MakeResults, tmp_path: Path
) -> None:
    loaded = results.load(make_results(tmp_path))
    found = {(item.file, item.function): item.bytes for item in loaded.stack}
    assert found == {
        ("wireforge/link.hpp", "void wireforge::Link::poll()"): 96,
        ("demo/model.hpp", "void demo::encode(int)"): 64,
    }


@pytest.mark.parametrize(
    "missing",
    ["junit", "ctest.xml", "coverage.json", "toolchain.json"],
)
def test_a_missing_file_is_an_error(
    make_results: MakeResults, tmp_path: Path, missing: str
) -> None:
    build = make_results(tmp_path)
    target = build / "results" / missing
    if target.is_dir():
        for path in target.rglob("*.xml"):
            path.unlink()
    else:
        target.unlink()
    with pytest.raises(results.ResultsError):
        results.load(build)


def test_damaged_files_are_errors(make_results: MakeResults, tmp_path: Path) -> None:
    build = make_results(tmp_path)
    (build / "results" / "coverage.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(results.ResultsError, match="not a JSON object"):
        results.load(build)
    (build / "results" / "coverage.json").write_text("{", encoding="utf-8")
    with pytest.raises(results.ResultsError):
        results.load(build)
    make_results(tmp_path / "other")
    (tmp_path / "other" / "results" / "ctest.xml").write_text("<testsuite>", encoding="utf-8")
    with pytest.raises(results.ResultsError):
        results.load(tmp_path / "other")


def test_a_directory_without_results_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(results.ResultsError, match="no results directory"):
        results.load(tmp_path)


DEVIATIONS = """\
schema_version: 1
deviations:
  - kind: coverage
    file: wireforge/ring.hpp
    function: "wireforge::Ring<*>::push(*)"
    lines: [118, 119]
    category: defensive
    rationale: The ring cannot be full with this link.
    reviewer: J. Doe
  - kind: coverage
    file: wireforge/span.hpp
    function: "wireforge::Span::end()"
    lines: [31]
    category: deactivated
    rationale: Moved line.
    reviewer: J. Doe
  - kind: coverage
    file: wireforge/never.hpp
    function: "*"
    category: unreachable-by-design
    rationale: Not used any more.
    reviewer: J. Doe
  - kind: rule
    tool: Some Checker
    rule: R1
    file: "*"
    category: justified
    rationale: Later.
    reviewer: J. Doe
"""


def test_deviations_justify_by_function_wildcard_and_lines(
    make_results: MakeResults, tmp_path: Path
) -> None:
    (tmp_path / "deviations.yaml").write_text(DEVIATIONS, encoding="utf-8")
    deviations = results.load_deviations(tmp_path)
    assert len(deviations) == 3, "rule deviations are not used yet"
    loaded = results.load(make_results(tmp_path / "run"))
    assert loaded.runtime is not None
    unjustified, unused = results.match_coverage(loaded.runtime, deviations)
    assert [(gap.file, gap.lines) for gap in unjustified] == [("wireforge/span.hpp", (30,))]
    assert [item.file for item in unused] == ["wireforge/span.hpp", "wireforge/never.hpp"]


def test_a_partly_justified_gap_keeps_the_rest(make_results: MakeResults, tmp_path: Path) -> None:
    (tmp_path / "deviations.yaml").write_text(
        DEVIATIONS.replace("lines: [118, 119]", "lines: [118]"), encoding="utf-8"
    )
    loaded = results.load(make_results(tmp_path / "run"))
    assert loaded.runtime is not None
    unjustified, _ = results.match_coverage(loaded.runtime, results.load_deviations(tmp_path))
    assert (("wireforge/ring.hpp", (119,))) in [(gap.file, gap.lines) for gap in unjustified]


def test_no_deviations_file_and_an_invalid_one(tmp_path: Path) -> None:
    assert results.load_deviations(tmp_path) == []
    (tmp_path / "deviations.yaml").write_text(
        "schema_version: 1\ndeviations:\n  - { kind: coverage, file: a }\n", encoding="utf-8"
    )
    with pytest.raises(results.ResultsError):
        results.load_deviations(tmp_path)


def test_a_coverage_report_without_files_is_an_error(
    make_results: MakeResults, tmp_path: Path
) -> None:
    build = make_results(tmp_path)
    (build / "results" / "coverage_runtime.json").write_text('{"files": []}', encoding="utf-8")
    with pytest.raises(results.ResultsError, match="matched nothing"):
        results.load(build)
