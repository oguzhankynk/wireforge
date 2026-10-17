"""Traceability: every model element to its source line, generated C++ symbol and tests.

`trace()` renders the generated code and test sources of a valid project in memory and reports,
per enum, type, message, field and interface: the defining file and line of the model (the ICD
row), the C++ symbol, the generated file and line that defines it, and the generated tests that
exercise it (tests named after the message and, for fields, after the field). The report is
exported as Markdown or CSV (`wireforge trace`).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from wireforge import cpp_tests, generate, golden, test_cases
from wireforge.cpp import wire_only
from wireforge.generate import source_text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from wireforge.codec import ChecksumPlugin
    from wireforge.model import Field, Project, Struct

TEST = re.compile(r"^TEST\((\w+), (\w+)\)", re.MULTILINE)
CSV_COLUMNS = ("element", "kind", "source", "symbol", "generated", "tests")


@dataclass(frozen=True)
class TraceRow:
    """One model element and where it went."""

    element: str
    kind: str
    source: str
    symbol: str
    generated: str
    tests: tuple[str, ...]


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
        self, project: Project, files: dict[str, str], tests: list[tuple[str, str]]
    ) -> None:
        self.project = project
        self.locator = _Locator(files)
        self.model = f"{project.name}/model.hpp"
        self.namespace = project.namespace or project.name
        self.tests = tests

    def where(self, file: str, line: int | None) -> str:
        return f"{file}:{line}" if line is not None else ""

    def tests_of(self, message: str, field: str | None = None) -> tuple[str, ...]:
        return tuple(
            f"{suite}.{name}"
            for suite, name in self.tests
            if self.covers(suite, name, message, field)
        )

    @staticmethod
    def covers(suite: str, name: str, message: str, field: str | None) -> bool:
        if field is not None:
            return suite == message and (name == field or name.startswith(f"{field}_"))
        return suite == message or (suite == "Golden" and f"_{message}_" in name)

    def struct(self, item: Struct, section: str) -> Iterator[TraceRow]:
        kind = "message" if item.message is not None else "type"
        start = self.locator.find(self.model, re.compile(rf"^struct {item.name} \{{"))
        message = item.name if item.message is not None else None
        yield TraceRow(
            f"/{section}/{item.name}",
            kind,
            source_text(self.project, item.source),
            f"{self.namespace}::{item.name}",
            self.where(self.model, start),
            self.tests_of(message) if message else (),
        )
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
        tests = self.tests_of(item.name, field.name) if item.message is not None else ()
        return TraceRow(
            f"/{section}/{item.name}/fields/{index}",
            "field",
            source_text(self.project, field.source),
            symbol,
            self.where(self.model, line),
            tests,
        )

    def rows(self) -> Iterator[TraceRow]:
        for enum in self.project.enums.values():
            line = self.locator.find(self.model, re.compile(rf"^enum class {enum.name}\b"))
            yield TraceRow(
                f"/enums/{enum.name}",
                "enum",
                source_text(self.project, enum.source),
                f"{self.namespace}::{enum.name}",
                self.where(self.model, line),
                (),
            )
        for item in self.project.types.values():
            yield from self.struct(item, "types")
        for item in self.project.messages.values():
            yield from self.struct(item, "messages")
        for interface in self.project.interfaces.values():
            file = f"{self.project.name}/interfaces/{interface.name}.hpp"
            line = self.locator.find(file, re.compile(rf"^namespace .*::{interface.name} \{{"))
            tests = tuple(
                f"{suite}.{name}"
                for suite, name in self.tests
                if suite == f"Interface_{interface.name}"
                or (suite == "Golden" and name.startswith(f"{interface.name}_"))
            )
            yield TraceRow(
                f"/interfaces/{interface.name}",
                "interface",
                source_text(self.project, interface.source),
                f"{self.namespace}::{interface.name}",
                self.where(file, line),
                tests,
            )


def trace(
    project: Project,
    content_hash: str,
    plugins: dict[str, ChecksumPlugin] | None = None,
) -> list[TraceRow]:
    """Trace rows of a valid project."""
    files = generate.render(project, content_hash)
    profiles = test_cases.cases(project, plugins)
    vectors = golden.vectors(project, plugins)
    sources = (
        cpp_tests.message_test_source(project, profiles)
        + cpp_tests.golden_test_source(project, vectors)
        + cpp_tests.interface_test_source(project, profiles)
    )
    tests = sorted(set(TEST.findall(sources)))
    return list(_Tracer(project, files, tests).rows())


def to_csv(rows: list[TraceRow]) -> str:
    """One CSV row per traced element (tests separated by spaces)."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({**asdict(row), "tests": " ".join(row.tests)})
    return out.getvalue()


def to_markdown(rows: list[TraceRow], project: Project, content_hash: str) -> str:
    """Trace report: element, source, symbol, generated location and test count."""
    lines = [
        f"# Trace report {project.name} {project.version or ''}".rstrip(),
        "",
        f"Model hash `{content_hash}` (also in the banner of every generated file).",
        "",
        "| Element | Kind | Source | C++ symbol | Generated | Tests |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        tests = str(len(row.tests)) if row.tests else "-"
        cells = (f"`{row.element}`", row.kind, row.source, f"`{row.symbol}`", row.generated, tests)
        lines.append("| " + " | ".join(cells) + " |")
    untested = [
        row.element for row in rows if row.kind in {"message", "interface"} and not row.tests
    ]
    lines += ["", f"Messages and interfaces without tests: {', '.join(untested) or 'none'}."]
    return "\n".join(lines) + "\n"
