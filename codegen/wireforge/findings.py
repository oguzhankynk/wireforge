"""Findings of the customer's coding-rule checker (MISRA, AUTOSAR, ...) as input to the documents.

WireForge does not check coding rules itself; a project that has to show conformance uses a
licensed checker. `load()` reads what that tool reported, as SARIF 2.1.0 or as cppcheck XML
version 2, and keeps the findings that lie in generated code or in the runtime headers. Everything
else is counted and left out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from wireforge.results import Deviation, Node, ResultsError, glob, normalize_header, parse_xml

if TYPE_CHECKING:
    from pathlib import Path

SARIF_VERSION = "2.1.0"


@dataclass(frozen=True)
class RuleFinding:
    """One finding of a rule checker."""

    tool: str
    rule: str
    severity: str
    file: str
    line: int | None
    function: str | None
    message: str


def _first_line(text: str) -> str:
    return next(iter(text.strip().splitlines()), "")


def _sarif(document: dict[str, Any]) -> list[RuleFinding]:
    found: list[RuleFinding] = []
    for run in document.get("runs", []):
        tool = str(run.get("tool", {}).get("driver", {}).get("name", "unknown"))
        for result in run.get("results", []):
            locations = result.get("locations") or [{}]
            physical = locations[0].get("physicalLocation", {})
            uri = str(physical.get("artifactLocation", {}).get("uri", ""))
            logical = (locations[0].get("logicalLocations") or [{}])[0]
            found.append(
                RuleFinding(
                    tool,
                    str(result.get("ruleId", "")),
                    str(result.get("level", "warning")),
                    uri.removeprefix("file://"),
                    physical.get("region", {}).get("startLine"),
                    logical.get("fullyQualifiedName") or logical.get("name"),
                    _first_line(str(result.get("message", {}).get("text", ""))),
                )
            )
    return found


def _cppcheck(root: Node) -> list[RuleFinding]:
    found: list[RuleFinding] = []
    for error in root.iter("error"):
        place = error.find("location")
        line = place.get("line") if place is not None else ""
        found.append(
            RuleFinding(
                "cppcheck",
                error.get("id"),
                error.get("severity"),
                place.get("file") if place is not None else "",
                int(line) if line.isdigit() else None,
                None,
                _first_line(error.get("msg")),
            )
        )
    return found


def read(path: Path) -> list[RuleFinding]:
    """Read every finding of a SARIF 2.1.0 or cppcheck XML v2 file."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        message = f"{path}: cannot read: {error}"
        raise ResultsError(message) from error
    if text.lstrip().startswith("<"):
        try:
            root = parse_xml(text)
        except Exception as error:  # expat errors
            message = f"{path}: not a rule checker report: {error}"
            raise ResultsError(message) from error
        if root.tag != "results" or root.get("version") != "2":
            message = f"{path}: XML is neither cppcheck results version 2 nor SARIF"
            raise ResultsError(message)
        return _cppcheck(root)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        message = f"{path}: not SARIF or cppcheck XML: {error}"
        raise ResultsError(message) from error
    if not isinstance(document, dict) or document.get("version") != SARIF_VERSION:
        message = f"{path}: not SARIF {SARIF_VERSION} (missing or other `version`)"
        raise ResultsError(message)
    return _sarif(document)


def load(paths: list[Path]) -> tuple[list[RuleFinding], int]:
    """Return (findings in generated code or the runtime, number of findings elsewhere)."""
    inside: list[RuleFinding] = []
    outside = 0
    for path in paths:
        for item in read(path):
            name = normalize_header(item.file)
            if name is None:
                outside += 1
            else:
                inside.append(
                    RuleFinding(
                        item.tool,
                        item.rule,
                        item.severity,
                        name,
                        item.line,
                        item.function,
                        item.message,
                    )
                )
    return inside, outside


@dataclass(frozen=True)
class RuleGap:
    """Findings of one rule of one tool in one file, with their lines."""

    tool: str
    rule: str
    file: str
    lines: tuple[int, ...]
    count: int


def group(findings: list[RuleFinding]) -> list[RuleGap]:
    """Group findings by tool, rule and file."""
    found: dict[tuple[str, str, str], list[RuleFinding]] = {}
    for item in findings:
        found.setdefault((item.tool, item.rule, item.file), []).append(item)
    return [
        RuleGap(
            tool,
            rule,
            file,
            tuple(sorted({i.line for i in items if i.line is not None})),
            len(items),
        )
        for (tool, rule, file), items in sorted(found.items())
    ]


def match_rules(
    findings: list[RuleFinding], deviations: list[Deviation]
) -> tuple[list[RuleFinding], list[Deviation]]:
    """Split findings into those without a deviation and report the deviations nobody used.

    A deviation matches by tool (case-insensitive), rule (as the tool names it), file (glob) and,
    when it names one, function (glob).
    """
    rules = [(index, item) for index, item in enumerate(deviations) if item.kind == "rule"]
    used: set[int] = set()
    open_findings: list[RuleFinding] = []
    for finding in findings:
        matched = False
        for index, deviation in rules:
            if (
                deviation.tool.lower() == finding.tool.lower()
                and deviation.rule == finding.rule
                and glob(deviation.file, finding.file)
                and (deviation.function == "*" or glob(deviation.function, finding.function or ""))
            ):
                used.add(index)
                matched = True
        if not matched:
            open_findings.append(finding)
    unused = [item for index, item in rules if index not in used]
    return open_findings, unused
