"""Wireshark dissector generator: one self-contained Lua plugin per project.

The plugin registers one protocol per interface. Its upper part is a data table describing every
profile's wire layout (header, trailer, framing, messages with fields fully expanded, bit
positions, ranges and checksums); its lower part is a fixed interpreter
(`wireshark_runtime.lua`) that walks those tables the same way the reference codec walks the
model. Profiles become a protocol preference, so one file serves every profile.

Each field gets a display filter name `<protocol>.<Message>.<path>` (header and trailer fields
`<protocol>.header.<name>` and `<protocol>.trailer.<name>`). Decode problems are reported as
expert info whose text starts with the runtime status name (`[ChecksumMismatch] ...`).

The plugin uses only the Lua API of Wireshark 3.6 and later and runs on Lua 5.2 to 5.4.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wireforge import bundle, expr, versioning
from wireforge.codec import CRC32, ReferenceCodec, Settings
from wireforge.expr import ExprError
from wireforge.issues import Issue
from wireforge.loader import load_project
from wireforge.model import (
    BUILTIN_BITS,
    FLOAT_TYPES,
    SIGNED_TYPES,
    ChecksumUse,
    Field,
    Interface,
    Occurs,
    Project,
    Struct,
    Value,
)
from wireforge.validate import Validator, Variant

BYTE_BITS = 8
NUMBER_BYTES = 4
WIDE_BYTES = 8
WORD_BITS = 32
PRINTABLE = range(0x20, 0x7F)
RUNTIME = Path(__file__).with_name("wireshark_runtime.lua")
PROTOCOL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
LINE_WIDTH = 100
INDENT = "  "

LuaValue = Any


class WiresharkError(Exception):
    """The project cannot be expressed as a dissector (the message says why)."""


@dataclass(frozen=True)
class FieldSpec:
    """One registered ProtoField."""

    abbr: str
    label: str
    ftype: str
    base: str = "NONE"
    mask: int | None = None
    enum: str | None = None
    description: str = ""

    def lua(self) -> dict[str, LuaValue]:
        """Return the table the runtime turns into a ProtoField."""
        spec: dict[str, LuaValue] = {"abbr": self.abbr, "label": self.label, "type": self.ftype}
        if self.base != "NONE":
            spec["base"] = self.base
        if self.mask is not None:
            spec["mask"] = self.mask
        if self.enum is not None:
            spec["enum"] = self.enum
        if self.description:
            spec["description"] = self.description
        return spec


@dataclass
class Registry:
    """ProtoFields of one protocol, shared by every profile."""

    protocol: str
    specs: dict[str, FieldSpec] = field(default_factory=dict)

    def add(self, spec: FieldSpec) -> str:
        """Register `spec` (once) and return its abbreviation."""
        known = self.specs.get(spec.abbr)
        if known is None:
            self.specs[spec.abbr] = spec
        elif known != spec:
            message = (
                f"two different fields would both be named {spec.abbr} (a name clash, or a field "
                "that differs between profiles)"
            )
            raise WiresharkError(message)
        return spec.abbr


def protocol_name(project: Project, interface: Interface) -> str:
    """Return the protocol (display filter) name of an interface: lower case, `_` separated."""
    text = f"{project.name}_{interface.name}".lower()
    text = re.sub(r"[^a-z0-9_]", "_", text)
    return text if PROTOCOL_NAME.match(text) else f"wf_{text}"


def _int_type(type_name: str, width: int) -> str:
    prefix = "int" if type_name in SIGNED_TYPES else "uint"
    return f"{prefix}{width * BYTE_BITS}"


def _pair(value: int, *, signed: bool) -> dict[str, LuaValue]:
    """Split a 64-bit value into two 32-bit halves (the runtime builds an Int64 or UInt64 of it)."""
    bits = value % (1 << 64)
    pair: dict[str, LuaValue] = {"low": bits & 0xFFFFFFFF, "high": bits >> WORD_BITS}
    if signed:
        pair["signed"] = True
    return pair


class _Builder:
    """Builds the layout tables of one profile."""

    def __init__(self, project: Project, validator: Validator, variant: Variant) -> None:
        self.project = project
        self.validator = validator
        self.variant = variant
        self.codec = ReferenceCodec(project, variant.name or None)

    # --- model helpers --------------------------------------------------------------------------

    def static(self, raw: Value, what: str) -> int:
        value = self.validator.static_value(raw, self.variant)
        if value is None:
            message = f"{what}: cannot evaluate {raw!r}{self.variant.label()}"
            raise WiresharkError(message)
        return value

    def holds(self, condition: str | None) -> bool:
        if condition is None:
            return True
        try:
            return expr.evaluate(expr.parse_condition(condition), self.variant.scope) != 0
        except ExprError:
            return True

    def active(self, fields: tuple[Field, ...]) -> tuple[Field, ...]:
        return tuple(item for item in fields if self.validator.present(item, self.variant))

    def pure_bits(self, type_name: str) -> Struct | None:
        item = self.project.types.get(type_name)
        if item is None:
            return None
        active = self.active(item.fields)
        return item if active and all(inner.bits is not None for inner in active) else None

    def in_group(self, item: Field) -> bool:
        if item.occurs is not None:
            return False
        return item.bits is not None or self.pure_bits(item.type) is not None

    def base_type(self, item: Field) -> str:
        enum = self.project.enums.get(item.type)
        return enum.type if enum is not None else item.type

    def scalar_width(self, item: Field) -> int:
        return BUILTIN_BITS[self.base_type(item)] // BYTE_BITS

    def fixed_size(self, fields: tuple[Field, ...], what: str) -> int:
        """Size of fields without variable-size arrays (headers, trailers, fill elements)."""
        total_bits = 0
        for item in self.active(fields):
            if self.in_group(item):
                inner = self.pure_bits(item.type) if item.bits is None else None
                members = self.active(inner.fields) if inner is not None else (item,)
                total_bits += sum(member.bits or 0 for member in members)
                continue
            occurs = item.occurs
            if occurs is not None and (occurs.count is not None or occurs.fill):
                message = f"{what}: {item.name} has a variable size"
                raise WiresharkError(message)
            count = 1 if occurs is None else self.static(occurs.max, item.name)
            inner_struct = self.project.types.get(item.type)
            element = (
                self.fixed_size(inner_struct.fields, what) * BYTE_BITS
                if inner_struct is not None
                else self.scalar_width(item) * BYTE_BITS
            )
            total_bits += count * element
        return total_bits // BYTE_BITS

    # --- expressions ----------------------------------------------------------------------------

    def expression(self, raw: Value, names: set[str]) -> LuaValue:
        """Count expression as a runtime tree; parts without field names are folded."""
        if isinstance(raw, float):
            message = f"count {raw!r} is not an integer"
            raise WiresharkError(message)
        return self.node(expr.as_expression(raw), names)

    def node(self, node: expr.Node, names: set[str]) -> LuaValue:
        if not expr.referenced_names(node) & names:
            try:
                return expr.evaluate(node, self.variant.scope)
            except ExprError as error:
                message = f"cannot evaluate a count expression{self.variant.label()}: {error}"
                raise WiresharkError(message) from error
        if isinstance(node, expr.Name):
            return ["field", node.name]
        if isinstance(node, expr.Unary):
            return ["unary", node.op, self.node(node.operand, names)]
        if isinstance(node, expr.Binary):
            return ["binary", node.op, self.node(node.left, names), self.node(node.right, names)]
        if isinstance(node, expr.Ternary):
            parts = (node.condition, node.then, node.otherwise)
            return ["if", *(self.node(part, names) for part in parts)]
        if isinstance(node, expr.Call):
            return ["call", node.function, *(self.node(arg, names) for arg in node.args)]
        message = f"unsupported count expression node {expr.describe(node)}"
        raise WiresharkError(message)

    # --- values ---------------------------------------------------------------------------------

    def bound(self, raw: Value | None) -> float | None:
        if raw is None:
            return None
        if isinstance(raw, float):
            return raw
        return self.validator.static_value(raw, self.variant)

    def number(self, value: float, *, big: bool, signed: bool) -> LuaValue:
        if big and isinstance(value, int):
            return _pair(value, signed=signed)
        return value

    def checks(self, item: Field, entry: dict[str, LuaValue], *, big: bool) -> None:
        """Add range, enum and constant checks of a scalar or bit-field."""
        signed = self.base_type(item) in SIGNED_TYPES
        low, high = self.codec.int_range(item) if item.type not in FLOAT_TYPES else (None, None)
        lower, upper = self.bound(item.min), self.bound(item.max)
        if lower is not None and (low is None or lower > low):
            entry["min"] = self.number(lower, big=big, signed=signed)
        if upper is not None and (high is None or upper < high):
            entry["max"] = self.number(upper, big=big, signed=signed)
        if item.type in self.project.enums:
            entry["enum"] = item.type
        if item.const is not None:
            const: object = item.const
            enum = self.project.enums.get(item.type)
            if enum is not None and isinstance(const, str):
                const = enum.values[const]
            if isinstance(const, bool):
                const = int(const)
            if isinstance(const, (int, float)):
                entry["const"] = self.number(const, big=big, signed=signed)
        if item.reserved:
            entry["reserved"] = True


class _Layout:
    """Expands the fields of one message, header or trailer into runtime tables."""

    def __init__(
        self, builder: _Builder, registry: Registry, prefix: str, settings: Settings
    ) -> None:
        self.builder = builder
        self.registry = registry
        self.prefix = prefix
        self.settings = settings

    def label(self, item: Field) -> str:
        return f"{item.name} [{item.units}]" if item.units else item.name

    def describe(self, item: Field) -> str:
        return " ".join((item.description or "").split())

    def base(self, item: Field) -> str:
        if item.type in self.builder.project.enums:
            return "DEC"
        hexadecimal = item.role in {"sync", "message_id"} or item.checksum is not None
        if hexadecimal or item.const is not None or item.reserved:
            return "HEX"
        return "DEC"

    def struct(self, fields: tuple[Field, ...], path: str) -> list[dict[str, LuaValue]]:
        builder = self.builder
        active = builder.active(fields)
        names = {item.name for item in active}
        result: list[dict[str, LuaValue]] = []
        group: list[Field] = []
        for item in [*active, None]:
            if item is not None and builder.in_group(item):
                group.append(item)
                continue
            if group:
                result.append(self.group(group, path))
                group = []
            if item is not None:
                result.append(self.field(item, path, names))
        return result

    def field(self, item: Field, path: str, names: set[str]) -> dict[str, LuaValue]:
        builder = self.builder
        abbr = f"{self.prefix}.{path}{item.name}"
        entry: dict[str, LuaValue] = {"name": item.name}
        if item.occurs is not None:
            entry["occurs"] = self.occurs(item, item.occurs, names)
        inner = builder.project.types.get(item.type)
        if inner is not None:
            entry["kind"] = "struct"
            spec = FieldSpec(abbr, item.name, "none", description=self.describe(item))
            entry["abbr"] = self.registry.add(spec)
            entry["fields"] = self.struct(inner.fields, f"{path}{item.name}.")
            return entry
        entry["kind"] = "scalar"
        entry["type"] = item.type
        width = builder.scalar_width(item)
        entry["width"] = width
        if item.type in FLOAT_TYPES:
            ftype = "float" if item.type == "f32" else "double"
        elif item.type == "bool":
            ftype = "bool"
        elif item.type == "char":
            ftype = "string"
        else:
            ftype = _int_type(builder.base_type(item), width)
            entry["signed"] = builder.base_type(item) in SIGNED_TYPES
        spec = FieldSpec(
            abbr,
            self.label(item),
            ftype,
            base=self.base(item) if ftype.startswith(("uint", "int")) else "NONE",
            enum=item.type if item.type in builder.project.enums and width < WIDE_BYTES else None,
            description=self.describe(item),
        )
        entry["abbr"] = self.registry.add(spec)
        builder.checks(item, entry, big=width == WIDE_BYTES)
        if item.checksum is not None:
            entry["checksum"] = self.checksum(item.checksum)
        return entry

    def occurs(self, item: Field, occurs: Occurs, names: set[str]) -> dict[str, LuaValue]:
        builder = self.builder
        capacity = builder.static(occurs.max, item.name)
        result: dict[str, LuaValue] = {"max": capacity}
        dynamic = occurs.count is not None or occurs.fill
        result["min"] = builder.static(occurs.min, item.name) if occurs.min is not None else 0
        if not dynamic:
            result["min"] = capacity
        if occurs.fill:
            result["fill"] = True
            inner = builder.project.types.get(item.type)
            result["element"] = (
                builder.fixed_size(inner.fields, item.name)
                if inner is not None
                else builder.scalar_width(item)
            )
        elif occurs.count is not None:
            result["count"] = builder.expression(occurs.count, names)
        if dynamic:
            result["dynamic"] = True
        return result

    def checksum(self, use: ChecksumUse) -> dict[str, LuaValue]:
        result: dict[str, LuaValue] = {"use": use.use}
        if use.scope is not None:
            result["scope"] = use.scope
        else:
            result["first"] = use.first
            result["last"] = use.last
        return result

    def leaves(self, group: list[Field], path: str) -> list[tuple[Field, str, str | None]]:
        """Group members as (leaf, abbreviation path, owning nested struct field name)."""
        result: list[tuple[Field, str, str | None]] = []
        for item in group:
            inner = self.builder.pure_bits(item.type) if item.bits is None else None
            if inner is None:
                result.append((item, f"{path}{item.name}", None))
            else:
                result.extend(
                    (member, f"{path}{item.name}.{member.name}", item.name)
                    for member in self.builder.active(inner.fields)
                )
        return result

    def group(self, group: list[Field], path: str) -> dict[str, LuaValue]:
        leaves = self.leaves(group, path)
        total = sum(leaf.bits or 0 for leaf, _, _ in leaves)
        width = total // BYTE_BITS
        wide = width > NUMBER_BYTES
        used = 0
        parts: list[dict[str, LuaValue]] = []
        owners: dict[str, dict[str, LuaValue]] = {}
        for leaf, leaf_path, owner in leaves:
            bits = leaf.bits or 0
            shift = used if self.settings.bit_order == "lsb_first" else total - used - bits
            used += bits
            entry = self.leaf(leaf, leaf_path, bits, shift, width)
            if owner is None:
                parts.append(entry)
                continue
            if owner not in owners:
                abbr = f"{self.prefix}.{path}{owner}"
                owner_field = next(item for item in group if item.name == owner)
                spec = FieldSpec(abbr, owner, "none", description=self.describe(owner_field))
                owners[owner] = {"name": owner, "abbr": self.registry.add(spec), "leaves": []}
                parts.append(owners[owner])
            owners[owner]["leaves"].append(entry)
        result: dict[str, LuaValue] = {"kind": "group", "width": width, "parts": parts}
        if wide:
            result["wide"] = True
        return result

    def leaf(
        self, leaf: Field, path: str, bits: int, shift: int, width: int
    ) -> dict[str, LuaValue]:
        builder = self.builder
        wide = width > NUMBER_BYTES
        signed = builder.base_type(leaf) in SIGNED_TYPES
        entry: dict[str, LuaValue] = {
            "name": leaf.name,
            "type": leaf.type,
            "bits": bits,
            "shift": shift,
        }
        if signed:
            entry["signed"] = True
        mask = None if wide else ((1 << bits) - 1) << shift
        if leaf.type == "bool":
            ftype = "bool"
            base = "NONE" if wide else str(width * BYTE_BITS)
        else:
            ftype = ("int" if signed else "uint") + ("64" if wide else str(width * BYTE_BITS))
            base = self.base(leaf)
        spec = FieldSpec(
            f"{self.prefix}.{path}",
            self.label(leaf),
            ftype,
            base=base,
            mask=mask,
            enum=leaf.type if leaf.type in builder.project.enums and not wide else None,
            description=self.describe(leaf),
        )
        entry["abbr"] = self.registry.add(spec)
        builder.checks(leaf, entry, big=wide)
        return entry


class _InterfaceBuilder:
    """Builds the protocol of one interface for every profile."""

    def __init__(self, project: Project, interface: Interface, validator: Validator) -> None:
        self.project = project
        self.interface = interface
        self.validator = validator
        self.registry = Registry(protocol_name(project, interface))

    def build(self, variants: list[Variant]) -> dict[str, LuaValue]:
        name = self.registry.protocol
        registry = self.registry
        registry.add(FieldSpec(f"{name}.header", "Header", "none"))
        registry.add(FieldSpec(f"{name}.trailer", "Trailer", "none"))
        registry.add(FieldSpec(f"{name}.payload", "Payload", "bytes"))
        registry.add(FieldSpec(f"{name}.skipped", "Skipped bytes", "bytes"))
        layouts = [self.layout(variant) for variant in variants]
        interface = self.interface
        version = f" {self.project.version}" if self.project.version else ""
        description = (
            f"{self.project.name}{version} {interface.name} {interface.version} (WireForge)"
        )
        return {
            "name": name,
            "interface": interface.name,
            "description": description,
            "profiles": [variant.name or "default" for variant in variants],
            "fields": [spec.lua() for spec in registry.specs.values()],
            "layouts": layouts,
        }

    def layout(self, variant: Variant) -> dict[str, LuaValue]:
        builder = _Builder(self.project, self.validator, variant)
        interface = self.interface
        codec = builder.codec
        settings = codec.interface_settings(interface)
        name = self.registry.protocol
        header_fields = builder.active(interface.header or ())
        trailer_fields = builder.active(interface.trailer)
        frame_layout = _Layout(builder, self.registry, f"{name}.header", settings)
        header = frame_layout.struct(header_fields, "")
        trailer = _Layout(builder, self.registry, f"{name}.trailer", settings).struct(
            trailer_fields, ""
        )
        framing = interface.framing
        result: dict[str, LuaValue] = {
            "framing": framing.kind,
            "header_size": builder.fixed_size(header_fields, f"{interface.name} header"),
            "trailer_size": builder.fixed_size(trailer_fields, f"{interface.name} trailer"),
            "little": settings.endian == "little",
            "check": settings.check_decode,
            "header": header,
            "trailer": trailer,
            "length_counts": framing.length_counts,
        }
        if settings.allow_trailing:
            result["allow_trailing"] = True
        if framing.resync and framing.kind != "none":
            result["resync"] = True
        if framing.kind == "fixed" and framing.frame_size is not None:
            result["frame_size"] = builder.static(framing.frame_size, "frame_size")
        if framing.max_frame_size is not None:
            result["max_frame_size"] = builder.static(framing.max_frame_size, "max_frame_size")
        self.roles(builder, header_fields, result)
        result["messages"] = self.messages(builder)
        return result

    def roles(
        self, builder: _Builder, header: tuple[Field, ...], result: dict[str, LuaValue]
    ) -> None:
        """Locate the sync, message id and length fields of the header."""
        offset = 0
        sync: list[dict[str, LuaValue]] = []
        little = result["little"]
        for item in header:
            size = builder.fixed_size((item,), f"{self.interface.name} header")
            if item.role == "sync" and isinstance(item.const, int):
                data = item.const.to_bytes(size, "little" if little else "big")
                sync.append({"offset": offset, "bytes": data})
            elif item.role == "message_id":
                result["id_field"] = item.name
            elif item.role == "payload_length":
                result["length_field"] = {"name": item.name, "offset": offset, "width": size}
            offset += size
        if sync:
            result["sync"] = sync

    def messages(self, builder: _Builder) -> list[dict[str, LuaValue]]:
        interface = self.interface
        name = self.registry.protocol
        codec = builder.codec
        ids = codec.message_ids(interface.name)
        result = []
        for entry in interface.messages:
            message = self.project.messages[entry.name]
            if not builder.holds(entry.condition) or not builder.holds(message.condition):
                continue
            settings = codec.message_settings(message.name, interface.name)
            prefix = f"{name}.{message.name}"
            description = " ".join((message.description or "").split())
            spec = FieldSpec(prefix, message.name, "none", description=description)
            fields = _Layout(builder, self.registry, prefix, settings).struct(message.fields, "")
            item: dict[str, LuaValue] = {
                "name": message.name,
                "abbr": self.registry.add(spec),
                "little": settings.endian == "little",
                "check": settings.check_decode,
                "fields": fields,
            }
            identifier = ids.get(message.name)
            if identifier is not None:
                item["id"] = identifier
            result.append(item)
        return result


def _checksums(project: Project) -> dict[str, LuaValue]:
    result: dict[str, LuaValue] = {}
    for name, item in sorted(project.checksums.items()):
        if item.algo == "custom":
            result[name] = {"algo": "custom", "impl": item.impl or ""}
            continue
        if item.algo == "crc32":
            width, poly, init, refin, refout, xorout = CRC32
        else:
            width, poly, init = item.width or 0, item.poly or 0, item.init
            refin, refout, xorout = item.refin, item.refout, item.xorout
        wide = width > WORD_BITS
        result[name] = {
            "algo": "crc",
            "width": width,
            "poly": _pair(poly, signed=False) if wide else poly,
            "init": _pair(init, signed=False) if wide else init,
            "refin": refin,
            "refout": refout,
            "xorout": _pair(xorout, signed=False) if wide else xorout,
        }
    return result


def model(project: Project) -> dict[str, LuaValue]:
    """Return the data table of the plugin for a valid project."""
    validator = Validator(project)
    by_name = {variant.name: variant for variant in validator.variants}
    names = list(project.profiles) or [""]
    variants = [by_name[name] for name in names]
    protocols = []
    seen: dict[str, str] = {}
    for interface_name in sorted(project.interfaces):
        interface = project.interfaces[interface_name]
        builder = _InterfaceBuilder(project, interface, validator)
        protocol = builder.registry.protocol
        if protocol in seen:
            message = (
                f"interfaces {seen[protocol]} and {interface_name} map to the same protocol "
                f"name {protocol}"
            )
            raise WiresharkError(message)
        seen[protocol] = interface_name
        protocols.append(builder.build(variants))
    enums = {
        name: {value: member for member, value in item.values.items()}
        for name, item in sorted(project.enums.items())
    }
    return {
        "project": project.name,
        "enums": enums,
        "checksums": _checksums(project),
        "protocols": protocols,
    }


# --- Lua rendering ------------------------------------------------------------------------------

_LUA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LUA_KEYWORDS = frozenset(
    (
        *("and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto"),
        *("if", "in", "local", "nil", "not", "or", "repeat", "return", "then", "true"),
        *("until", "while"),
    )
)


def _string(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    out = []
    for byte in data:
        char = chr(byte)
        if char in {'"', "\\"}:
            out.append("\\" + char)
        elif byte in PRINTABLE:
            out.append(char)
        else:
            out.append(f"\\{byte:03d}")
    return '"' + "".join(out) + '"'


def _key(key: object) -> str:
    if isinstance(key, str):
        if _LUA_NAME.match(key) and key not in _LUA_KEYWORDS:
            return key
        return f"[{_string(key)}]"
    return f"[{_scalar(key)}]"


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isinf(value):
            return "math.huge" if value > 0 else "-math.huge"
        return repr(value)
    if isinstance(value, (str, bytes)):
        return _string(value)
    message = f"cannot render {value!r} as Lua"
    raise TypeError(message)


def _inline(value: object) -> str:
    if isinstance(value, dict):
        items = ", ".join(f"{_key(key)} = {_inline(item)}" for key, item in value.items())
        return "{ " + items + " }" if items else "{}"
    if isinstance(value, list):
        items = ", ".join(_inline(item) for item in value)
        return "{ " + items + " }" if items else "{}"
    return _scalar(value)


def lua_table(value: object, depth: int = 0) -> str:
    """Render plain data as a Lua table constructor, one entry per line when it is long."""
    text = _inline(value)
    if len(text) + len(INDENT) * depth <= LINE_WIDTH or not isinstance(value, (dict, list)):
        return text
    inner = INDENT * (depth + 1)
    if isinstance(value, dict):
        lines = [
            f"{inner}{_key(key)} = {lua_table(item, depth + 1)}," for key, item in value.items()
        ]
    else:
        lines = [f"{inner}{lua_table(item, depth + 1)}," for item in value]
    return "{\n" + "\n".join(lines) + "\n" + INDENT * depth + "}"


def render(project: Project, content_hash: str) -> str:
    """Return the Lua plugin of a valid project."""
    data = model(project)
    version = f" {project.version}" if project.version else ""
    names = ", ".join(protocol["name"] for protocol in data["protocols"]) or "(none)"
    header = [
        f"-- Wireshark dissector for {project.name}{version}.",
        (
            f"-- Generated by WireForge {versioning.TOOL_VERSION} (file format "
            f"{versioning.FORMAT_MAJOR}.{versioning.FORMAT_MINOR}), model {content_hash}."
        ),
        "-- Do not edit: regenerate with `wireforge wireshark`.",
        "--",
        f"-- Protocols: {names}.",
        "-- Install: copy this file into the Wireshark personal Lua plugins folder",
        "-- (Help > About Wireshark > Folders), or run `tshark -X lua_script:<this file>`.",
        "-- Bind a protocol in Edit > Preferences > Protocols (UDP port, TCP port, user DLT)",
        "-- or with Decode As. Custom checksums are verified when another plugin sets",
        '-- `_G.wireforge_checksums["<impl>"] = function(bytes) return <number> end`',
        "-- (`bytes` is a Lua string of the covered bytes).",
        "",
        f"local MODEL = {lua_table(data)}",
        "",
    ]
    return "\n".join(header) + RUNTIME.read_text(encoding="utf-8")


@dataclass(frozen=True)
class Plugin:
    """Result of `build`: the plugin text, or the problems that prevent it."""

    issues: list[Issue]
    text: str = ""
    project: str = ""
    protocols: tuple[str, ...] = ()


def build(location: Path) -> Plugin:
    """Validate the project at `location` and return its plugin."""
    try:
        content_hash = bundle.build_bundle(location)["bundle"]["content_hash"]
    except bundle.BundleError as error:
        return Plugin(error.issues or [Issue(str(location), None, "", str(error))])
    result = load_project(location)
    if result.project is None:
        return Plugin(result.issues)
    project = result.project
    try:
        text = render(project, content_hash)
    except WiresharkError as error:
        return Plugin([Issue(str(location), None, "/interfaces", str(error))])
    names = tuple(sorted(protocol_name(project, item) for item in project.interfaces.values()))
    return Plugin([], text, project.name, names)


def generate(location: Path, output: Path) -> Plugin:
    """Write the plugin of the project at `location` to `output` (nothing on problems)."""
    plugin = build(location)
    if not plugin.issues:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(plugin.text, encoding="utf-8")
    return plugin


def main(argv: list[str] | None = None) -> int:
    """Command line: python -m wireforge.wireshark PROJECT -o FILE.lua."""
    parser = argparse.ArgumentParser(prog="python -m wireforge.wireshark")
    parser.add_argument("project", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)
    issues = generate(args.project, args.output).issues
    for issue in issues:
        sys.stdout.write(f"{issue}\n")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
