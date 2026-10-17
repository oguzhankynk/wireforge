"""Parts of the lifecycle documents that depend on the results of a test run.

The test report, the resource document, the toolchain section and the strict rules DS-5 to DS-7
live here. Without results every part says so: nothing is guessed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wireforge import docs_markdown as md
from wireforge import results as test_results

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from wireforge.lifecycle import DocsContext

PERCENT = 100
MEASURED_AT = "-O0 with coverage instrumentation: indicative, measure on the target"
RUNTIME_MISSING = "Runtime coverage: not measured in this build."


def outcome_of(ctx: DocsContext, ids: Iterable[str]) -> str:
    """Summarize the results of tests given by id: failed, skipped, passed or not run."""
    results = ctx.results
    if results is None:
        return ""
    wanted = set(ids)
    found = [item.outcome for item in (*results.tests, *results.selftests) if item.id in wanted]
    for state in ("failed", "skipped"):
        if state in found:
            return state
    return "passed" if found else "not run"


def _percent(covered: int, total: int) -> str:
    return f"{PERCENT * covered / total:.1f} %" if total else "-"


def findings(ctx: DocsContext) -> list[tuple[str, str, str]]:
    """Return (rule, message, pointer) for DS-5 (failed tests), DS-6 and DS-7 (coverage)."""
    results = ctx.results
    if results is None:
        return []
    found: list[tuple[str, str, str]] = []
    for item in (*results.tests, *results.selftests):
        if item.outcome != "passed":
            where = f" in {item.executable}" if item.executable else ""
            found.append(("DS-5", f"test {item.id}{where} {item.outcome}", ""))
    for entry in results.generated:
        if entry.lines_covered < entry.lines_total:
            text = (
                f"generated code {entry.file} covers {entry.lines_covered} of "
                f"{entry.lines_total} statements"
            )
            found.append(("DS-6", text, ""))
    if results.runtime is None:
        found.append(("DS-6", "runtime coverage was not measured in this build", ""))
    covered = [*results.generated, *(results.runtime or ())]
    unjustified, _ = test_results.match_coverage(covered, list(ctx.deviations))
    for gap in unjustified:
        lines = ", ".join(str(line) for line in gap.lines)
        found.append(("DS-7", f"{gap.file}: {gap.function} lines {lines} are not covered", ""))
    return found


def _coverage_rows(entries: Iterable[test_results.FileCoverage]) -> list[Sequence[object]]:
    return [
        (
            entry.file,
            f"{entry.lines_covered} / {entry.lines_total}",
            _percent(entry.lines_covered, entry.lines_total),
            f"{entry.branches_covered} / {entry.branches_total}",
        )
        for entry in entries
    ]


def _gap_rows(gaps: Iterable[test_results.Gap]) -> list[Sequence[object]]:
    return [(gap.file, gap.function, ", ".join(str(line) for line in gap.lines)) for gap in gaps]


def _results_sections(ctx: DocsContext, results: test_results.Results) -> list[str]:
    lines = ["### Summary", ""]
    executables = sorted({item.executable for item in results.tests})
    rows: list[Sequence[object]] = []
    for name in executables:
        mine = [item for item in results.tests if item.executable == name]
        rows.append((name, *(sum(item.outcome == s for item in mine) for s in _STATES)))
    rows.append(
        ("self-tests", *(sum(item.outcome == s for item in results.selftests) for s in _STATES))
    )
    lines += [*md.table(("Executable", *_STATES), rows), "", "### Results by requirement", ""]
    by_requirement: list[Sequence[object]] = []
    for rid in ctx.project.requirements:
        direct = {entry.id for entry in ctx.tests if rid in entry.verifies}
        low = {test for row in ctx.llr_rows if rid in row.parents for test in row.tests}
        tests = sorted(direct | low)
        by_requirement.append((md.link(rid), len(tests), outcome_of(ctx, tests)))
    by_requirement += [
        (md.link(row.id), len(row.tests), outcome_of(ctx, row.tests)) for row in ctx.llr_rows
    ]
    lines += [*md.table(("Requirement", "Tests", "Result"), by_requirement), "", "### Failures", ""]
    failed = [item for item in (*results.tests, *results.selftests) if item.outcome != "passed"]
    lines += [f"- {item.id} ({item.executable}): {item.outcome} {item.message}" for item in failed]
    return [*lines, *(["none"] if not failed else []), ""]


def test_report(ctx: DocsContext) -> str:
    """Render test_report.md."""
    lines = md.preamble(
        "Test report",
        ctx.project.name,
        ctx.project.version,
        ctx.content_hash,
        len(ctx.model_files),
    )
    robust = sum(1 for entry in ctx.tests if entry.kind == "robustness")
    counts = (("tests", len(ctx.tests)), ("robustness tests", robust))
    results = ctx.results
    lines += ["## Results", ""]
    if results is None:
        lines += ["Test results: not provided.", ""]
    else:
        lines += _results_sections(ctx, results)
    lines += [*md.table(("Item", "Count"), counts), "", "## Coverage", ""]
    if results is None:
        return "\n".join([*lines, "Coverage: not provided.", ""])
    lines += ["Generated code (statements and branches)", ""]
    lines += [
        *md.table(("File", "Statements", "Cover", "Branches"), _coverage_rows(results.generated))
    ]
    lines += ["", "Runtime headers (statements of the code that was compiled into the tests)", ""]
    if results.runtime is None:
        lines += [RUNTIME_MISSING]
    else:
        lines += md.table(
            ("File", "Statements", "Cover", "Branches"), _coverage_rows(results.runtime)
        )
    covered = [*results.generated, *(results.runtime or ())]
    unjustified, unused = test_results.match_coverage(covered, list(ctx.deviations))
    justified = [gap for gap in test_results.gaps(covered) if gap not in unjustified]
    lines += ["", "### Uncovered lines without a justification", ""]
    lines += md.table(("File", "Function", "Lines"), _gap_rows(unjustified))
    lines += ["", "### Uncovered lines with a justification", ""]
    lines += md.table(("File", "Function", "Lines"), _gap_rows(justified))
    lines += ["", "### Unused justifications", ""]
    lines += [f"- {item.file} {item.function}: {item.rationale}" for item in unused] or ["none"]
    return "\n".join([*lines, "", "## Self-tests", "", *_selftest_table(results), ""]) + "\n"


def _selftest_table(results: test_results.Results) -> list[str]:
    rows = [(item.id, item.outcome) for item in results.selftests]
    return md.table(("Self-test", "Result"), rows)


_STATES = ("passed", "failed", "skipped")


def resources(ctx: DocsContext) -> str:
    """Render resources.md: memory from the layout analysis and stack use from the build."""
    lines = md.preamble(
        "Resources",
        ctx.project.name,
        ctx.project.version,
        ctx.content_hash,
        len(ctx.model_files),
    )
    memory = [
        (
            profile.profile or "-",
            profile.link_ram,
            max((item.max_frame for item in profile.interfaces), default=0),
        )
        for profile in ctx.layout.profiles
    ]
    lines += [
        "## Memory",
        "",
        *md.table(("Profile", "Link RAM bytes", "Largest frame bytes"), memory),
    ]
    lines += ["", "## Stack usage", ""]
    results = ctx.results
    if results is None:
        return "\n".join([*lines, "Stack usage: not provided.", ""])
    lines += [
        f"Measured with {MEASURED_AT}. The largest value over all translation units is shown.",
        "",
    ]
    rows = [
        (f"{item.file}:{item.line}", item.function, item.bytes, item.qualifier)
        for item in results.stack
    ]
    lines += md.table(("Location", "Function", "Bytes", "Kind"), rows)
    return "\n".join([*lines, ""])


def toolchain_lines(ctx: DocsContext) -> list[str]:
    """Return the toolchain section of environment.md."""
    lines = ["## Toolchain", ""]
    if ctx.results is None:
        return [*lines, "Toolchain: not provided.", ""]
    rows = sorted(ctx.results.toolchain.items())
    return [*lines, *md.table(("Item", "Value"), rows), ""]
