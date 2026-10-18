"""The check a CI system runs on every change of a project: `wireforge ci`.

One call validates the project, compares it with the project at a git revision (the base of a pull
request) for wire compatibility and the version bump, and optionally generates, builds and runs the
tests. The result is one Markdown summary a pull request can show: what passed, which interfaces
changed, whether a change breaks the other side of the link, and the test line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wireforge import service, test_project
from wireforge.loader import load_project
from wireforge.service import BuildOptions, Result, ServiceError

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"
MAX_CHANGES = 20
TEST_LINE = re.compile(r"\d+% tests passed.*")
BUMPS = {"none": "none", "patch": "patch", "minor": "minor", "major": "major"}


@dataclass(frozen=True)
class CiOptions:
    """What `wireforge ci` does besides the validation."""

    base: str | None = None
    test: bool = False
    output: Path = Path("wireforge-ci")
    build: BuildOptions | None = None
    tests: test_project.TestOptions | None = None


def _status(result: Result | None) -> str:
    if result is None:
        return SKIPPED
    return PASSED if result.ok else FAILED


def _compat_section(compat: Result, base: str) -> list[str]:
    lines = [f"## Interfaces compared with `{base}`", ""]
    interfaces = compat.data.get("interfaces", [])
    if not interfaces:
        return [*lines, "No interface in common.", ""]
    lines += [
        "| Interface | Version | Needed bump | Actual bump | Result |",
        "|-----------|---------|-------------|-------------|--------|",
    ]
    for item in interfaces:
        versions = f"{item['old_version']} → {item['new_version']}"
        result = PASSED if item["version_ok"] else FAILED
        lines.append(
            f"| {item['interface']} | {versions} | {item['required_bump']} | "
            f"{item['actual_bump']} | {result} |"
        )
    lines.append("")
    for item in interfaces:
        changes = item["changes"]
        if not changes:
            continue
        lines += [f"### Changes of `{item['interface']}`", ""]
        lines += [f"- {change}" for change in changes[:MAX_CHANGES]]
        if len(changes) > MAX_CHANGES:
            lines.append(f"- … and {len(changes) - MAX_CHANGES} more")
        lines.append("")
    return lines


@dataclass(frozen=True)
class Checks:
    """The results a summary is made of."""

    name: str
    validation: Result
    compat: Result | None = None
    base: str | None = None
    tests: Result | None = None
    note: str = ""


def summary(checks: Checks) -> str:
    """Return the Markdown summary of a check."""
    name, validation, compat = checks.name, checks.validation, checks.compat
    base, tests, note = checks.base, checks.tests, checks.note
    rows = [("Validation", _status(validation))]
    if compat is not None:
        rows.append(("Wire compatibility", _status(compat)))
    rows.append(("Tests", _status(tests)))
    lines = [f"# WireForge: {name}", "", "| Check | Result |", "|-------|--------|"]
    lines += [f"| {check} | {result} |" for check, result in rows]
    lines.append("")
    if note:
        lines += [note, ""]
    if validation.issues:
        lines += ["## Validation", ""]
        lines += [f"- {issue}" for issue in validation.issues]
        lines.append("")
    if compat is not None and base is not None:
        lines += _compat_section(compat, base)
        if compat.issues:
            lines += [f"- {issue}" for issue in compat.issues] + [""]
    if tests is not None:
        found = TEST_LINE.search(tests.text)
        lines += ["## Tests", "", found.group(0) if found else tests.text.strip()[-400:], ""]
    return "\n".join(lines)


def run(location: Path, options: CiOptions | None = None) -> Result:
    """Validate, compare with the base revision and test; return the result with its summary."""
    options = options or CiOptions()
    validation = service.validate(location)
    loaded = load_project(location).project
    name = loaded.name if loaded is not None else Path(location).resolve().name
    compat: Result | None = None
    note = ""
    if options.base is not None and validation.ok:
        try:
            compat = service.compatibility_with_revision(location, options.base)
        except ServiceError as error:
            note = f"Not compared with `{options.base}`: {error}"
    tests: Result | None = None
    if options.test and validation.ok:
        tests = service.run_tests(location, options.output, options.tests, options.build)
    ok = validation.ok and (compat is None or compat.ok) and (tests is None or tests.ok)
    text = summary(Checks(name, validation, compat, options.base, tests, note))
    data: dict[str, Any] = {
        "validation": validation.ok,
        "compatibility": None if compat is None else compat.data,
        "tests": None if tests is None else tests.ok,
        "summary": text,
    }
    return Result(ok, validation.issues, data, text + "\n")
