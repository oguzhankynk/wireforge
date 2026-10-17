"""Results of a test run of the generated test project, and the deviations that explain gaps.

`load()` reads the `results/` directory the generated CMake project writes next to its build
(`junit/<executable>/*.xml`, `ctest.xml`, `coverage.json`, `coverage_runtime.json`,
`toolchain.json`) and the `.su` stack-usage files of the compiler. The reader is strict: a
missing or damaged file is an error, never a silently empty report. The only optional file is the
runtime coverage, which is measured only when the build asked for it.

`load_deviations()` reads the optional `deviations.yaml` of a project: justifications for lines the
tests do not reach. `match_coverage()` says which uncovered lines are justified.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from xml.parsers import expat

from wireforge import schema
from wireforge.loader import to_plain

if TYPE_CHECKING:
    from collections.abc import Iterable

DEVIATIONS_FILE = "deviations.yaml"
LAST_CHUNK = True  # expat's positional `isfinal`
SU_FIELDS = 2  # bytes and qualifier follow the function
SU_LINE = re.compile(r"^(?P<file>.*?):(?P<line>\d+):\d+:(?P<function>.*)$")


class ResultsError(ValueError):
    """A result file is missing, damaged or does not follow the expected layout."""


@dataclass
class Node:
    """An XML element: the few things the result readers need."""

    tag: str
    attrs: dict[str, str]
    children: list[Node]

    def find(self, tag: str) -> Node | None:
        """Return the first direct child with this tag."""
        return next((child for child in self.children if child.tag == tag), None)

    def iter(self, tag: str) -> list[Node]:
        """Return every descendant with this tag."""
        found = [child for child in self.children if child.tag == tag]
        for child in self.children:
            found += child.iter(tag)
        return found

    def get(self, name: str, default: str = "") -> str:
        """Return an attribute."""
        return self.attrs.get(name, default)


def parse_xml(text: str) -> Node:
    """Parse XML text into nodes (external entities are not resolved)."""
    root = Node("", {}, [])
    stack = [root]

    def start(tag: str, attrs: dict[str, str]) -> None:
        node = Node(tag, dict(attrs), [])
        stack[-1].children.append(node)
        stack.append(node)

    parser = expat.ParserCreate()
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda _tag: stack.pop()
    parser.Parse(text, LAST_CHUNK)
    return root.children[0]


@dataclass(frozen=True)
class TestResult:
    """Outcome of one test."""

    __test__ = False  # not a pytest class

    id: str
    executable: str
    outcome: str
    requirements: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class FileCoverage:
    """Statement and branch coverage of one header; `uncovered` lists (line, function)."""

    file: str
    lines_total: int
    lines_covered: int
    uncovered: tuple[tuple[int, str], ...]
    branches_total: int
    branches_covered: int


@dataclass(frozen=True)
class StackEntry:
    """Stack use of one function, the largest over all translation units."""

    file: str
    line: int
    function: str
    bytes: int
    qualifier: str


@dataclass(frozen=True)
class Results:
    """Everything a test run produced."""

    tests: tuple[TestResult, ...]
    selftests: tuple[TestResult, ...]
    generated: tuple[FileCoverage, ...]
    runtime: tuple[FileCoverage, ...] | None
    stack: tuple[StackEntry, ...]
    toolchain: dict[str, str]


@dataclass(frozen=True)
class Deviation:
    """A reviewed justification for a coverage gap."""

    kind: str
    file: str
    function: str
    category: str
    rationale: str
    reviewer: str
    lines: tuple[int, ...] = ()


def _need(path: Path) -> Path:
    if not path.exists():
        message = f"{path}: missing, the test run did not write it"
        raise ResultsError(message)
    return path


def _xml(path: Path) -> Node:
    try:
        return parse_xml(_need(path).read_text(encoding="utf-8"))
    except (expat.ExpatError, OSError, IndexError) as error:
        message = f"{path}: cannot read test results: {error}"
        raise ResultsError(message) from error


def _json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(_need(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        message = f"{path}: cannot read: {error}"
        raise ResultsError(message) from error
    if not isinstance(loaded, dict):
        message = f"{path}: not a JSON object"
        raise ResultsError(message)
    return loaded


def _case(element: Node, executable: str, name: str, classname: str) -> TestResult:
    ident = f"{classname}.{name}" if classname else name
    outcome, message = "passed", ""
    failure = element.find("failure") or element.find("error")
    if failure is not None:
        outcome = "failed"
        message = next(iter(failure.get("message").splitlines()), "")
    elif element.find("skipped") is not None or element.get("status") in {"notrun", "disabled"}:
        outcome = "skipped"
    requirements: tuple[str, ...] = ()
    for item in element.iter("property"):
        if item.get("name") == "requirements":
            requirements = tuple(part for part in item.get("value").split(",") if part)
    return TestResult(ident, executable, outcome, requirements, message)


def _gtest(directory: Path) -> tuple[TestResult, ...]:
    if not directory.is_dir() or not any(directory.rglob("*.xml")):
        message = f"{directory}: no GoogleTest results (junit/<executable>/*.xml)"
        raise ResultsError(message)
    found: list[TestResult] = []
    for path in sorted(directory.rglob("*.xml")):
        executable = path.parent.name if path.parent != directory else ""
        found += [
            _case(element, executable, element.get("name"), element.get("classname"))
            for element in _xml(path).iter("testcase")
        ]
    return tuple(found)


def _selftests(path: Path) -> tuple[TestResult, ...]:
    root = _xml(path)
    return tuple(
        _case(element, element.get("name"), element.get("name"), "")
        for element in root.iter("testcase")
        if "_selftest" in element.get("name")
    )


def _coverage(path: Path) -> tuple[FileCoverage, ...]:
    files: list[FileCoverage] = []
    entries = _json(path).get("files", [])
    if not entries:
        message = f"{path}: lists no files; the coverage filter matched nothing"
        raise ResultsError(message)
    for entry in entries:
        lines = [line for line in entry.get("lines", []) if not line.get("gcovr/noncode")]
        branches = [branch for line in lines for branch in line.get("branches", [])]
        uncovered = tuple(
            (int(line["line_number"]), str(line.get("function_name", "")))
            for line in lines
            if line.get("count", 0) == 0
        )
        files.append(
            FileCoverage(
                str(entry["file"]),
                len(lines),
                len(lines) - len(uncovered),
                uncovered,
                len(branches),
                sum(1 for branch in branches if branch.get("count", 0) > 0),
            )
        )
    return tuple(sorted(files, key=lambda item: item.file))


def _normalize(path: str) -> str | None:
    """Return the project- or runtime-relative name of a header, None for other files."""
    parts = Path(path).as_posix().split("/")
    if "interfaces" in parts and parts[-1].endswith(".hpp"):
        return "/".join(parts[parts.index("interfaces") - 1 :])
    if parts[-1] == "model.hpp" and len(parts) > 1:
        return "/".join(parts[-2:])
    if "wireforge" in parts and parts[-1].endswith(".hpp"):
        return "/".join(parts[len(parts) - 1 - parts[::-1].index("wireforge") :])
    return None


def _stack(build_dir: Path) -> tuple[StackEntry, ...]:
    largest: dict[tuple[str, str], StackEntry] = {}
    for path in sorted(build_dir.rglob("*.su")):
        for text in path.read_text(encoding="utf-8", errors="replace").splitlines():
            head, _, rest = text.partition("\t")
            match = SU_LINE.match(head)
            sizes = rest.split("\t")
            name = _normalize(match["file"]) if match else None
            if match is None or name is None or len(sizes) < SU_FIELDS:
                continue
            entry = StackEntry(name, int(match["line"]), match["function"], int(sizes[0]), sizes[1])
            key = (name, entry.function)
            if key not in largest or entry.bytes > largest[key].bytes:
                largest[key] = entry
    return tuple(sorted(largest.values(), key=lambda item: (item.file, item.line, item.function)))


def load(build_dir: Path) -> Results:
    """Read the results of a test run in `build_dir` (the build directory, not `results/`)."""
    results = build_dir / "results"
    if not results.is_dir():
        message = f"{results}: no results directory; build the coverage target of the test project"
        raise ResultsError(message)
    runtime_file = results / "coverage_runtime.json"
    toolchain = {str(key): str(value) for key, value in _json(results / "toolchain.json").items()}
    return Results(
        _gtest(results / "junit"),
        _selftests(results / "ctest.xml"),
        _coverage(results / "coverage.json"),
        _coverage(runtime_file) if runtime_file.exists() else None,
        _stack(build_dir),
        toolchain,
    )


# --- deviations ---------------------------------------------------------------------------------


def load_deviations(project_dir: Path) -> list[Deviation]:
    """Read the coverage deviations of a project directory (none without `deviations.yaml`)."""
    path = project_dir / DEVIATIONS_FILE
    if not path.exists():
        return []
    document = schema.load_yaml(path)
    issues = schema.validate_document(
        document, "deviations.schema.json", file=str(path), source=document
    )
    if issues:
        message = "\n".join(str(issue) for issue in issues)
        raise ResultsError(message)
    entries: list[dict[str, Any]] = cast("dict[str, Any]", to_plain(document))["deviations"]
    return [
        Deviation(
            "coverage",
            item["file"],
            item["function"],
            item["category"],
            item["rationale"],
            item["reviewer"],
            tuple(item.get("lines", ())),
        )
        for item in entries
        if item["kind"] == "coverage"
    ]


def _glob(pattern: str, text: str) -> bool:
    expression = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(expression, text) is not None


@dataclass(frozen=True)
class Gap:
    """Uncovered lines of one function of one file."""

    file: str
    function: str
    lines: tuple[int, ...]


def gaps(coverage: Iterable[FileCoverage]) -> list[Gap]:
    """Group the uncovered lines by file and function."""
    found: dict[tuple[str, str], list[int]] = {}
    for item in coverage:
        for line, function in item.uncovered:
            found.setdefault((item.file, function), []).append(line)
    return [Gap(file, function, tuple(lines)) for (file, function), lines in sorted(found.items())]


def match_coverage(
    coverage: Iterable[FileCoverage], deviations: list[Deviation]
) -> tuple[list[Gap], list[Deviation]]:
    """Split the gaps into unjustified ones and report the deviations nobody used.

    A deviation covers a gap when file and function match; with `lines` it covers only those lines,
    so a runtime update that moves the line makes the gap unjustified again.
    """
    used: set[int] = set()
    unjustified: list[Gap] = []
    for gap in gaps(coverage):
        remaining = list(gap.lines)
        for index, deviation in enumerate(deviations):
            if not (_glob(deviation.file, gap.file) and _glob(deviation.function, gap.function)):
                continue
            covered = [line for line in remaining if not deviation.lines or line in deviation.lines]
            if covered:
                used.add(index)
                remaining = [line for line in remaining if line not in covered]
        if remaining:
            unjustified.append(Gap(gap.file, gap.function, tuple(remaining)))
    unused = [item for index, item in enumerate(deviations) if index not in used]
    return unjustified, unused
