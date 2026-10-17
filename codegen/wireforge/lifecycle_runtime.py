"""Documents of the runtime library: requirements, design, tests and traceability.

The runtime is WireForge's own code, so this dossier is produced by the WireForge project (in its
CI, per release) and not by users. Project documents refer to it by requirement id and runtime
version. The requirements are written and reviewed by people (`runtime/requirements.yaml`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wireforge import docs_markdown as md
from wireforge import resources, runtime_reqs, versioning
from wireforge import results as test_results
from wireforge.lifecycle import StrictFinding

if TYPE_CHECKING:
    from pathlib import Path

# Tests that check the test tooling itself and verify no requirement.
INFRASTRUCTURE = frozenset({"AllocationTrap"})
INCLUDE = re.compile(r"^#include <wireforge/([a-z_]+\.hpp)>", re.MULTILINE)
BRIEF = re.compile(r"@brief (.+)")
LIMITS = (
    "This dossier is evidence for the applicant's own process, not a certification. The runtime "
    "requirements were written from the headers and the tests and need review by a person; "
    "concurrency claims hold for single-threaded use and are the integrator's on the target."
)


@dataclass(frozen=True)
class RuntimeContext:
    """What the runtime documents are made of."""

    include_dir: Path
    tests_dir: Path
    requirements: tuple[runtime_reqs.RuntimeRequirement, ...]
    tests: tuple[runtime_reqs.RuntimeTest, ...]
    results: tuple[test_results.TestResult, ...] | None = None
    coverage: tuple[test_results.FileCoverage, ...] | None = None
    deviations: tuple[test_results.Deviation, ...] = ()


def context(
    include_dir: Path | None = None, tests_dir: Path | None = None, build_dir: Path | None = None
) -> RuntimeContext:
    """Gather the runtime requirements, headers, tests and (with a build directory) results."""
    include_dir = include_dir or resources.runtime_include()
    tests_dir = tests_dir or resources.runtime_include().parents[1] / "tests"
    requirements = runtime_reqs.load()
    problems = runtime_reqs.check(requirements, include_dir)
    if problems:
        raise ValueError("\n".join(str(item) for item in problems))
    results = coverage = None
    if build_dir is not None:
        directory = build_dir / "results"
        try:
            results = test_results.read_gtest(directory / "junit")
            coverage = tuple(
                dataclasses.replace(item, file=item.file.removeprefix("runtime/include/"))
                for item in test_results.read_coverage(directory / "coverage.json")
            )
        except test_results.ResultsError as error:
            raise ValueError(str(error)) from error
    deviations = tuple(test_results.load_deviations(include_dir.parent))
    found = tuple(runtime_reqs.tests(tests_dir))
    return RuntimeContext(
        include_dir, tests_dir, tuple(requirements), found, results, coverage, deviations
    )


def _active(ctx: RuntimeContext) -> list[runtime_reqs.RuntimeRequirement]:
    return [item for item in ctx.requirements if not item.retired]


def _tests_of(ctx: RuntimeContext, ident: str) -> list[str]:
    return sorted({item.id for item in ctx.tests if ident in item.ids})


def _outcome(ctx: RuntimeContext, tests: list[str]) -> str:
    if ctx.results is None:
        return ""
    found = [item.outcome for item in ctx.results if item.id in set(tests)]
    for state in ("failed", "skipped"):
        if state in found:
            return state
    return "passed" if found else "not run"


def strict_findings(ctx: RuntimeContext) -> list[StrictFinding]:
    """Return what a strict review rejects: requirements without tests, tests without requirements.

    With results it also rejects failed tests, statements below 100 % and unjustified gaps.
    """
    findings: list[StrictFinding] = []
    for item in _active(ctx):
        if item.verification == "test" and not _tests_of(ctx, item.id):
            text = f"{item.id} is verified by test but no test records it"
            findings.append(StrictFinding("DS-3", text, f"/requirements/{item.id}"))
    known = {item.id for item in ctx.requirements}
    for test in ctx.tests:
        unknown = [ident for ident in test.ids if ident not in known]
        if unknown or (not test.ids and test.id.split(".")[0] not in INFRASTRUCTURE):
            text = f"test {test.id} records {', '.join(unknown) or 'no requirement'}"
            findings.append(StrictFinding("DS-2", text))
    if ctx.results is not None:
        findings += [
            StrictFinding("DS-5", f"test {item.id} {item.outcome}")
            for item in ctx.results
            if item.outcome != "passed"
        ]
    if ctx.coverage is not None:
        unjustified, _ = test_results.match_coverage(ctx.coverage, list(ctx.deviations))
        for gap in unjustified:
            lines = ", ".join(str(line) for line in gap.lines)
            findings.append(StrictFinding("DS-7", f"{gap.file}: {gap.function} lines {lines}"))
    return findings


def _preamble(ctx: RuntimeContext, title: str) -> list[str]:
    name = f"wireforge-runtime {runtime_reqs.version(ctx.include_dir)}"
    return md.preamble(title, name, None, _headers_hash(ctx), len(_headers(ctx)))


def _headers(ctx: RuntimeContext) -> list[Path]:
    return sorted((ctx.include_dir / "wireforge").glob("*.hpp"))


def _headers_hash(ctx: RuntimeContext) -> str:
    digest = hashlib.sha256()
    for path in _headers(ctx):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _index(ctx: RuntimeContext, findings: list[StrictFinding]) -> str:
    lines = _preamble(ctx, "Runtime dossier")
    lines += ["## Baseline", "", "The model hash above is the hash of the runtime headers.", ""]
    documents = [
        ("requirements.md", "Runtime requirements (also as `requirements.csv`)."),
        ("design.md", "Headers, what each is for and what it includes."),
        ("test_spec.md", "Unit tests and the requirements they verify (also `test_spec.csv`)."),
        ("test_report.md", "Results and statement coverage."),
        ("traceability.md", "Requirement to code to tests (also `traceability.csv`)."),
        ("environment.md", "Header hashes and tool versions."),
        ("do178c_dal_c.md", "Mapping of this dossier to the DO-178C objectives at DAL C."),
    ]
    lines += ["## Documents", "", *md.table(("File", "Content"), documents), ""]
    active = _active(ctx)
    summary = [
        ("requirements", len(active)),
        ("verified by test", sum(item.verification == "test" for item in active)),
        ("verified by analysis or review", sum(item.verification != "test" for item in active)),
        ("unit tests", len(ctx.tests)),
        ("findings of the strict rules", len(findings)),
    ]
    lines += ["## Summary", "", *md.table(("Item", "Count"), summary), ""]
    lines += ["## Strict result", ""]
    lines += [f"- {item.rule}: {item.message}" for item in findings] or ["none"]
    if ctx.results is None:
        lines += ["", "Results: not provided — strict covered the requirements and tests only."]
    lines += ["", "## Limits", "", LIMITS, ""]
    return "\n".join(lines)


def _requirements(ctx: RuntimeContext) -> tuple[str, str]:
    lines = _preamble(ctx, "Runtime requirements")
    rows = [
        (
            f"{md.anchor(item.id)}{item.id}",
            item.header,
            item.text,
            item.verification,
            ", ".join(item.realizes) or "-",
            "retired" if item.retired else "active",
        )
        for item in ctx.requirements
    ]
    headers = ("ID", "Header", "Text", "Verification", "Realizes", "State")
    lines += ["## Requirements", "", *md.table(headers, rows), ""]
    lines += [
        "## Review",
        "",
        (
            "The requirements were drafted from the Doxygen comments of the headers and from what "
            "the tests check, so they describe the code. A reviewer must compare each one with the "
            "intended behaviour; the pull request that changes the file records who did."
        ),
        "",
    ]
    csv_rows = [
        (i.id, i.header, i.text, i.verification, " ".join(i.realizes), i.retired)
        for i in ctx.requirements
    ]
    csv_text = md.csv_text(
        ("id", "header", "text", "verification", "realizes", "retired"), csv_rows
    )
    return "\n".join(lines), csv_text


def _design(ctx: RuntimeContext) -> str:
    lines = _preamble(ctx, "Runtime design")
    rows = []
    for path in _headers(ctx):
        source = path.read_text(encoding="utf-8")
        brief = BRIEF.search(source)
        includes = sorted(set(INCLUDE.findall(source)))
        count = sum(1 for item in _active(ctx) if item.header == path.name)
        rows.append((path.name, brief[1] if brief else "-", ", ".join(includes) or "-", count))
    headers = ("Header", "Purpose", "Includes", "Requirements")
    lines += ["## Headers", "", *md.table(headers, rows), ""]
    lines += [
        "## Dependencies",
        "",
        (
            "The runtime is header-only and allocates no memory. A header includes only the "
            "headers listed in the table."
        ),
        "",
    ]
    return "\n".join(lines)


def _test_spec(ctx: RuntimeContext) -> tuple[str, str]:
    lines = _preamble(ctx, "Runtime test specification")
    rows = [
        (
            f"{md.anchor('test-' + item.id)}{item.id}",
            item.file,
            md.links(item.ids) if item.ids else "-",
        )
        for item in ctx.tests
    ]
    lines += ["## Tests", "", *md.table(("ID", "File", "Verifies"), rows), ""]
    csv_text = md.csv_text(
        ("id", "file", "verifies"), [(i.id, i.file, " ".join(i.ids)) for i in ctx.tests]
    )
    return "\n".join(lines), csv_text


def _test_report(ctx: RuntimeContext) -> str:
    lines = _preamble(ctx, "Runtime test report")
    lines += ["## Results", ""]
    if ctx.results is None:
        return "\n".join(
            [
                *lines,
                "Test results: not provided.",
                "",
                "## Coverage",
                "",
                "Coverage: not provided.",
                "",
            ]
        )
    counts = [
        (state, sum(item.outcome == state for item in ctx.results))
        for state in ("passed", "failed", "skipped")
    ]
    lines += [*md.table(("Outcome", "Tests"), counts), "", "## Coverage", ""]
    coverage = ctx.coverage or ()
    rows = [
        (
            item.file,
            f"{item.lines_covered} / {item.lines_total}",
            f"{100 * item.lines_covered / item.lines_total:.1f} %" if item.lines_total else "-",
        )
        for item in coverage
    ]
    lines += [*md.table(("File", "Statements", "Cover"), rows), ""]
    unjustified, unused = test_results.match_coverage(coverage, list(ctx.deviations))
    gaps = [(g.file, g.function, ", ".join(str(n) for n in g.lines)) for g in unjustified]
    lines += ["### Uncovered lines without a justification", ""]
    lines += [*md.table(("File", "Function", "Lines"), gaps), ""]
    lines += ["### Unused justifications", ""]
    lines += [f"- {item.file} {item.function}" for item in unused] or ["none"]
    return "\n".join([*lines, ""])


def _traceability(ctx: RuntimeContext) -> tuple[str, str]:
    lines = _preamble(ctx, "Runtime traceability")
    plain = []
    for item in _active(ctx):
        tests = _tests_of(ctx, item.id)
        plain.append(
            (
                item.id,
                runtime_reqs.symbol_line(item, ctx.include_dir),
                tests,
                _outcome(ctx, tests),
            )
        )
    rows = [
        (md.link(i), code, ", ".join(tests) or "-", out or "-") for i, code, tests, out in plain
    ]
    headers = ("Requirement", "Code", "Tests", "Result")
    lines += ["## Requirements to code and tests", "", *md.table(headers, rows), ""]
    csv_text = md.csv_text(
        ("requirement", "code", "tests", "result"),
        [(i, code, " ".join(tests), out) for i, code, tests, out in plain],
    )
    return "\n".join(lines), csv_text


def _environment(ctx: RuntimeContext) -> str:
    lines = _preamble(ctx, "Runtime environment")
    rows = [
        (path.name, f"`{hashlib.sha256(path.read_bytes()).hexdigest()}`") for path in _headers(ctx)
    ]
    lines += ["## Header hashes", "", *md.table(("Header", "sha256"), rows), ""]
    versions = [
        ("WireForge", versioning.TOOL_VERSION),
        ("Runtime", runtime_reqs.version(ctx.include_dir)),
    ]
    lines += ["## Tool versions", "", *md.table(("Item", "Version"), versions), ""]
    return "\n".join(lines)


# Table, objectives, our own summary, evidence. The runtime has no model, so its dossier is smaller
# than the one of a project; everything not listed here is the applicant's.
DO178C_ROWS = (
    ("A-2", "1,4", "Requirements of the runtime", "requirements.md#requirements"),
    ("A-2", "6", "Source code: the headers", "environment.md#header-hashes"),
    ("A-4", "1,2,4,6", "Requirements are traceable to code and tests", "traceability.md"),
    ("A-5", "1,2,5", "Code is traceable to requirements", "traceability.md"),
    ("A-6", "3,4", "Tests verify the requirements", "test_spec.md#tests"),
    ("A-7", "1,2", "Test procedures and results", "test_report.md#results"),
    ("A-7", "3,4", "Test coverage of the requirements", "traceability.md"),
    ("A-7", "7", "Statement coverage", "test_report.md#coverage"),
    ("A-8", "all", "Configuration management", "WireForge project: git history and CI"),
    ("A-9", "all", "Quality assurance", "WireForge project: CI gates"),
)


def _do178c(ctx: RuntimeContext) -> str:
    lines = _preamble(ctx, "DO-178C DAL C mapping of the runtime dossier")
    lines += ["## Scope", "", LIMITS, ""]
    rows = []
    for table, numbers, summary, evidence in DO178C_ROWS:
        shown = evidence if evidence.startswith("WireForge") else f"[{evidence}]({evidence})"
        rows.append((table, numbers, summary, shown))
    lines += [
        "## Objectives",
        "",
        *md.table(("Table", "Objectives", "Summary", "Evidence"), rows),
        "",
    ]
    lines += ["Rows that are not listed are the applicant's.", ""]
    return "\n".join(lines)


def render(ctx: RuntimeContext) -> dict[str, str]:
    """Render the runtime documents: file name -> content."""
    findings = strict_findings(ctx)
    requirements, requirements_csv = _requirements(ctx)
    spec, spec_csv = _test_spec(ctx)
    traceability, traceability_csv = _traceability(ctx)
    return {
        "index.md": _index(ctx, findings),
        "requirements.md": requirements,
        "requirements.csv": requirements_csv,
        "design.md": _design(ctx),
        "test_spec.md": spec,
        "test_spec.csv": spec_csv,
        "test_report.md": _test_report(ctx),
        "traceability.md": traceability,
        "traceability.csv": traceability_csv,
        "environment.md": _environment(ctx),
        "do178c_dal_c.md": _do178c(ctx),
    }
