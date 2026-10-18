from __future__ import annotations

import dataclasses
import filecmp
import re
import shutil
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wireforge import bundle, golden, lifecycle, llr, service, test_project, vcs
from wireforge import results as test_results
from wireforge.loader import load_project

if TYPE_CHECKING:
    from collections.abc import Callable

    from wireforge.codec import ChecksumPlugin
    from wireforge.model import Project

    MakeResults = Callable[..., Path]
ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"
PLUGINS = golden.load_plugins(AVIONICS / "plugins" / "vendor_sum.py")


def _project(name: str) -> Project:
    project = load_project(EXAMPLES / name).project
    assert project is not None
    return project


def _context(
    location: Path, plugins: dict[str, ChecksumPlugin] | None = None
) -> lifecycle.DocsContext:
    project = load_project(location).project
    assert project is not None
    content_hash = bundle.build_bundle(location)["bundle"]["content_hash"]
    return lifecycle.context(project, content_hash, plugins)


def _headings(text: str) -> set[str]:
    found = {lifecycle.slug(match[2]) for match in re.finditer(r"^(#+) (.+)$", text, re.MULTILINE)}
    return found | set(re.findall(r'<a id="([^"]+)"></a>', text))


def test_documents_are_deterministic(tmp_path: Path) -> None:
    ctx = _context(AVIONICS, PLUGINS)
    assert lifecycle.render(ctx) == lifecycle.render(_context(AVIONICS, PLUGINS))
    for name in ("one", "two"):
        assert service.lifecycle_docs(AVIONICS, tmp_path / name).ok
    comparison = filecmp.dircmp(tmp_path / "one", tmp_path / "two")
    assert not comparison.diff_files
    assert not comparison.left_only
    assert not comparison.right_only


def test_documents_hold_no_absolute_path_or_date() -> None:
    for example in ("01_basic_telemetry", "02_multi_interface_avionics"):
        files = lifecycle.render(_context(EXAMPLES / example, PLUGINS))
        for name, text in files.items():
            assert str(ROOT) not in text, name
            assert not re.search(r"\d{4}-\d{2}-\d{2}", text), name
            assert "/home/" not in text, name


def test_basic_example_documents() -> None:
    files = lifecycle.render(_context(EXAMPLES / "01_basic_telemetry"))
    assert set(files) == {
        "index.md",
        "requirements.md",
        "requirements.csv",
        "design.md",
        "interfaces_coupling.md",
        "test_spec.md",
        "test_spec.csv",
        "test_report.md",
        "resources.md",
        "traceability.md",
        "traceability.csv",
        "environment.md",
        "code_standard.md",
        "review_checklists.md",
        "do178c_dal_c.md",
    }
    for rid in ("HLR-SNS-001", "HLR-SNS-004", "HLR-SNS-005", "HLR-SNS-006"):
        assert rid in files["requirements.md"]
    assert files["traceability.csv"].startswith("llr,kind,parents,code,tests,derived,result\n")
    tests = files["test_spec.csv"].splitlines()
    assert len(tests) - 1 == len(_context(EXAMPLES / "01_basic_telemetry").tests) == 41
    assert "Test results: not provided." in files["test_report.md"]


def test_every_evidence_reference_resolves() -> None:
    files = lifecycle.render(_context(AVIONICS, PLUGINS))
    headings = {name: _headings(text) for name, text in files.items() if name.endswith(".md")}
    for objective in lifecycle.DO178C_DAL_C:
        if objective.applicant:
            continue
        for reference in objective.evidence:
            name, _, anchor = reference.partition("#")
            assert name in headings, reference
            assert not anchor or anchor in headings[name], reference


def test_no_certification_claim() -> None:
    for example in ("01_basic_telemetry", "02_multi_interface_avionics"):
        files = lifecycle.render(_context(EXAMPLES / example, PLUGINS))
        for name, text in files.items():
            assert not re.search(r"\b(certified|compliant)\b", text, re.IGNORECASE), name


def test_tests_cover_every_profile_and_executable() -> None:
    ctx = _context(AVIONICS, PLUGINS)
    assert {entry.profile for entry in ctx.tests} == {"platformA", "platformB"}
    assert {entry.executable for entry in ctx.tests} == set(
        test_project.executables(ctx.project, "tests")
    )
    assert any(entry.kind == "robustness" for entry in ctx.tests)
    assert all(entry.verifies and entry.procedure and entry.expected for entry in ctx.tests)


def test_strict_findings_by_example() -> None:
    assert lifecycle.strict_findings(_context(EXAMPLES / "01_basic_telemetry")) == []
    assert lifecycle.strict_findings(_context(AVIONICS, PLUGINS)) == []
    findings = lifecycle.strict_findings(_context(EXAMPLES / "03_edge_cases"))
    assert [item.rule for item in findings] == ["DS-1"]


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "basic"
    shutil.copytree(EXAMPLES / "01_basic_telemetry", target)
    return target


INLINE = """\
schema_version: 1
project: inline
requirements:
  HLR-1: { text: Carry the A message., verification: review }
messages:
  A:
    id: 1
    reqs: [HLR-1]
    fields: [{ name: a, type: u8 }]
  B:
    id: 2
    fields: [{ name: b, type: u8 }]
interfaces:
  link:
    version: "1.0.0"
    direction: both
    header:
      fields:
        - { name: id, type: u8, role: message_id }
        - { name: length, type: u8, role: payload_length }
    framing: { kind: length_prefixed, max_frame_size: 16 }
    messages: [A, B]
"""


def test_strict_finds_a_message_without_requirement(tmp_path: Path) -> None:
    (tmp_path / "project.yaml").write_text(INLINE, encoding="utf-8")
    findings = lifecycle.strict_findings(_context(tmp_path))
    assert [(item.rule, item.pointer) for item in findings] == [("DS-2", "/messages/B")]


def test_strict_finds_an_unlinked_requirement(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    path = target / "project.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "requirements:\n", "requirements:\n  HLR-UNUSED:\n    text: Nobody implements this.\n"
        ),
        encoding="utf-8",
    )
    findings = lifecycle.strict_findings(_context(target))
    assert [(item.rule, item.pointer) for item in findings] == [
        ("DS-4", "/requirements/HLR-UNUSED")
    ]
    assert not service.lifecycle_docs(target, tmp_path / "out", strict=True).ok
    assert service.lifecycle_docs(target, tmp_path / "out").ok


def test_review_only_requirement_is_not_an_unlinked_finding(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    path = target / "project.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "requirements:\n",
            "requirements:\n  HLR-REVIEW:\n    text: Reviewed by hand.\n    verification: review\n",
        ),
        encoding="utf-8",
    )
    assert lifecycle.strict_findings(_context(target)) == []


def test_docs_only_touch_their_own_files(tmp_path: Path) -> None:
    output = tmp_path / "docs"
    output.mkdir()
    (output / "notes.txt").write_text("keep", encoding="utf-8")
    (output / "index.md").write_text("old", encoding="utf-8")
    result = service.lifecycle_docs(EXAMPLES / "01_basic_telemetry", output, strict=True)
    assert result.ok, result.text
    assert (output / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert (output / "index.md").read_text(encoding="utf-8").startswith("# Lifecycle documents")
    assert result.data["summary"]["high-level requirements"] == 4


def test_archive_is_a_zip_with_the_index() -> None:
    result, data = service.lifecycle_archive(EXAMPLES / "01_basic_telemetry")
    assert result.ok
    with zipfile.ZipFile(BytesIO(data)) as archive:
        assert "index.md" in archive.namelist()
        assert archive.read("index.md") == archive.read("index.md")
    again = service.lifecycle_archive(EXAMPLES / "01_basic_telemetry")[1]
    assert again == data, "the zip is reproducible"


def test_invalid_project_writes_nothing(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "project.yaml").write_text("schema_version: 1\nproject: p\nx: 1\n", encoding="utf-8")
    result = service.lifecycle_docs(broken, tmp_path / "out")
    assert not result.ok
    assert not (tmp_path / "out").exists()
    assert service.lifecycle_archive(broken)[1] == b""


@pytest.mark.parametrize("name", ["01_basic_telemetry", "02_multi_interface_avionics"])
def test_review_checklist_auto_items_pass(name: str) -> None:
    text = lifecycle.render(_context(EXAMPLES / name, PLUGINS))["review_checklists.md"]
    assert "[auto: PASS]" in text
    assert "[auto: FAIL]" not in text
    assert "- [ ] reviewer:" in text


def _with_results(
    make_results: MakeResults, tmp_path: Path, **options: bool
) -> lifecycle.DocsContext:
    ctx = _context(EXAMPLES / "01_basic_telemetry")
    build = make_results(tmp_path, **options)
    return dataclasses.replace(ctx, results=test_results.load(build))


def test_without_results_every_result_section_says_so() -> None:
    files = lifecycle.render(_context(EXAMPLES / "01_basic_telemetry"))
    assert "Test results: not provided." in files["test_report.md"]
    assert "Coverage: not provided." in files["test_report.md"]
    assert "Stack usage: not provided." in files["resources.md"]
    assert "Toolchain: not provided." in files["environment.md"]
    assert "Results: not provided — strict covered model checks only." in files["index.md"]
    assert files["traceability.csv"].splitlines()[0].endswith(",result")


def test_documents_with_results(make_results: MakeResults, tmp_path: Path) -> None:
    ctx = _with_results(make_results, tmp_path, bad=True)
    files = lifecycle.render(ctx)
    report = files["test_report.md"]
    assert "| demo_tests | 1 | 1 | 1 |" in report
    assert "M.Broken (demo_tests): failed Expected equality" in report
    assert "Runtime headers" in report
    assert "wireforge/ring.hpp" in report
    assert "| GNU |" in files["environment.md"] or "cxx_compiler_id" in files["environment.md"]
    assert "wireforge/link.hpp:70" in files["resources.md"]
    assert "indicative, measure on the target" in files["resources.md"]
    assert "Results: 5 test results" in files["index.md"]


def test_strict_rules_for_results(make_results: MakeResults, tmp_path: Path) -> None:
    ctx = _with_results(make_results, tmp_path, bad=True)
    rules = [item.rule for item in lifecycle.strict_findings(ctx)]
    assert rules.count("DS-5") == 3, "a failure, a skip and a failed self-test"
    assert "DS-7" in rules
    assert "DS-6" not in rules, "generated code is fully covered and the runtime was measured"
    clean = lifecycle.strict_findings(_with_results(make_results, tmp_path / "clean"))
    assert {item.rule for item in clean} == {"DS-5", "DS-7"}


def test_runtime_not_measured_is_a_strict_error(make_results: MakeResults, tmp_path: Path) -> None:
    findings = lifecycle.strict_findings(_with_results(make_results, tmp_path, runtime=False))
    messages = [item.message for item in findings if item.rule == "DS-6"]
    assert messages == ["runtime coverage was not measured in this build"]
    report = lifecycle.render(_with_results(make_results, tmp_path / "again", runtime=False))[
        "test_report.md"
    ]
    assert "Runtime coverage: not measured in this build." in report


def test_a_generated_gap_is_ds6_and_deviations_clear_ds7(
    make_results: MakeResults, tmp_path: Path
) -> None:
    ctx = _with_results(make_results, tmp_path)
    assert ctx.results is not None
    deviations = (
        test_results.Deviation(
            "coverage", "wireforge/*", "*", "defensive", "Not reachable here.", "J. Doe"
        ),
    )
    covered = dataclasses.replace(ctx, deviations=deviations)
    assert "DS-7" not in {item.rule for item in lifecycle.strict_findings(covered)}
    assert "Unused justifications" in lifecycle.render(covered)["test_report.md"]
    short = dataclasses.replace(
        ctx.results,
        generated=(dataclasses.replace(ctx.results.generated[0], lines_covered=1),),
    )
    broken = dataclasses.replace(covered, results=short)
    found = [item.message for item in lifecycle.strict_findings(broken) if item.rule == "DS-6"]
    assert found == ["generated code demo/model.hpp covers 1 of 2 statements"]


def test_traceability_result_column(make_results: MakeResults, tmp_path: Path) -> None:
    ctx = _with_results(make_results, tmp_path)
    assert ctx.results is not None
    ctx = dataclasses.replace(
        ctx,
        results=dataclasses.replace(
            ctx.results,
            tests=(
                test_results.TestResult(
                    "Golden.sensor_to_us_SensorReading_default", "demo_tests", "failed", (), ""
                ),
            ),
        ),
    )
    rows = lifecycle.render(ctx)["traceability.csv"].splitlines()
    layout_row = next(row for row in rows if row.startswith("LLR-Vec3.LAYOUT"))
    assert layout_row.endswith(",failed") or ",failed" in layout_row


TAG = re.compile(r"Requirements:\s*([A-Za-z0-9_.:-]+(?:\s*[,;]\s*[A-Za-z0-9_.:-]+)*)")
URL = "https://example.invalid/blob/{commit}/{path}#L{line}"


def _git(directory: Path, *arguments: str) -> str:
    git = shutil.which("git") or "git"
    done = subprocess.run(
        [git, "-c", "user.email=t@t", "-c", "user.name=t", *arguments],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("needs git")
    target = tmp_path / "basic"
    shutil.copytree(EXAMPLES / "01_basic_telemetry", target)
    _git(target, "init", "-q")
    _git(target, "add", "-A")
    _git(target, "commit", "-qm", "base")
    return target


def test_permalinks_point_at_the_commit(tmp_path: Path) -> None:
    target = _repository(tmp_path)
    options = service.DocsOptions(source_url=URL)
    result = service.lifecycle_docs(target, tmp_path / "docs", options=options)
    assert result.ok, result.text
    text = (tmp_path / "docs" / "requirements.md").read_text(encoding="utf-8")
    commit = _git(target, "rev-parse", "HEAD")
    assert f"(https://example.invalid/blob/{commit}/project.yaml#L" in text
    index = (tmp_path / "docs" / "index.md").read_text(encoding="utf-8")
    assert f"| Git commit | {commit} |" in index


def test_a_dirty_working_copy_refuses_permalinks(tmp_path: Path) -> None:
    target = _repository(tmp_path)
    (target / "project.yaml").write_text(
        (target / "project.yaml").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8"
    )
    with pytest.raises(service.ServiceError, match="uncommitted changes"):
        service.lifecycle_docs(
            target, tmp_path / "docs", options=service.DocsOptions(source_url=URL)
        )
    allowed = service.DocsOptions(source_url=URL, allow_dirty=True)
    assert service.lifecycle_docs(target, tmp_path / "docs", options=allowed).ok
    index = (tmp_path / "docs" / "index.md").read_text(encoding="utf-8")
    assert "(uncommitted changes)" in index


def test_permalinks_need_a_repository(tmp_path: Path) -> None:
    target = tmp_path / "basic"
    shutil.copytree(EXAMPLES / "01_basic_telemetry", target)
    if shutil.which("git") is not None and vcs.root(tmp_path) is not None:
        pytest.skip("the temporary directory is inside a repository")
    with pytest.raises(service.ServiceError, match="git repository"):
        service.lifecycle_docs(target, tmp_path / "d", options=service.DocsOptions(source_url=URL))


def test_the_tag_convention_finds_every_id() -> None:
    """The regex of the integration guide finds the requirement ids in generated code."""
    project = _project("01_basic_telemetry")
    notes = llr.requirement_notes(project)
    for owner, lines in notes.items():
        joined = " ".join(line.removeprefix("/// ") for line in lines)
        found = TAG.search(joined)
        assert found is not None, owner
        ids = re.split(r"\s*[,;]\s*", found[1])
        assert all(re.fullmatch(r"[A-Za-z0-9_.:-]+", item) for item in ids)
        assert any(item.startswith("LLR-") for item in ids)


def test_integration_pages_make_no_forbidden_claim() -> None:
    forbidden = re.compile(r"\b(certified|compliant|qualified)\b", re.IGNORECASE)
    negated = r"\b(not|no|never|isn't|aren't)\b[^.\n]{0,60}\b(certified|compliant|qualified)\b"
    allowed = re.compile(negated, re.IGNORECASE)
    pages = [ROOT / "docs" / "DO178C.md", *sorted((ROOT / "docs" / "integration").glob("*.md"))]
    assert len(pages) > 5
    for page in pages:
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1):
            if forbidden.search(line):
                assert allowed.search(line) or "unqualified" in line, (
                    f"{page.name}:{number}: {line}"
                )
