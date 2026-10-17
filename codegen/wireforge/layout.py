"""Layout analyzer: wire layout, sizes and buffer budget of messages and interfaces, per profile.

For each profile (or once for a project without profiles) the analyzer reports

- per message: minimum and maximum encoded size and one row per field with its byte offset
  range, size range, bit position inside its bit group (the effective bit map of the message's
  bit order), occurrence, range, default and documentation;
- per interface: header and trailer layout, the message ids, payload and frame sizes, and the RAM
  the generated link allocates (receive ring, receive frame and transmit frame buffers).

The sizes follow the encoding rules of the reference codec and equal the `min_wire_size()`,
`max_wire_size()`, `kMaxFrameSize` and `kRxBufferBytes` values of the generated code. Reports
are exported as Markdown (an ICD document), CSV (one row per field) or JSON.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wireforge import expr
from wireforge.expr import ExprError
from wireforge.loader import load_project
from wireforge.model import BUILTIN_BITS, Field, Interface, Project, Struct, Value
from wireforge.validate import Validator, Variant

if TYPE_CHECKING:
    from collections.abc import Iterator

BYTE_BITS = 8


class LayoutError(Exception):
    """The layout cannot be computed (an expression has no value in a profile)."""


@dataclass(frozen=True)
class FieldLayout:
    """One field of a message, header or trailer.

    Offsets and sizes are in bytes from the start of the message (or header/trailer). Fields
    after variable-size fields have an offset range. Fields of array elements are reported for
    the first element (`path` contains `[]`). Bit-field members carry `bit_high`/`bit_low`, the
    bit positions inside the group container (bit 0 is the least significant bit), and
    `group_size`, the container size in bytes; their offset is the container's offset.
    """

    path: str
    type: str
    offset_min: int
    offset_max: int
    size_min: int
    size_max: int
    bit_high: int | None = None
    bit_low: int | None = None
    group_size: int | None = None
    occurs: str = ""
    range: str = ""
    default: str = ""
    role: str = ""
    condition: str = ""
    units: str = ""
    description: str = ""


@dataclass(frozen=True)
class MessageLayout:
    """Layout of one message."""

    name: str
    id: int | None
    endian: str
    bit_order: str
    size_min: int
    size_max: int
    fields: tuple[FieldLayout, ...]
    description: str = ""


@dataclass(frozen=True)
class InterfaceMessage:
    """A message of an interface: effective id, payload and frame sizes."""

    name: str
    id: int | None
    payload_min: int
    payload_max: int
    frame_min: int
    frame_max: int


@dataclass(frozen=True)
class InterfaceLayout:
    """Layout and buffer budget of one interface."""

    name: str
    version: str
    direction: str
    framing: str
    endian: str
    header_size: int
    trailer_size: int
    header: tuple[FieldLayout, ...]
    trailer: tuple[FieldLayout, ...]
    messages: tuple[InterfaceMessage, ...]
    max_payload: int
    max_frame: int
    rx_buffer: int
    tx_buffer: int
    link_ram: int


@dataclass(frozen=True)
class ProfileLayout:
    """Everything of one profile (`profile` is None without profiles)."""

    profile: str | None
    defines: dict[str, int]
    messages: tuple[MessageLayout, ...]
    interfaces: tuple[InterfaceLayout, ...]
    link_ram: int = 0


@dataclass(frozen=True)
class LayoutReport:
    """Layouts of all profiles of a project."""

    project: str
    version: str | None
    profiles: tuple[ProfileLayout, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the report as plain JSON-compatible data."""
        return asdict(self)


@dataclass
class _Cursor:
    """Running offset range while walking a struct."""

    low: int = 0
    high: int = 0
    rows: list[FieldLayout] = field(default_factory=list)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_text(item) for item in value) + "]"
    return str(value)


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


class _Analyzer:
    """Computes the layouts of one profile."""

    def __init__(self, project: Project, validator: Validator, variant: Variant) -> None:
        self.project = project
        self.validator = validator
        self.variant = variant
        self.order = "msb_first"  # bit order of the struct being walked

    # --- values ---------------------------------------------------------------------------------

    def static(self, raw: Value, what: str) -> int:
        value = self.validator.static_value(raw, self.variant)
        if value is None:
            message = f"{what}: cannot evaluate {raw!r}{self.variant.label()}"
            raise LayoutError(message)
        return value

    def bound(self, raw: Value | None) -> str:
        if raw is None:
            return ""
        if isinstance(raw, (int, float)):
            return _text(raw)
        value = self.validator.static_value(raw, self.variant)
        return raw if value is None else f"{raw} = {value}"

    def present(self, item: Field) -> bool:
        return self.validator.present(item, self.variant)

    def active(self, fields: tuple[Field, ...]) -> list[Field]:
        return [item for item in fields if self.present(item)]

    def holds(self, condition: str | None) -> bool:
        if condition is None:
            return True
        try:
            return expr.evaluate(expr.parse_condition(condition), self.variant.scope) != 0
        except ExprError:
            return True

    # --- sizes ----------------------------------------------------------------------------------

    def pure_bits(self, type_name: str) -> Struct | None:
        item = self.project.types.get(type_name)
        if item is None:
            return None
        active = self.active(item.fields)
        return item if active and all(inner.bits is not None for inner in active) else None

    def group_bits(self, item: Field) -> int | None:
        """Bits the field takes inside a bit group, or None when it is not a group member."""
        if item.occurs is not None:
            return None
        if item.bits is not None:
            return item.bits
        inner = self.pure_bits(item.type)
        if inner is None:
            return None
        return sum(member.bits or 0 for member in self.active(inner.fields))

    def element_size(self, item: Field) -> tuple[int, int]:
        inner = self.project.types.get(item.type)
        if inner is not None:
            return self.struct_size(inner.fields)
        base = self.project.enums[item.type].type if item.type in self.project.enums else item.type
        size = BUILTIN_BITS[base] // BYTE_BITS
        return size, size

    def counts(self, item: Field) -> tuple[int, int]:
        occurs = item.occurs
        if occurs is None:
            return 1, 1
        capacity = self.static(occurs.max, item.name)
        dynamic = occurs.count is not None or occurs.fill
        if not dynamic:
            return capacity, capacity
        low = self.static(occurs.min, item.name) if occurs.min is not None else 0
        return low, capacity

    def field_size(self, item: Field) -> tuple[int, int]:
        element = self.element_size(item)
        low, high = self.counts(item)
        return element[0] * low, element[1] * high

    def struct_size(self, fields: tuple[Field, ...]) -> tuple[int, int]:
        cursor = _Cursor()
        self.walk(fields, "", cursor, rows=False)
        return cursor.low, cursor.high

    # --- rows -----------------------------------------------------------------------------------

    def occurs_text(self, item: Field) -> str:
        occurs = item.occurs
        if occurs is None:
            return ""
        low, high = self.counts(item)
        if occurs.fill:
            return f"fill, {low}..{high}"
        if occurs.count is None:
            return str(high)
        return f"{low}..{high}, count {occurs.count}"

    def row(
        self, item: Field, path: str, offsets: tuple[int, int], size: tuple[int, int]
    ) -> FieldLayout:
        value = item.const if item.const is not None else item.default
        lower, upper = self.bound(item.min), self.bound(item.max)
        span = f"{lower}..{upper}" if lower or upper else ""
        role = item.role or ""
        if item.const is not None:
            role = role or "const"
        if item.reserved:
            role = "reserved"
        if item.checksum is not None:
            role = f"checksum {item.checksum.use}"
        return FieldLayout(
            path=path,
            type=item.type,
            offset_min=offsets[0],
            offset_max=offsets[1],
            size_min=size[0],
            size_max=size[1],
            occurs=self.occurs_text(item),
            range=span,
            default=_text(value),
            role=role,
            condition=item.condition or "",
            units=item.units or "",
            description=_one_line(item.description),
        )

    def walk(self, fields: tuple[Field, ...], prefix: str, cursor: _Cursor, *, rows: bool) -> None:
        group: list[Field] = []
        for item in [*self.active(fields), None]:
            bits = None if item is None else self.group_bits(item)
            if item is not None and bits is not None:
                group.append(item)
                continue
            if group:
                self.close_group(group, prefix, cursor, rows=rows)
                group = []
            if item is not None:
                self.place(item, prefix, cursor, rows=rows)

    def place(self, item: Field, prefix: str, cursor: _Cursor, *, rows: bool) -> None:
        path = f"{prefix}{item.name}"
        size = self.field_size(item)
        start = (cursor.low, cursor.high)
        if rows:
            cursor.rows.append(self.row(item, path, start, size))
        inner = self.project.types.get(item.type)
        if rows and inner is not None:
            element = _Cursor(start[0], start[1])
            suffix = "[]." if item.occurs is not None else "."
            self.walk(inner.fields, f"{path}{suffix}", element, rows=True)
            cursor.rows += element.rows
        cursor.low += size[0]
        cursor.high += size[1]

    def leaves(self, group: list[Field], prefix: str) -> Iterator[tuple[Field, str]]:
        for item in group:
            inner = self.pure_bits(item.type) if item.bits is None else None
            if inner is None:
                yield item, f"{prefix}{item.name}"
            else:
                for member in self.active(inner.fields):
                    yield member, f"{prefix}{item.name}.{member.name}"

    def close_group(self, group: list[Field], prefix: str, cursor: _Cursor, *, rows: bool) -> None:
        total = sum(self.group_bits(item) or 0 for item in group)
        size = (total + BYTE_BITS - 1) // BYTE_BITS
        if rows:
            order = self.order
            used = 0
            for leaf, path in self.leaves(group, prefix):
                width = leaf.bits or 0
                low = used if order == "lsb_first" else total - used - width
                used += width
                base = self.row(leaf, path, (cursor.low, cursor.high), (size, size))
                cursor.rows.append(
                    FieldLayout(
                        **{
                            **asdict(base),
                            "bit_high": low + width - 1,
                            "bit_low": low,
                            "group_size": size,
                        }
                    )
                )
        cursor.low += size
        cursor.high += size

    # --- messages and interfaces ----------------------------------------------------------------

    def message(self, item: Struct) -> MessageLayout:
        info = item.message
        defaults = self.project.defaults
        endian = (info.endian if info else None) or str(defaults.get("endian") or "big")
        self.order = (info.bit_order if info else None) or str(
            defaults.get("bit_order") or "msb_first"
        )
        cursor = _Cursor()
        self.walk(item.fields, "", cursor, rows=True)
        return MessageLayout(
            name=item.name,
            id=info.id if info else None,
            endian=endian,
            bit_order=self.order,
            size_min=cursor.low,
            size_max=cursor.high,
            fields=tuple(cursor.rows),
            description=_one_line(item.description),
        )

    def part(self, fields: tuple[Field, ...]) -> tuple[int, tuple[FieldLayout, ...]]:
        cursor = _Cursor()
        self.walk(fields, "", cursor, rows=True)
        return cursor.high, tuple(cursor.rows)

    def interface(self, interface: Interface) -> InterfaceLayout:
        defaults = self.project.defaults
        endian = interface.endian or str(defaults.get("endian") or "big")
        self.order = interface.bit_order or str(defaults.get("bit_order") or "msb_first")
        header_size, header = self.part(interface.header or ())
        trailer_size, trailer = self.part(interface.trailer)
        overhead = header_size + trailer_size
        messages = []
        for entry in interface.messages:
            message = self.project.messages[entry.name]
            if not self.holds(entry.condition) or not self.holds(message.condition):
                continue
            low, high = self.struct_size(message.fields)
            info = message.message
            identifier = entry.id if entry.id is not None else (info.id if info else None)
            messages.append(
                InterfaceMessage(entry.name, identifier, low, high, low + overhead, high + overhead)
            )
        max_payload = max((item.payload_max for item in messages), default=0)
        framing = interface.framing
        limit = framing.frame_size if framing.kind == "fixed" else framing.max_frame_size
        max_frame = overhead + max_payload if limit is None else self.static(limit, interface.name)
        buffers = interface.buffers
        rx = (
            self.static(buffers["rx_bytes"], "rx_bytes") if "rx_bytes" in buffers else 2 * max_frame
        )
        tx = self.static(buffers["tx_bytes"], "tx_bytes") if "tx_bytes" in buffers else max_frame
        return InterfaceLayout(
            name=interface.name,
            version=interface.version,
            direction=interface.direction,
            framing=framing.kind,
            endian=endian,
            header_size=header_size,
            trailer_size=trailer_size,
            header=header,
            trailer=trailer,
            messages=tuple(messages),
            max_payload=max_payload,
            max_frame=max_frame,
            rx_buffer=rx,
            tx_buffer=tx,
            link_ram=rx + 2 * max_frame,
        )

    def profile(self, name: str | None) -> ProfileLayout:
        messages = tuple(
            self.message(item)
            for item in self.project.messages.values()
            if self.holds(item.condition)
        )
        interfaces = tuple(
            self.interface(self.project.interfaces[key]) for key in sorted(self.project.interfaces)
        )
        defines = {key: int(value) for key, value in self.variant.scope.values.items()}
        return ProfileLayout(
            profile=name,
            defines=dict(sorted(defines.items())),
            messages=messages,
            interfaces=interfaces,
            link_ram=sum(item.link_ram for item in interfaces),
        )


def analyze(project: Project, profile: str | None = None) -> LayoutReport:
    """Layout report of a valid project (all profiles, or only `profile`)."""
    validator = Validator(project)
    variants = {variant.name: variant for variant in validator.variants}
    names: list[str | None] = [*project.profiles] or [None]
    if profile is not None:
        if profile not in project.profiles:
            message = f"unknown profile {profile!r}; choose one of {sorted(project.profiles)}"
            raise LayoutError(message)
        names = [profile]
    profiles = tuple(
        _Analyzer(project, validator, variants[name or ""]).profile(name) for name in names
    )
    return LayoutReport(project.name, project.version, profiles)


# --- exports ------------------------------------------------------------------------------------

CSV_COLUMNS = (
    "profile",
    "owner",
    "path",
    "type",
    "offset_min",
    "offset_max",
    "size_min",
    "size_max",
    "bit_high",
    "bit_low",
    "group_size",
    "occurs",
    "range",
    "default",
    "role",
    "condition",
    "units",
    "description",
)


def _csv_rows(report: LayoutReport) -> Iterator[dict[str, object]]:
    for profile in report.profiles:
        owners: list[tuple[str, tuple[FieldLayout, ...]]] = [
            (message.name, message.fields) for message in profile.messages
        ]
        for interface in profile.interfaces:
            owners.append((f"{interface.name}/header", interface.header))
            owners.append((f"{interface.name}/trailer", interface.trailer))
        for owner, rows in owners:
            for row in rows:
                yield {"profile": profile.profile or "", "owner": owner, **asdict(row)}


def to_csv(report: LayoutReport) -> str:
    """One row per field of every message, header and trailer, per profile."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in _csv_rows(report):
        writer.writerow({key: "" if value is None else value for key, value in row.items()})
    return out.getvalue()


def to_json(report: LayoutReport) -> str:
    """Return the report as indented JSON."""
    return json.dumps(report.to_dict(), indent=2) + "\n"


def _cell(text: object) -> str:
    return _text(text).replace("|", "\\|")


def _offset(row: FieldLayout) -> str:
    if row.offset_min == row.offset_max:
        return str(row.offset_min)
    return f"{row.offset_min}..{row.offset_max}"


def _size(low: int, high: int) -> str:
    return str(low) if low == high else f"{low}..{high}"


def _bits(row: FieldLayout) -> str:
    if row.bit_high is None or row.bit_low is None:
        return ""
    if row.bit_high == row.bit_low:
        return str(row.bit_low)
    return f"{row.bit_high}..{row.bit_low}"


FIELD_HEADER = (
    (
        "| Field | Type | Offset | Size | Bits | Occurs | Range | Default | Role | If | Units "
        "| Description |"
    ),
    "|---|---|---|---|---|---|---|---|---|---|---|---|",
)


def _field_table(rows: tuple[FieldLayout, ...]) -> list[str]:
    if not rows:
        return ["(no fields)", ""]
    lines = list(FIELD_HEADER)
    for row in rows:
        cells = (
            row.path,
            row.type,
            _offset(row),
            _size(row.size_min, row.size_max),
            _bits(row),
            row.occurs,
            row.range,
            row.default,
            row.role,
            row.condition,
            row.units,
            row.description,
        )
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    lines.append("")
    return lines


def _interface_section(interface: InterfaceLayout) -> list[str]:
    lines = [
        f"### Interface {interface.name} {interface.version} ({interface.direction})",
        "",
        (
            f"Framing `{interface.framing}`, {interface.endian} endian; header "
            f"{interface.header_size} bytes, trailer {interface.trailer_size} bytes; largest "
            f"payload {interface.max_payload} bytes, frame buffer {interface.max_frame} bytes."
        ),
        "",
        (
            f"Link RAM: receive ring {interface.rx_buffer} + receive frame {interface.max_frame} + "
            f"transmit frame {interface.max_frame} = **{interface.link_ram} bytes**."
        ),
        "",
        "| Message | Id | Payload | Frame |",
        "|---|---|---|---|",
    ]
    for message in interface.messages:
        identifier = "" if message.id is None else f"0x{message.id:04X}"
        payload = _size(message.payload_min, message.payload_max)
        frame = _size(message.frame_min, message.frame_max)
        lines.append(f"| {message.name} | {identifier} | {payload} | {frame} |")
    lines.append("")
    if interface.header:
        lines += ["Header:", "", *_field_table(interface.header)]
    if interface.trailer:
        lines += ["Trailer:", "", *_field_table(interface.trailer)]
    return lines


def _message_section(message: MessageLayout) -> list[str]:
    identifier = "" if message.id is None else f", id 0x{message.id:04X}"
    lines = [f"### Message {message.name}", ""]
    if message.description:
        lines += [message.description, ""]
    lines += [
        (
            f"{_size(message.size_min, message.size_max)} bytes{identifier}, {message.endian} "
            f"endian, bit order {message.bit_order}."
        ),
        "",
        *_field_table(message.fields),
    ]
    return lines


def to_markdown(report: LayoutReport) -> str:
    """ICD document: interfaces, messages and field tables per profile."""
    version = f" {report.version}" if report.version else ""
    lines = [f"# ICD {report.project}{version}", ""]
    lines += [
        (
            "Offsets and sizes in bytes; bits are positions inside the bit-group container "
            "(bit 0 = least significant bit). Array element fields are shown for the first "
            "element."
        ),
        "",
    ]
    for profile in report.profiles:
        title = f"Profile {profile.profile}" if profile.profile else "Layout"
        lines += [f"## {title}", ""]
        if profile.defines:
            defines = ", ".join(f"{key} = {value}" for key, value in profile.defines.items())
            lines += [f"Defines: {defines}.", ""]
        lines += [f"Total link RAM: **{profile.link_ram} bytes**.", ""]
        for interface in profile.interfaces:
            lines += _interface_section(interface)
        for message in profile.messages:
            lines += _message_section(message)
    return "\n".join(lines).rstrip("\n") + "\n"


EXPORTERS = {"markdown": to_markdown, "csv": to_csv, "json": to_json}


def main(argv: list[str] | None = None) -> int:
    """Command line: python -m wireforge.layout PROJECT [--profile P] [--format F] [-o FILE]."""
    parser = argparse.ArgumentParser(prog="python -m wireforge.layout")
    parser.add_argument("project", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--format", choices=sorted(EXPORTERS), default="markdown")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    result = load_project(args.project)
    issues = (
        result.issues if result.project is None else result.issues + Validator(result.project).run()
    )
    errors = [issue for issue in issues if issue.severity == "error"]
    if result.project is None or errors:
        for issue in errors:
            sys.stdout.write(f"{issue}\n")
        return 1
    try:
        text = EXPORTERS[args.format](analyze(result.project, args.profile))
    except LayoutError as error:
        sys.stdout.write(f"{error}\n")
        return 1
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
