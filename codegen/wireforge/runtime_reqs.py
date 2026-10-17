"""Requirements of the runtime library (`runtime/requirements.yaml`) and their link to its tests.

The runtime is written by hand and does not come from a model, so its requirements are written and
reviewed by people. This module reads the file, checks it against the headers (rules RR-1 to RR-3),
finds the requirement ids the runtime tests record and answers which runtime requirements realize
which kind of generated low-level requirement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from wireforge import resources, schema
from wireforge.issues import Issue
from wireforge.loader import to_plain

HEAD = re.compile(r"^(?:TEST|TEST_F|TYPED_TEST|TEST_P)\((\w+), (\w+)\) \{$", re.MULTILINE)
PROPERTY = re.compile(r'^\s*::testing::Test::RecordProperty\("requirements", "([^"]*)"\);')
VERSION = re.compile(r"kVersion(Major|Minor|Patch) = (\d+)U;")
# Generated requirements without a runtime counterpart: they come from the model alone.
GENERATED_ONLY = frozenset({"DEFAULT", "VALUES"})


@dataclass(frozen=True)
class RuntimeRequirement:
    """One requirement of the runtime."""

    id: str
    text: str
    header: str
    code: tuple[str, ...]
    verification: str
    realizes: tuple[str, ...] = ()
    retired: bool = False


@dataclass(frozen=True)
class RuntimeTest:
    """A runtime unit test and the requirement ids it records."""

    id: str
    ids: tuple[str, ...]
    file: str


def load(path: Path | None = None) -> list[RuntimeRequirement]:
    """Read and validate the requirements file (schema errors raise ValueError)."""
    path = path or resources.runtime_requirements()
    document = schema.load_yaml(path)
    issues = schema.validate_document(
        document, "runtime_requirements.schema.json", file=str(path), source=document
    )
    if issues:
        raise ValueError("\n".join(str(issue) for issue in issues))
    entries = cast("dict[str, Any]", to_plain(document))["requirements"]
    return [
        RuntimeRequirement(
            ident,
            item["text"],
            item["header"],
            tuple(item["code"]),
            item["verification"],
            tuple(item.get("realizes", ())),
            bool(item.get("retired", False)),
        )
        for ident, item in entries.items()
    ]


def check(
    requirements: list[RuntimeRequirement], include_dir: Path, *, file: str = "requirements.yaml"
) -> list[Issue]:
    """Check the requirements against the headers.

    RR-1: the id names the header it belongs to. RR-2: the header exists. RR-3: every code symbol
    appears in the header.
    """
    issues: list[Issue] = []
    for item in requirements:
        pointer = f"/requirements/{item.id}"
        if item.id.split("-")[1] != Path(item.header).stem:
            text = f"[RR-1] {item.id} belongs to {item.header}, the id names another header"
            issues.append(Issue(file, None, pointer, text))
        header = include_dir / "wireforge" / item.header
        if not header.exists():
            issues.append(Issue(file, None, pointer, f"[RR-2] {item.header} does not exist"))
            continue
        source = header.read_text(encoding="utf-8")
        issues += [
            Issue(file, None, pointer, f"[RR-3] {symbol} is not in {item.header}")
            for symbol in item.code
            if re.search(rf"\b{re.escape(symbol)}\b", source) is None
        ]
    return issues


def symbol_line(item: RuntimeRequirement, include_dir: Path) -> str:
    """Return `wireforge/<header>:<line>` of the first code symbol found in the header."""
    header = include_dir / "wireforge" / item.header
    lines = header.read_text(encoding="utf-8").splitlines()
    for symbol in item.code:
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        for number, text in enumerate(lines, start=1):
            if pattern.search(text) and not text.lstrip().startswith(("///", "*", "/*")):
                return f"wireforge/{item.header}:{number}"
    return f"wireforge/{item.header}"


@lru_cache(maxsize=1)
def realized_by_kind() -> dict[str, tuple[str, ...]]:
    """Map each generated requirement kind to the active runtime requirements that realize it."""
    path = resources.runtime_requirements()
    if not path.exists():
        return {}
    found: dict[str, list[str]] = {}
    for item in load(path):
        if not item.retired:
            for kind in item.realizes:
                found.setdefault(kind, []).append(item.id)
    return {kind: tuple(ids) for kind, ids in found.items()}


def tests(directory: Path) -> list[RuntimeTest]:
    """Find the unit tests of a directory with the requirement ids each one records."""
    found: list[RuntimeTest] = []
    for path in sorted(directory.glob("*_test.cpp")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            head = HEAD.match(line)
            if head is None:
                continue
            following = lines[index + 1] if index + 1 < len(lines) else ""
            recorded = PROPERTY.match(following)
            ids = tuple(part for part in recorded[1].split(",") if part) if recorded else ()
            found.append(RuntimeTest(f"{head[1]}.{head[2]}", ids, path.name))
    return found


def version(include_dir: Path) -> str:
    """Return the runtime version from `version.hpp`."""
    text = (include_dir / "wireforge" / "version.hpp").read_text(encoding="utf-8")
    parts = dict(VERSION.findall(text))
    return f"{parts.get('Major', '?')}.{parts.get('Minor', '?')}.{parts.get('Patch', '?')}"
