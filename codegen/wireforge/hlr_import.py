"""Import the high-level requirements of the customer's tool as a baseline snapshot.

The requirements stay in the customer's tool. WireForge keeps a read-only snapshot of one baseline
(`requirements/hlr_snapshot.yaml`) so the model can point at them with `reqs`. The snapshot records
where it came from and a hash of its content; importing over a file somebody edited by hand is
refused, because the next import would silently throw the edit away.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from wireforge import reqif, versioning
from wireforge.results import ResultsError

if TYPE_CHECKING:
    from pathlib import Path

SNAPSHOT = "requirements/hlr_snapshot.yaml"
INCLUDE = "requirements/*.yaml"
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
MAX_ID = 64


@dataclass(frozen=True)
class ImportOptions:
    """How to read the file of the customer's tool."""

    baseline: str
    tool: str | None = None
    id_attr: str | None = None
    text_attr: str | None = None
    csv_id_column: str = "id"
    csv_text_column: str = "text"
    force: bool = False


def _csv(path: Path, options: ImportOptions) -> list[reqif.Requirement]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        message = f"{path}: cannot read: {error}"
        raise ResultsError(message) from error
    rows = list(csv.DictReader(io.StringIO(text)))
    columns = list(rows[0]) if rows else []
    for wanted in (options.csv_id_column, options.csv_text_column):
        if rows and wanted not in columns:
            message = (
                f"{path}: no column {wanted!r}; the file has {', '.join(columns)}. "
                "Name the columns with --csv-id-column and --csv-text-column."
            )
            raise ResultsError(message)
    return [
        reqif.Requirement(row[options.csv_id_column].strip(), row[options.csv_text_column].strip())
        for row in rows
        if row[options.csv_id_column].strip()
    ]


def read(path: Path, options: ImportOptions) -> tuple[str, list[reqif.Requirement]]:
    """Read (tool, requirements) from a ReqIF (`.reqif`, `.reqifz`) or CSV file."""
    if path.suffix.lower() == ".csv":
        return options.tool or "csv", _csv(path, options)
    document = reqif.read(path, options.id_attr, options.text_attr)
    return options.tool or document.tool or "ReqIF", list(document.requirements)


def check(requirements: list[reqif.Requirement]) -> None:
    """Refuse ids the model cannot use and duplicates."""
    seen: set[str] = set()
    problems: list[str] = []
    for item in requirements:
        if not ID_PATTERN.match(item.id) or len(item.id) > MAX_ID:
            problems.append(f"{item.id!r} is not a usable requirement id")
        elif item.id in seen:
            problems.append(f"{item.id!r} appears twice")
        if not item.text:
            problems.append(f"{item.id!r} has no text")
        seen.add(item.id)
    if problems:
        message = "cannot import: " + "; ".join(problems[:10])
        raise ResultsError(message)


def content_hash(requirements: dict[str, Any]) -> str:
    """Return the hash of the `requirements` section as it is written in the snapshot."""
    canonical = json.dumps(requirements, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot(
    path: Path, tool: str, requirements: list[reqif.Requirement], baseline: str
) -> dict[str, Any]:
    """Return the snapshot document (a fragment) of one baseline."""
    section = {item.id: {"text": item.text} for item in requirements}
    document: dict[str, Any] = {}
    versioning.stamp(document)
    document["requirements_source"] = {
        "tool": tool,
        "baseline": baseline,
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "content_sha256": content_hash(section),
    }
    document["requirements"] = section
    return document
