"""Markdown and CSV building blocks of the lifecycle documents."""

from __future__ import annotations

import csv
import io
import re
from typing import TYPE_CHECKING

from wireforge import versioning

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def slug(text: str) -> str:
    """Return the anchor id of a requirement id, test id or heading."""
    return re.sub(r"[^a-z0-9-]", "-", text.lower())


def file_format() -> str:
    """Return the file format version as `major.minor`."""
    return f"{versioning.FORMAT_MAJOR}.{versioning.FORMAT_MINOR}"


def cell(text: object) -> str:
    """Return a table cell: one line, pipes escaped, `-` when empty."""
    return " ".join(str(text).split()).replace("|", "\\|") or "-"


def table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    """Return the lines of a Markdown table."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(item) for item in row) + " |" for row in rows]
    return lines


def csv_text(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Return CSV text with Unix line ends."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue()


def anchor(ident: str) -> str:
    """Return an HTML anchor for an id."""
    return f'<a id="{slug(ident)}"></a>'


def link(ident: str, file: str = "requirements.md") -> str:
    """Return a Markdown link to an id in a document."""
    return f"[{ident}]({file}#{slug(ident)})"


def links(items: Iterable[str], file: str = "requirements.md") -> str:
    """Return links to several ids, `-` for none."""
    return ", ".join(link(item, file) for item in items) or "-"


def preamble(
    title: str, name: str, version: str | None, content_hash: str, files: int
) -> list[str]:
    """Return the heading and baseline table every document starts with."""
    rows = [
        ("Model hash", f"`{content_hash}`"),
        ("Generator", f"WireForge {versioning.TOOL_VERSION} (file format {file_format()})"),
        ("Model files", f"{files} (see environment.md)"),
    ]
    heading = f"# {title} — {name} {version or ''}".rstrip()
    return [heading, "", *table(("Item", "Value"), rows), ""]
