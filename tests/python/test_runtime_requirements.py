from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from wireforge import bundle, cli, lifecycle, llr, runtime_reqs, service
from wireforge import lifecycle_runtime as runtime_docs
from wireforge.loader import load_project

if TYPE_CHECKING:
    import pytest

    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = ROOT / "runtime" / "include"
TESTS = ROOT / "tests"


def test_the_requirements_file_is_valid_and_matches_the_headers() -> None:
    requirements = runtime_reqs.load()
    assert len(requirements) > 50
    assert runtime_reqs.check(requirements, INCLUDE) == []
    assert len({item.id for item in requirements}) == len(requirements)


def test_every_runtime_test_records_a_known_requirement() -> None:
    known = {item.id: item for item in runtime_reqs.load()}
    found = runtime_reqs.tests(TESTS)
    assert len(found) >= 100
    for test in found:
        if not test.ids:
            assert test.id.split(".")[0] in runtime_docs.INFRASTRUCTURE, test.id
        for ident in test.ids:
            assert ident in known, (test.id, ident)
            assert not known[ident].retired, (test.id, ident)


def test_every_requirement_verified_by_test_has_a_test() -> None:
    tested = {ident for test in runtime_reqs.tests(TESTS) for ident in test.ids}
    for item in runtime_reqs.load():
        if item.verification == "test" and not item.retired:
            assert item.id in tested, item.id


def test_every_generated_requirement_kind_has_a_runtime_requirement() -> None:
    realized = runtime_reqs.realized_by_kind()
    for kind in llr.KINDS:
        if kind not in runtime_reqs.GENERATED_ONLY:
            assert realized.get(kind), kind


def test_rules_rr1_to_rr3() -> None:
    good = runtime_reqs.RuntimeRequirement("RTR-span-001", "x", "span.hpp", ("Span",), "test")
    assert runtime_reqs.check([good], INCLUDE) == []
    wrong_header = runtime_reqs.RuntimeRequirement(
        "RTR-lock-001", "x", "span.hpp", ("Span",), "test"
    )
    missing = runtime_reqs.RuntimeRequirement("RTR-nothing-001", "x", "nothing.hpp", ("A",), "test")
    wrong_symbol = runtime_reqs.RuntimeRequirement(
        "RTR-span-002", "x", "span.hpp", ("Nope",), "test"
    )
    messages = [str(issue) for issue in runtime_reqs.check([wrong_header], INCLUDE)]
    assert any("[RR-1]" in text for text in messages)
    assert any("[RR-2]" in str(i) for i in runtime_reqs.check([missing], INCLUDE))
    assert any("[RR-3]" in str(i) for i in runtime_reqs.check([wrong_symbol], INCLUDE))


def test_runtime_version_and_symbol_lines() -> None:
    assert runtime_reqs.version(INCLUDE) == "0.1.0"
    item = next(i for i in runtime_reqs.load() if i.id == "RTR-version-001")
    assert runtime_reqs.symbol_line(item, INCLUDE).startswith("wireforge/version.hpp:")


def test_the_dossier_without_results() -> None:
    ctx = runtime_docs.context()
    files = runtime_docs.render(ctx)
    assert set(files) == {
        "index.md",
        "requirements.md",
        "requirements.csv",
        "design.md",
        "test_spec.md",
        "test_spec.csv",
        "test_report.md",
        "traceability.md",
        "traceability.csv",
        "environment.md",
        "do178c_dal_c.md",
    }
    assert runtime_docs.strict_findings(ctx) == []
    assert "Test results: not provided." in files["test_report.md"]
    assert "RTR-frame_decoder-003" in files["traceability.md"]
    assert "Header hashes" in files["environment.md"]
    assert files == runtime_docs.render(runtime_docs.context()), "deterministic"
    for name, text in files.items():
        assert str(ROOT) not in text, name


def _runtime_results(root: Path, *, failing: bool = False) -> Path:
    junit = root / "results" / "junit" / "io_test"
    junit.mkdir(parents=True)
    body = '<failure message="boom"/>' if failing else ""
    (junit / "ByteIo.Big.xml").write_text(
        f'<testsuites><testsuite name="ByteIo"><testcase name="BigAndLittleEndianForEveryWidth" '
        f'classname="ByteIo">{body}</testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    lines = [{"line_number": 5, "count": 0, "function_name": "wireforge::f()", "branches": []}]
    files = [{"file": "runtime/include/wireforge/byte_io.hpp", "lines": lines}]
    (root / "results" / "coverage.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    return root


def test_the_dossier_with_results(tmp_path: Path) -> None:
    ctx = runtime_docs.context(build_dir=_runtime_results(tmp_path))
    files = runtime_docs.render(ctx)
    assert "| passed | 1 |" in files["test_report.md"]
    assert "wireforge/byte_io.hpp" in files["test_report.md"]
    assert [item.rule for item in runtime_docs.strict_findings(ctx)] == ["DS-7"]
    failing = runtime_docs.context(build_dir=_runtime_results(tmp_path / "bad", failing=True))
    assert "DS-5" in {item.rule for item in runtime_docs.strict_findings(failing)}


def test_cli_docs_runtime(tmp_path: Path) -> None:
    output = tmp_path / "dossier"
    assert cli.main(["docs", "--runtime", "-o", str(output), "--strict"]) == cli.EXIT_OK
    assert (output / "requirements.md").exists()
    both = cli.main(
        ["docs", str(ROOT / "examples" / "01_basic_telemetry"), "--runtime", "-o", str(output)]
    )
    assert both == cli.EXIT_USAGE
    assert cli.main(["docs", "-o", str(output)]) == cli.EXIT_USAGE
    missing = cli.main(["docs", "--runtime", "-o", str(output), "--results", str(tmp_path / "no")])
    assert missing == cli.EXIT_ERROR


def _project(name: str) -> Project:
    result = load_project(ROOT / "examples" / name)
    assert result.project is not None
    return result.project


def test_a_runtime_verified_requirement_needs_a_runtime_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = ROOT / "examples" / "01_basic_telemetry"
    project = _project("01_basic_telemetry")
    content_hash = bundle.build_bundle(location)["bundle"]["content_hash"]
    with_runtime = lifecycle.context(project, content_hash)
    unknown = next(r for r in with_runtime.llr_rows if r.kind == "DISPATCH_UNKNOWN")
    assert "RTR-dispatcher-003" in unknown.realized_by
    assert lifecycle.strict_findings(with_runtime) == []
    monkeypatch.setattr(runtime_reqs, "realized_by_kind", dict)
    without = lifecycle.context(project, content_hash)
    findings = lifecycle.strict_findings(without)
    assert [item.rule for item in findings] == ["DS-3"]
    assert "DISPATCH_UNKNOWN" in findings[0].message
    assert service.lifecycle_docs(location, ROOT / "build" / "x", strict=False).ok
