"""Traceability: requirements to model elements, generated code and tests.

`trace()` renders the generated code and test sources of a valid project in memory and reports,
per enum, checksum, type, message, field and interface: the defining file and line of the model
(the ICD row), the C++ symbol, the generated file and line that defines it, the high-level
requirements it implements and the generated tests that exercise it. `trace_llrs()` reports the
low-level requirements derived from the model (`wireforge.llr`) with the same columns.

The test side of the link is read from the generated test sources themselves: every `TEST` records
the requirement ids it verifies as the first statement of its body, so the report cannot drift from
the tests. The report is exported as Markdown or CSV (`wireforge trace`).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from wireforge import cpp_tests, generate, golden, layout, llr, test_cases
from wireforge import vectors as requirement_vectors
from wireforge.cpp import wire_only
from wireforge.generate import source_text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from wireforge.codec import ChecksumPlugin
    from wireforge.model import Field, Project, Struct

PROFILE_OR_TEST = re.compile(
    r"^#if defined\(WIREFORGE_PROFILE_(?P<opened>\w+)\)$"
    r"|^(?P<closed>#endif // WIREFORGE_PROFILE_\w+)$"
    r"|^TEST\((?P<suite>\w+), (?P<name>\w+)\) \{\n"
    r'(?:\s*::testing::Test::RecordProperty\("requirements", "(?P<ids>[^"]*)"\);)?',
    re.MULTILINE,
)
CSV_COLUMNS = ("element", "kind", "source", "symbol", "generated", "requirements", "llrs", "tests")
# Declaration of the generated C++ that implements a requirement kind; `{o}` is the owner name.
VALIDATE = r"Status validate\(const {o}& value"
LOCATIONS = {
    "VALUES": r"^enum class {o}\b",
    "ALGORITHM": r"^using {o} =",
    "LAYOUT": r"inline void encode\(const {o}& value",
    "SIZE": r"min_wire_size\(wireforge::TypeTag<{o}>",
    "TRUNCATED": r"Status deserialize\(.*\b{o}& value",
    "TRAILING": r"Status deserialize\(.*\b{o}& value",
    "RANGE": VALIDATE,
    "ENUM": VALIDATE,
    "BITS": VALIDATE,
    "COUNT": VALIDATE,
    "FRAME_ENCODE": r"Status encode_frame\(",
    "FRAME_SYNC": r"^class Receiver\b",
    "FRAME_LENGTH": r"^class Receiver\b",
    "FRAME_RESYNC": r"^class Receiver\b",
    "FRAME_MAX": r"^class Receiver\b",
    "DISPATCH": r"^class Handler\b",
    "DISPATCH_UNKNOWN": r"^class Handler\b",
    "RAW": r"^class Handler\b",
}


@dataclass(frozen=True)
class TraceRow:
    """One model element and where it went."""

    element: str
    kind: str
    source: str
    symbol: str
    generated: str
    requirements: tuple[str, ...]
    llrs: tuple[str, ...]
    tests: tuple[str, ...]


@dataclass(frozen=True)
class LlrRow:
    """One low-level requirement and where it went."""

    id: str
    kind: str
    element: str
    text: str
    parents: tuple[str, ...]
    derived: bool
    generated: str
    tests: tuple[str, ...]
    runtime_verified: bool
    realized_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedTest:
    """A `TEST` of the generated sources: its requirement ids and the profile block it sits in."""

    suite: str
    name: str
    ids: tuple[str, ...]
    profile: str | None
    file: str

    @property
    def id(self) -> str:
        """Return `Suite.Name`."""
        return f"{self.suite}.{self.name}"


@dataclass(frozen=True)
class Report:
    """Both views of the trace."""

    rows: list[TraceRow]
    llrs: list[LlrRow]


class _Locator:
    """Finds definitions in the generated files."""

    def __init__(self, files: dict[str, str]) -> None:
        self.lines = {name: text.splitlines() for name, text in files.items()}

    def find(self, file: str, pattern: re.Pattern[str], start: int = 0) -> int | None:
        for index, line in enumerate(self.lines.get(file, [])[start:], start=start):
            if pattern.search(line):
                return index + 1
        return None


class _Tracer:
    def __init__(
        self,
        project: Project,
        files: dict[str, str],
        tests: list[tuple[str, str, tuple[str, ...]]],
        llrs: list[llr.Llr],
    ) -> None:
        self.project = project
        self.locator = _Locator(files)
        self.model = f"{project.name}/model.hpp"
        self.namespace = project.namespace or project.name
        self.llrs = llrs
        self.by_id: dict[str, list[str]] = {}
        for suite, name, ids in tests:
            for ident in ids:
                self.by_id.setdefault(ident, []).append(f"{suite}.{name}")
        self.lines: dict[str, int | None] = {}  # model pointer -> generated line

    def where(self, file: str, line: int | None) -> str:
        return f"{file}:{line}" if line is not None else ""

    def tests_of(self, llrs: list[llr.Llr]) -> tuple[str, ...]:
        found = {test for item in llrs for test in self.by_id.get(llr.display_id(item.key), ())}
        return tuple(sorted(found))

    def of_element(self, element: str, *, below: str | None = None) -> list[llr.Llr]:
        """Return the requirements of one element, plus those of the elements below it."""
        return [
            item
            for item in self.llrs
            if item.element == element or (below is not None and item.element.startswith(below))
        ]

    def link(self, row: TraceRow, below: str | None = None) -> TraceRow:
        """Fill the requirement and test columns of a row from the LLRs at (and below) it."""
        found = self.of_element(row.element, below=below)
        own = [item for item in found if row.kind == "interface" or item.element == row.element]
        return replace(
            row,
            llrs=tuple(llr.display_id(item.key) for item in own),
            tests=self.tests_of(found),
        )

    def struct(self, item: Struct, section: str) -> Iterator[TraceRow]:
        kind = "message" if item.message is not None else "type"
        start = self.locator.find(self.model, re.compile(rf"^struct {item.name} \{{"))
        element = f"/{section}/{item.name}"
        self.lines[element] = start
        row = TraceRow(
            element,
            kind,
            source_text(self.project, item.source),
            f"{self.namespace}::{item.name}",
            self.where(self.model, start),
            tuple(sorted(item.reqs)),
            (),
            (),
        )
        yield self.link(row, below=f"{element}/fields/")
        hidden = wire_only(item)
        for index, field in enumerate(item.fields):
            yield self.field(item, field, index, start, hidden)

    def field(
        self, item: Struct, field: Field, index: int, start: int | None, hidden: set[str]
    ) -> TraceRow:
        section = "messages" if item.message is not None else "types"
        if field.name in hidden:
            line = self.locator.find(self.model, re.compile(rf"encode\(const {item.name}& value"))
            symbol = f"{self.namespace}::encode({item.name}) (wire only)"
        else:
            pattern = re.compile(rf"\b{field.name}\b.*///<")
            line = self.locator.find(self.model, pattern, (start or 1) - 1) if start else None
            symbol = f"{self.namespace}::{item.name}::{field.name}"
        element = f"/{section}/{item.name}/fields/{index}"
        self.lines[element] = line
        row = TraceRow(
            element,
            "field",
            source_text(self.project, field.source),
            symbol,
            self.where(self.model, line),
            tuple(sorted(field.reqs)),
            (),
            (),
        )
        return self.link(row)

    def rows(self) -> Iterator[TraceRow]:
        for enum in self.project.enums.values():
            line = self.locator.find(self.model, re.compile(rf"^enum class {enum.name}\b"))
            row = TraceRow(
                f"/enums/{enum.name}",
                "enum",
                source_text(self.project, enum.source),
                f"{self.namespace}::{enum.name}",
                self.where(self.model, line),
                tuple(sorted(enum.reqs)),
                (),
                (),
            )
            yield self.link(row)
        for checksum in self.project.checksums.values():
            line = self.locator.find(self.model, re.compile(rf"^using {checksum.name} ="))
            row = TraceRow(
                f"/checksums/{checksum.name}",
                "checksum",
                source_text(self.project, checksum.source),
                f"{self.namespace}::checksums::{checksum.name}",
                self.where(self.model, line),
                tuple(sorted(checksum.reqs)),
                (),
                (),
            )
            yield self.link(row)
        for item in self.project.types.values():
            yield from self.struct(item, "types")
        for item in self.project.messages.values():
            yield from self.struct(item, "messages")
        for interface in self.project.interfaces.values():
            file = f"{self.project.name}/interfaces/{interface.name}.hpp"
            line = self.locator.find(file, re.compile(rf"^namespace .*::{interface.name} \{{"))
            element = f"/interfaces/{interface.name}"
            self.lines[element] = line
            row = TraceRow(
                element,
                "interface",
                source_text(self.project, interface.source),
                f"{self.namespace}::{interface.name}",
                self.where(file, line),
                tuple(sorted(interface.reqs)),
                (),
                (),
            )
            yield self.link(row, below=f"{element}/")

    # --- low-level requirements -----------------------------------------------------------------

    def location(self, item: llr.Llr) -> str:
        """Return the generated file and line implementing a requirement ('' when unknown)."""
        owner = item.key.split(".", 1)[0]
        pattern = LOCATIONS.get(item.kind)
        if item.element.startswith("/interfaces/"):
            file = f"{self.project.name}/interfaces/{owner}.hpp"
        else:
            file = self.model
        line = None
        if pattern is not None:
            compiled = re.compile(pattern.replace("{o}", re.escape(owner)))
            line = self.locator.find(file, compiled)
        if line is None:  # a member of a field, or the element that holds the requirement
            line = self.lines.get(item.element)
        if line is None:
            line = self.lines.get("/".join(item.element.split("/")[:3]))
        return self.where(file, line)

    def llr_rows(self) -> Iterator[LlrRow]:
        for item in self.llrs:
            ident = llr.display_id(item.key)
            yield LlrRow(
                ident,
                item.kind,
                item.element,
                item.text,
                item.parents,
                item.derived,
                self.location(item),
                self.tests_of([item]),
                item.runtime_verified,
                item.realized_by,
            )


def render_test_sources(
    project: Project, plugins: dict[str, ChecksumPlugin] | None = None
) -> dict[str, str]:
    """Render the three GoogleTest sources of a valid project: file name -> content."""
    profiles = test_cases.cases(project, plugins)
    golden_vectors = golden.vectors(project, plugins)
    sizes = layout.analyze(project)
    files = {
        "golden_test.cpp": cpp_tests.golden_test_source(project, golden_vectors),
        "message_test.cpp": cpp_tests.message_test_source(project, profiles, sizes),
        "interface_test.cpp": cpp_tests.interface_test_source(project, profiles, sizes),
    }
    if project.vectors:
        runs = requirement_vectors.run(project, plugins)
        files["hlr_vector_test.cpp"] = cpp_tests.hlr_vector_test_source(project, runs)
    return files


def parse_tests(sources: dict[str, str]) -> list[ParsedTest]:
    """Read every `TEST` of the generated sources with its requirements and profile block."""
    found: list[ParsedTest] = []
    for file, text in sources.items():
        profile: str | None = None
        for match in PROFILE_OR_TEST.finditer(text):
            if match["opened"]:
                profile = match["opened"]
            elif match["closed"]:
                profile = None
            else:
                ids = tuple(match["ids"].split(",")) if match["ids"] else ()
                found.append(ParsedTest(match["suite"], match["name"], ids, profile, file))
    return found


def report(
    project: Project,
    content_hash: str,
    plugins: dict[str, ChecksumPlugin] | None = None,
) -> Report:
    """Trace rows and low-level requirement rows of a valid project."""
    files = generate.render(project, content_hash)
    parsed = parse_tests(render_test_sources(project, plugins))
    tests = sorted({(item.suite, item.name, item.ids) for item in parsed})
    tracer = _Tracer(project, files, tests, llr.derive(project))
    return Report(list(tracer.rows()), list(tracer.llr_rows()))


def trace(
    project: Project,
    content_hash: str,
    plugins: dict[str, ChecksumPlugin] | None = None,
) -> list[TraceRow]:
    """Trace rows of a valid project."""
    return report(project, content_hash, plugins).rows


def trace_llrs(
    project: Project,
    content_hash: str,
    plugins: dict[str, ChecksumPlugin] | None = None,
) -> list[LlrRow]:
    """Low-level requirement rows of a valid project."""
    return report(project, content_hash, plugins).llrs


def to_csv(rows: list[TraceRow]) -> str:
    """One CSV row per traced element (requirements, LLRs and tests separated by spaces)."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **asdict(row),
                "requirements": " ".join(row.requirements),
                "llrs": " ".join(row.llrs),
                "tests": " ".join(row.tests),
            }
        )
    return out.getvalue()


def _names(items: list[str]) -> str:
    return ", ".join(items) or "none"


def to_markdown(
    rows: list[TraceRow], project: Project, content_hash: str, llrs: list[LlrRow]
) -> str:
    """Trace report: element, source, symbol, generated location, requirements and test count."""
    lines = [
        f"# Trace report {project.name} {project.version or ''}".rstrip(),
        "",
        f"Model hash `{content_hash}` (also in the banner of every generated file).",
        "",
        "| Element | Kind | Source | C++ symbol | Generated | Requirements | Tests |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        tests = str(len(row.tests)) if row.tests else "-"
        reqs = ", ".join(row.requirements) or "-"
        cells = (
            f"`{row.element}`",
            row.kind,
            row.source,
            f"`{row.symbol}`",
            row.generated,
            reqs,
            tests,
        )
        lines.append("| " + " | ".join(cells) + " |")
    untested = [
        row.element for row in rows if row.kind in {"message", "interface"} and not row.tests
    ]
    lines += ["", f"Messages and interfaces without tests: {', '.join(untested) or 'none'}."]
    lines += ["", "## Low-level requirements", ""]
    lines += ["| ID | Kind | Parents | Generated | Tests |", "|---|---|---|---|---|"]
    for item in llrs:
        parents = ", ".join(item.parents) or "derived"
        tests = str(len(item.tests)) if item.tests else "-"
        lines.append(f"| `{item.id}` | {item.kind} | {parents} | {item.generated} | {tests} |")
    untested_llrs = [item.id for item in llrs if not item.tests and not item.runtime_verified]
    runtime = [item.id for item in llrs if item.runtime_verified]
    lines += ["", f"LLRs without tests: {_names(untested_llrs)}."]
    lines += [f"LLRs verified by the runtime tests: {_names(runtime)}."]
    if project.requirements:
        used = {parent for row in rows for parent in row.requirements}
        used |= {parent for item in llrs for parent in item.parents}
        unused = [name for name in project.requirements if name not in used]
        derived = [item.id for item in llrs if item.derived]
        lines += [f"Requirements without model elements: {_names(unused)}."]
        lines += [f"Derived LLRs (no requirement behind them): {_names(derived)}."]
    return "\n".join(lines) + "\n"
