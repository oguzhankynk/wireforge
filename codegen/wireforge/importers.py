"""Importers: ICD tables (CSV, Excel) and C headers to WireForge projects.

ICD tables have one row per field (docs/IMPORT.md):

    section,name,id,field,type,bits,occurs,count,min,max,default,units,description

- `section` is `message` (default) or `type`; consecutive rows with the same name form one entry,
  the `id` of a message comes from its first row;
- `occurs` is empty (one value), `N` (fixed array), `0..N` (counted array: `count` names the
  count field or holds an expression; `0..1` is an optional field) or `fill..N` (fills the
  payload);
- `min`, `max`, `default` are numbers or expressions; empty cells are left out.

C headers: `#define NAME <integer>` becomes a define, `enum` a WireForge enum (u8, or u16/u32 when
values need it), and every `struct` whose members are fixed-width integers (`uint8_t` ...
`int64_t`), `bool`, `char`, `float`, `double`, earlier structs or enums becomes a type: arrays
`T name[N]` are fixed arrays, bit-fields `T name : n` keep their width. Structs listed as messages
become messages instead, numbered from 1 in the order of the header. Pointers and unions are
reported with their line.
"""

from __future__ import annotations

import csv
import importlib
import io
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ruamel.yaml.scalarint import HexInt

from wireforge.issues import Issue

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

COLUMNS = (
    "section",
    "name",
    "id",
    "field",
    "type",
    "bits",
    "occurs",
    "count",
    "min",
    "max",
    "default",
    "units",
    "description",
)
NUMBER = re.compile(r"^[-+]?(0x[0-9A-Fa-f]+|\d+(\.\d+)?([eE][-+]?\d+)?)$")
C_TYPES = {
    "uint8_t": "u8",
    "uint16_t": "u16",
    "uint32_t": "u32",
    "uint64_t": "u64",
    "int8_t": "i8",
    "int16_t": "i16",
    "int32_t": "i32",
    "int64_t": "i64",
    "bool": "bool",
    "_Bool": "bool",
    "char": "char",
    "float": "f32",
    "double": "f64",
}
# `#define NAME` (include guards) has two words and is skipped; longer non-integer ones are errors.
DEFINE_WORDS = 2
U8_LIMIT = 0xFF
U16_LIMIT = 0xFFFF


class IcdImportError(Exception):
    """The source cannot be imported; `issues` locate the problems."""

    def __init__(self, issues: list[Issue]) -> None:
        """Store the problems of the source."""
        super().__init__("; ".join(str(issue) for issue in issues))
        self.issues = issues


@dataclass
class Project:
    """Options of the imported project."""

    name: str
    namespace: str | None = None
    version: str = "0.1.0"
    endian: str | None = None


@dataclass
class _Builder:
    """Collects the sections of the imported project."""

    source: str
    defines: dict[str, int] = field(default_factory=dict)
    enums: dict[str, dict[str, Any]] = field(default_factory=dict)
    types: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def problem(self, line: int | None, text: str) -> None:
        self.issues.append(Issue(self.source, line, "", text))

    def document(self, options: Project) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": 1,
            "project": options.name,
            "version": options.version,
        }
        if options.namespace:
            document["namespace"] = options.namespace
        if options.endian:
            document["defaults"] = {"endian": options.endian}
        sections = {
            "defines": self.defines,
            "enums": self.enums,
            "types": self.types,
            "messages": self.messages,
        }
        document.update({name: value for name, value in sections.items() if value})
        return document


def _value(text: str) -> int | float | str:
    """Return a number when the cell is numeric, otherwise the text (an expression or a name)."""
    cell = text.strip()
    if not NUMBER.match(cell):
        return cell
    if cell.lower().lstrip("+-").startswith("0x"):
        digits = cell.lower().lstrip("+-")[2:]
        hex_value: int = HexInt(int(cell, 16), width=len(digits))  # keeps the hex spelling
        return hex_value
    number = float(cell)
    return (
        int(number)
        if number.is_integer() and "." not in cell and "e" not in cell.lower()
        else number
    )


# --- ICD tables ---------------------------------------------------------------------------------


def _occurs(row: dict[str, str]) -> dict[str, Any] | None:
    occurs = row.get("occurs", "").strip()
    if occurs == "":
        return None
    count = row.get("count", "").strip()
    low, separator, high = occurs.partition("..")
    if not separator:
        return {"max": _value(occurs)}
    if low.strip() == "fill":
        return {"max": _value(high), "fill": True}
    result: dict[str, Any] = {"max": _value(high)}
    if low.strip() not in {"", "0"}:
        result["min"] = _value(low)
    if count:
        result["count"] = _value(count)
    return result


def _row_field(row: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {"name": row["field"].strip(), "type": row["type"].strip()}
    if row.get("bits", "").strip():
        result["bits"] = int(row["bits"])
    for key in ("min", "max", "default"):
        if row.get(key, "").strip():
            result[key] = _value(row[key])
    if row.get("units", "").strip():
        result["units"] = row["units"].strip()
    occurs = _occurs(row)
    if occurs is not None:
        result["occurs"] = occurs
    if row.get("description", "").strip():
        result["description"] = " ".join(row["description"].split())
    return result


def _table(rows: Iterable[dict[str, str]], builder: _Builder) -> None:
    for line, row in enumerate(rows, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue
        name = (row.get("name") or "").strip()
        if not name or not (row.get("field") or "").strip() or not (row.get("type") or "").strip():
            builder.problem(line, "every row needs name, field and type")
            continue
        section = (row.get("section") or "message").strip().lower()
        if section not in {"message", "type"}:
            builder.problem(line, f"section must be message or type, not {section!r}")
            continue
        entries = builder.messages if section == "message" else builder.types
        entry = entries.setdefault(name, {"fields": []})
        if section == "message" and "id" not in entry and (row.get("id") or "").strip():
            entry["id"] = _value(row["id"])
        try:
            entry["fields"].append(_row_field({key: value or "" for key, value in row.items()}))
        except ValueError as error:
            builder.problem(line, str(error))


def _header_row(header: Sequence[str], source: str) -> list[str]:
    names = [(cell or "").strip().lower() for cell in header]
    missing = [column for column in ("name", "field", "type") if column not in names]
    if missing:
        raise IcdImportError([Issue(source, 1, "", f"missing columns: {', '.join(missing)}")])
    unknown = [name for name in names if name and name not in COLUMNS]
    if unknown:
        raise IcdImportError([Issue(source, 1, "", f"unknown columns: {', '.join(unknown)}")])
    return names


def from_csv(text: str, options: Project, source: str = "icd.csv") -> dict[str, Any]:
    """Return the project document of an ICD table in CSV form."""
    reader = csv.reader(io.StringIO(text))
    header = _header_row(next(reader, []), source)
    builder = _Builder(source)
    _table((dict(zip(header, row, strict=False)) for row in reader), builder)
    if builder.issues:
        raise IcdImportError(builder.issues)
    return builder.document(options)


def _cell(value: object) -> str:
    """Text of a spreadsheet cell; whole numbers stored as floats lose the fraction."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def from_xlsx(path: Path, options: Project) -> dict[str, Any]:
    """Return the project document of the ICD table in the first sheet of a workbook."""
    try:
        openpyxl = importlib.import_module("openpyxl")  # optional: pip install 'wireforge[import]'
    except ImportError as error:
        issue = Issue(
            str(path), None, "", "Excel import needs openpyxl (pip install 'wireforge[import]')"
        )
        raise IcdImportError([issue]) from error
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    rows = [[_cell(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
    workbook.close()
    if not rows:
        raise IcdImportError([Issue(str(path), None, "", "the first sheet is empty")])
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return from_csv(out.getvalue(), options, str(path))


# --- C headers ----------------------------------------------------------------------------------

COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
DEFINE = re.compile(
    r"^\s*#\s*define\s+([A-Za-z_]\w*)\s+\(?\s*(-?(?:0x[0-9A-Fa-f]+|\d+))[uUlL]*\s*\)?\s*$"
)
BLOCK = re.compile(
    r"typedef\s+(?P<kind>struct|enum)\s*\w*\s*\{(?P<body>[^{}]*)\}\s*(?P<alias>\w+)\s*;"
    r"|\b(?P<kind2>struct|enum)\s+(?P<name>\w+)\s*\{(?P<body2>[^{}]*)\}\s*;",
)
MEMBER = re.compile(
    r"^(?:const\s+)?(?:struct\s+|enum\s+)?(?P<type>\w+)\s+(?P<name>\w+)"
    r"\s*(?:\[(?P<count>[^\]]+)\])?\s*(?::\s*(?P<bits>\d+))?$"
)


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _blank_comments(text: str) -> str:
    """Return the text without comments (line numbers stay)."""
    return COMMENT.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), text)


def _enum(body: str, line: int, builder: _Builder) -> dict[str, Any] | None:
    values: dict[str, int] = {}
    current = -1
    for item in (part.strip() for part in body.split(",")):
        if not item:
            continue
        name, _, raw = (part.strip() for part in item.partition("="))
        if raw:
            number = _value(raw)
            if not isinstance(number, int):
                builder.problem(line, f"enum value {raw!r} is not an integer")
                return None
            current = number
        else:
            current += 1
        values[name] = current
    high, low = max(values.values(), default=0), min(values.values(), default=0)
    base = (
        "i32" if low < 0 else ("u8" if high <= U8_LIMIT else "u16" if high <= U16_LIMIT else "u32")
    )
    return {"type": base, "values": values}


def _member(statement: str, line: int, builder: _Builder, known: set[str]) -> dict[str, Any] | None:
    text = " ".join(statement.split())
    if "*" in text or "union" in text.split():
        builder.problem(line, f"pointers and unions are not wire data: {text!r}")
        return None
    match = MEMBER.match(text)
    if match is None:
        builder.problem(line, f"cannot read member {text!r}")
        return None
    c_type = match["type"]
    wire = C_TYPES.get(c_type, c_type if c_type in known else None)
    if wire is None:
        builder.problem(line, f"unknown type {c_type!r} (use fixed-width types)")
        return None
    member: dict[str, Any] = {"name": match["name"], "type": wire}
    if match["bits"]:
        member["bits"] = int(match["bits"])
    if match["count"]:
        member["occurs"] = {"max": _value(match["count"])}
    return member


def _struct(body: str, line: int, builder: _Builder, known: set[str]) -> dict[str, Any] | None:
    fields = []
    offset = 0
    for statement in body.split(";"):
        if statement.strip():
            start = offset + len(statement) - len(statement.lstrip())
            member = _member(statement, line + body.count("\n", 0, start), builder, known)
            if member is None:
                return None
            fields.append(member)
        offset += len(statement) + 1
    return {"fields": fields}


def _defines(clean: str, builder: _Builder) -> None:
    for number, text in enumerate(clean.splitlines(), start=1):
        match = DEFINE.match(text)
        if match:
            builder.defines[match[1]] = int(match[2], 0)
        elif text.strip().startswith("#define") and len(text.split()) > DEFINE_WORDS:
            builder.problem(number, f"only integer defines are imported: {text.strip()!r}")


def _blocks(clean: str, builder: _Builder) -> dict[str, dict[str, Any]]:
    """Read enums into `builder` and return the structs in header order."""
    structs: dict[str, dict[str, Any]] = {}
    for match in BLOCK.finditer(clean):
        kind = match["kind"] or match["kind2"]
        name = match["alias"] or match["name"]
        body = match["body"] if match["body"] is not None else match["body2"]
        line = _line_of(clean, match.start())
        if kind == "enum":
            enum = _enum(body, line, builder)
            if enum is not None:
                builder.enums[name] = enum
            continue
        struct = _struct(body, line, builder, set(builder.enums) | set(structs))
        if struct is not None:
            structs[name] = struct
    return structs


def _assign(
    structs: dict[str, dict[str, Any]], messages: Sequence[str] | None, builder: _Builder
) -> None:
    used = {member["type"] for struct in structs.values() for member in struct["fields"]}
    chosen = (
        list(messages) if messages is not None else [name for name in structs if name not in used]
    )
    for name in chosen:
        if name not in structs:
            builder.problem(None, f"no struct {name!r} in the header")
    next_id = 1
    for name, struct in structs.items():
        if name in chosen:
            builder.messages[name] = {"id": next_id, **struct}
            next_id += 1
        else:
            builder.types[name] = struct


def from_c_header(
    text: str, options: Project, messages: Sequence[str] | None = None, source: str = "icd.h"
) -> dict[str, Any]:
    """Return the project document of the defines, enums and structs of a C header.

    Structs named in `messages` become messages (ids 1, 2, ... in header order); without
    `messages` every struct that no other struct uses becomes a message.
    """
    clean = _blank_comments(text)
    builder = _Builder(source)
    _defines(clean, builder)
    _assign(_blocks(clean, builder), messages, builder)
    if builder.issues:
        raise IcdImportError(builder.issues)
    return builder.document(options)
