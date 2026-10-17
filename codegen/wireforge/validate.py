"""Semantic validation of a loaded project.

Every issue is tagged with its rule id (`[SR-n]`). Duplicates across files (SR-1) are reported by
the loader and version stamps (SR-19) by the structural stage. Expressions are evaluated once per
variant: each profile, or the plain defines when the project has no profiles.
"""

from __future__ import annotations

import dataclasses
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from wireforge import expr
from wireforge.expr import ExprError, Scope
from wireforge.loader import load_project
from wireforge.model import (
    BUILTIN_BITS,
    FLOAT_TYPES,
    INTEGER_TYPES,
    SIGNED_TYPES,
    UNSIGNED_TYPES,
    Checksum,
    Field,
    Interface,
    Project,
    Scalar,
    Source,
    Struct,
    Value,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from wireforge.issues import Issue

BYTE_BITS = 8
MAX_GROUP_BITS = 64
MAX_CHECKSUM_BYTES = 8
FLOAT32_MAX = struct.unpack(">f", bytes.fromhex("7f7fffff"))[0]
CHECK_INPUT = b"123456789"
ROLE_TYPES_UNSIGNED = frozenset({"message_id", "payload_length", "sequence", "protocol_version"})
SINGLE_ROLES = ("message_id", "payload_length")

CPP_KEYWORDS = frozenset(
    {
        "alignas",
        "alignof",
        "and",
        "and_eq",
        "asm",
        "auto",
        "bitand",
        "bitor",
        "bool",
        "break",
        "case",
        "catch",
        "char",
        "char8_t",
        "char16_t",
        "char32_t",
        "class",
        "compl",
        "concept",
        "const",
        "consteval",
        "constexpr",
        "constinit",
        "const_cast",
        "continue",
        "co_await",
        "co_return",
        "co_yield",
        "decltype",
        "default",
        "delete",
        "do",
        "double",
        "dynamic_cast",
        "else",
        "enum",
        "explicit",
        "export",
        "extern",
        "false",
        "float",
        "for",
        "friend",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "mutable",
        "namespace",
        "new",
        "noexcept",
        "not",
        "not_eq",
        "nullptr",
        "operator",
        "or",
        "or_eq",
        "private",
        "protected",
        "public",
        "register",
        "reinterpret_cast",
        "requires",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "static_assert",
        "static_cast",
        "struct",
        "switch",
        "template",
        "this",
        "thread_local",
        "throw",
        "true",
        "try",
        "typedef",
        "typeid",
        "typename",
        "union",
        "unsigned",
        "using",
        "virtual",
        "void",
        "volatile",
        "wchar_t",
        "while",
        "xor",
        "xor_eq",
    }
)
_RESERVED_IDENTIFIER = re.compile(r"^_[A-Z]|__")

# Kinds of value expressions and what they may reference.
COUNT = "count"  # occurs.count: earlier fields allowed, may be dynamic
LAYOUT = "layout"  # capacities and frame sizes: static, no opaque constants
RANGE = "range"  # field min/max: static, opaque constants allowed (not evaluated)


@dataclass(frozen=True)
class Variant:
    """One configuration to evaluate: a profile, or the plain defines (name "")."""

    name: str
    scope: Scope
    opaque: frozenset[str]

    def label(self) -> str:
        """Suffix for messages: where the problem occurs."""
        return f" in profile {self.name!r}" if self.name else ""

    @property
    def rule(self) -> str:
        """Evaluation failures are SR-16 inside profiles, SR-5 otherwise."""
        return "SR-16" if self.name else "SR-5"


@dataclass(frozen=True)
class _At:
    """Location of a reported problem: an element plus an optional sub-pointer."""

    source: Source
    suffix: str = ""

    def below(self, suffix: str) -> _At:
        return _At(self.source, self.suffix + suffix)


@dataclass(frozen=True)
class _Context:
    """The struct (type, message, header or trailer) whose fields are being checked."""

    owner: str
    fields: tuple[Field, ...]
    is_message: bool = False
    frame_part: bool = False

    def index_of(self, name: str) -> int | None:
        for index, item in enumerate(self.fields):
            if item.name == name:
                return index
        return None


def _crc(checksum: Checksum, data: bytes) -> int:
    """Rocksoft-model CRC of `data` with the parameters of `checksum`."""
    width = checksum.width or 0
    top = 1 << (width - 1)
    mask = (1 << width) - 1
    register = checksum.init
    for byte in data:
        value = int(f"{byte:08b}"[::-1], 2) if checksum.refin else byte
        for bit in range(7, -1, -1):
            feedback = bool(register & top) ^ bool((value >> bit) & 1)
            register = ((register << 1) & mask) ^ ((checksum.poly or 0) if feedback else 0)
    if checksum.refout:
        register = int(f"{register:0{width}b}"[::-1], 2)
    return register ^ checksum.xorout


def _checksum_bits(checksum: Checksum) -> int:
    if checksum.algo == "crc32":
        return 32
    if checksum.algo == "crc":
        return checksum.width or 0
    return (checksum.size_bytes or 0) * BYTE_BITS


def _int_bounds(type_name: str, bits: int) -> tuple[int, int]:
    if type_name in SIGNED_TYPES:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def _is_relative_path(path: str) -> bool:
    parts = re.split(r"[\\/]", path)
    absolute = path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path) is not None
    return not absolute and ".." not in parts


class Validator:
    """Checks one project; `run()` returns every semantic issue."""

    def __init__(self, project: Project) -> None:
        """Prepare variants and lookup tables."""
        self.project = project
        self.issues: list[Issue] = []
        self.members = {
            (enum.name, member): value
            for enum in project.enums.values()
            for member, value in enum.values.items()
        }
        self.variants = self._variants()
        self.cyclic: set[str] = set()
        self._current: _Context | None = None  # struct whose fields are being checked
        self._bits_cache: dict[tuple[str, str, bool], int | None] = {}

    # --- infrastructure -------------------------------------------------------------------------

    def report(self, at: Source | _At, rule: str, message: str, suffix: str = "") -> None:
        """Record one error at an element (plus optional sub-pointer)."""
        where = at if isinstance(at, _At) else _At(at)
        self.issues.append(where.source.issue(rule, message, where.suffix + suffix))

    def _variants(self) -> list[Variant]:
        defines = {name: define.value for name, define in self.project.defines.items()}
        if not self.project.profiles:
            return [self._variant("", defines, None)]
        return [
            self._variant(name, {**defines, **profile.defines}, name)
            for name, profile in self.project.profiles.items()
        ]

    def _variant(self, name: str, defines: dict[str, int], profile: str | None) -> Variant:
        values = dict(defines)
        opaque = set()
        for constant in self.project.constants.values():
            value = constant.profile_values.get(profile or "", constant.value)
            if value is None:
                opaque.add(constant.name)
            else:
                values[constant.name] = value
        scope = Scope(values=values, members=self.members, defined=frozenset(values))
        return Variant(name, scope, frozenset(opaque))

    def run(self) -> list[Issue]:
        """Check every rule and return the issues found."""
        self._names()
        self._enums()
        self._resolve_types()
        self._constants()
        self._checksums()
        for item in self.project.types.values():
            self._struct(_Context(f"type {item.name}", item.fields), item)
        for item in self.project.messages.values():
            self._struct(_Context(f"message {item.name}", item.fields, is_message=True), item)
        for interface in self.project.interfaces.values():
            self._interface(interface)
        self._user_includes()
        return self.issues

    # --- names ------------------------------------------------------------------------------------

    def _check_identifier(self, name: str, source: Source, what: str) -> None:
        if name in CPP_KEYWORDS:
            self.report(source, "SR-2", f"{what} {name!r} is a C++ keyword")
        elif _RESERVED_IDENTIFIER.search(name):
            self.report(source, "SR-2", f"{what} {name!r} is a reserved C++ identifier")

    def _names(self) -> None:
        project = self.project
        generated: dict[str, str] = {}
        sections: list[tuple[str, Iterable[tuple[str, Source]]]] = [
            ("define", ((n, d.source) for n, d in project.defines.items())),
            ("enum", ((n, e.source) for n, e in project.enums.items())),
            ("type", ((n, t.source) for n, t in project.types.items())),
            ("message", ((n, m.source) for n, m in project.messages.items())),
            ("interface", ((n, i.source) for n, i in project.interfaces.items())),
            ("checksum", ((n, c.source) for n, c in project.checksums.items())),
            ("constant", ((n, c.source) for n, c in project.constants.items())),
        ]
        for what, entries in sections:
            for name, source in entries:
                self._check_identifier(name, source, what)
                if what in {"constant", "checksum"}:
                    continue
                if name in generated:
                    message = f"{what} {name!r} collides with {generated[name]} {name!r} in C++"
                    self.report(source, "SR-2", message)
                generated.setdefault(name, what)
        for enum in project.enums.values():
            for member in enum.values:
                self._check_identifier(member, enum.source, f"member of enum {enum.name}")

    # --- enums ------------------------------------------------------------------------------------

    def _enums(self) -> None:
        for enum in self.project.enums.values():
            low, high = _int_bounds(enum.type, BUILTIN_BITS[enum.type])
            seen: dict[int, str] = {}
            for member, value in enum.values.items():
                if not low <= value <= high:
                    message = f"enum {enum.name}.{member} = {value} does not fit {enum.type}"
                    self.report(enum.source, "SR-15", message, f"/values/{member}")
                if value in seen:
                    message = f"enum {enum.name}: {member} and {seen[value]} share value {value}"
                    self.report(enum.source, "SR-15", message, f"/values/{member}")
                seen.setdefault(value, member)

    # --- types ------------------------------------------------------------------------------------

    def _all_field_lists(self) -> Iterable[tuple[str, tuple[Field, ...]]]:
        for item in self.project.types.values():
            yield f"type {item.name}", item.fields
        for item in self.project.messages.values():
            yield f"message {item.name}", item.fields
        for interface in self.project.interfaces.values():
            yield f"header of interface {interface.name}", interface.header or ()
            yield f"trailer of interface {interface.name}", interface.trailer

    def _resolve_types(self) -> None:
        known = set(BUILTIN_BITS) | set(self.project.enums) | set(self.project.types)
        for owner, fields in self._all_field_lists():
            for item in fields:
                if item.type in known:
                    continue
                if item.type in self.project.messages:
                    message = f"{owner}: field {item.name} uses message {item.type} as a type; "
                    message += "move the shared fields to `types`"
                else:
                    message = f"{owner}: field {item.name} has unknown type {item.type!r}"
                self.report(item.source, "SR-3", message, "/type")
        self._find_cycles()

    def _find_cycles(self) -> None:
        done: set[str] = set()
        for name in self.project.types:
            self._visit(name, [], done)

    def _visit(self, name: str, path: list[str], done: set[str]) -> None:
        if name in path:
            cycle = [*path[path.index(name) :], name]
            self.cyclic.update(cycle)
            message = f"type {name} contains itself: {' -> '.join(cycle)}"
            self.report(self.project.types[name].source, "SR-3", message)
            return
        if name in done:
            return
        for inner in self.project.types[name].fields:
            if inner.type in self.project.types:
                self._visit(inner.type, [*path, name], done)
        done.add(name)

    # --- user constants ---------------------------------------------------------------------------

    def _constants(self) -> None:
        field_names = {item.name for _, fields in self._all_field_lists() for item in fields}
        profile_defines = {name for p in self.project.profiles.values() for name in p.defines}
        for constant in self.project.constants.values():
            source = constant.source
            if constant.name in self.project.defines or constant.name in profile_defines:
                self.report(source, "SR-18", f"constant {constant.name} collides with a define")
            if constant.name in field_names:
                message = f"constant {constant.name} collides with a field of the same name"
                self.report(source, "SR-18", message)
            for profile in constant.profile_values:
                if profile not in self.project.profiles:
                    message = f"constant {constant.name}: unknown profile {profile!r}"
                    self.report(source, "SR-18", message, f"/profile_values/{profile}")
            given = set(constant.profile_values)
            partial = given and given != set(self.project.profiles)
            if constant.value is None and partial:
                message = (
                    f"constant {constant.name} has no value and values for only some profiles; "
                    "give `value` or a value for every profile"
                )
                self.report(source, "SR-18", message)

    # --- checksum declarations --------------------------------------------------------------------

    def _checksums(self) -> None:
        for checksum in self.project.checksums.values():
            if checksum.algo == "crc":
                self._crc_parameters(checksum)
            if checksum.algo == "custom":
                self._custom_checksum(checksum)

    def _crc_parameters(self, checksum: Checksum) -> None:
        width = checksum.width or 0
        limit = 1 << width
        for key in ("poly", "init", "xorout"):
            value = getattr(checksum, key)
            if value is not None and value >= limit:
                message = f"checksum {checksum.name}: {key} {value:#x} does not fit width {width}"
                self.report(checksum.source, "SR-12", message, f"/{key}")
        if checksum.check is not None and checksum.poly is not None:
            actual = _crc(checksum, CHECK_INPUT)
            if actual != checksum.check:
                message = (
                    f"checksum {checksum.name}: check value {checksum.check:#x} does not match "
                    f"the computed {actual:#x} for '123456789'"
                )
                self.report(checksum.source, "SR-12", message, "/check")

    def _custom_checksum(self, checksum: Checksum) -> None:
        if (checksum.size_bytes or 0) > MAX_CHECKSUM_BYTES:
            message = f"checksum {checksum.name}: size_bytes must be 1..8 (runtime contract)"
            self.report(checksum.source, "SR-12", message, "/size_bytes")
        if checksum.include is not None and not _is_relative_path(checksum.include):
            message = f"checksum {checksum.name}: include {checksum.include!r} must be relative"
            self.report(checksum.source, "SR-20", message + " without '..'", "/include")

    # --- expressions ------------------------------------------------------------------------------

    def _parse(self, raw: Value, at: _At) -> expr.Node | None:
        if isinstance(raw, float):
            self.report(at, "SR-5", f"invalid expression {raw!r}: not an integer")
            return None
        try:
            return expr.as_expression(raw)
        except ExprError as error:
            self.report(at, "SR-5", f"invalid expression {raw!r}: {error}")
            return None

    def _profile_define(self, name: str) -> bool:
        return any(name in profile.defines for profile in self.project.profiles.values())

    def _constant_use(self, name: str, at: _At, kind: str) -> None:
        if kind == LAYOUT and any(name in variant.opaque for variant in self.variants):
            message = (
                f"constant {name} has no value (opaque) and may only be used in min/max, "
                "not in sizes or counts"
            )
            self.report(at, "SR-18", message)

    def _names_resolve(self, node: expr.Node, at: _At, kind: str, context: _Context | None) -> bool:
        """Check every name; returns False when the expression refers to fields (not static)."""
        static = True
        for name in sorted(expr.referenced_names(node)):
            if context is not None and context.index_of(name) is not None:
                static = False
                if kind != COUNT:
                    self.report(
                        at, "SR-5", f"field {name!r} may only be referenced by occurs.count"
                    )
            elif name in self.project.constants:
                self._constant_use(name, at, kind)
            elif name not in self.project.defines and not self._profile_define(name):
                self.report(at, "SR-5", f"unknown name {name!r}")
        for enum, member in sorted(expr.referenced_members(node)):
            if (enum, member) not in self.members:
                self.report(at, "SR-5", f"unknown enum member {enum}.{member}")
        for type_name in sorted(expr.call_arguments(node, "sizeof")):
            if type_name not in self.project.types and type_name not in BUILTIN_BITS:
                self.report(at, "SR-5", f"sizeof: unknown type {type_name!r}")
        return static

    def _scope_with_sizes(self, node: expr.Node, variant: Variant) -> Scope | None:
        sizes = {}
        for type_name in expr.call_arguments(node, "sizeof"):
            bits = self._type_bits(type_name, variant, maximum=False)
            if bits is None or bits % BYTE_BITS:
                return None
            sizes[type_name] = bits // BYTE_BITS
        return dataclasses.replace(variant.scope, sizes=sizes) if sizes else variant.scope

    def _evaluate(
        self, raw: Value, at: _At, kind: str, context: _Context | None = None
    ) -> dict[str, int] | None:
        """Values per variant, or None when the expression is invalid or not static."""
        node = self._parse(raw, at)
        if node is None or not self._names_resolve(node, at, kind, context):
            return None
        names = expr.referenced_names(node)
        results: dict[str, int] = {}
        for variant in self.variants:
            if names & variant.opaque:
                continue
            scope = self._scope_with_sizes(node, variant)
            if scope is None:
                self.report(at, "SR-5", "sizeof() needs a fixed-size type" + variant.label())
                return None
            try:
                results[variant.name] = expr.evaluate(node, scope)
            except ExprError as error:
                self.report(at, variant.rule, f"cannot evaluate {raw!r}{variant.label()}: {error}")
                return None
        return results

    def static_value(self, raw: Value, variant: Variant) -> int | None:
        """Silent evaluation of a layout expression (problems are reported elsewhere)."""
        try:
            node = expr.as_expression(raw) if not isinstance(raw, float) else None
            scope = None if node is None else self._scope_with_sizes(node, variant)
            return None if node is None or scope is None else expr.evaluate(node, scope)
        except ExprError:
            return None

    def _condition(self, raw: str | None, at: _At, context: _Context | None = None) -> None:
        if raw is None:
            return
        try:
            node = expr.parse_condition(raw)
        except ExprError as error:
            self.report(at, "SR-5", f"invalid if: condition {raw!r}: {error}")
            return
        for name in sorted(expr.referenced_names(node)):
            if context is not None and context.index_of(name) is not None:
                self.report(at, "SR-5", f"if: conditions must not reference fields ({name!r})")
            elif name in self.project.constants:
                message = f"constant {name} is a C++ symbol, not a macro; it cannot appear in if:"
                self.report(at, "SR-18", message)
            elif name not in self.project.defines:
                message = f"if: may name only defines; use defined({name}) for profile macros"
                self.report(at, "SR-5", message)

    # --- sizes ----------------------------------------------------------------------------------

    def _type_bits(self, type_name: str, variant: Variant, *, maximum: bool) -> int | None:
        """Wire size in bits of one element; with maximum=True dynamic arrays count at capacity."""
        if type_name in BUILTIN_BITS:
            return BUILTIN_BITS[type_name]
        if type_name in self.project.enums:
            return BUILTIN_BITS[self.project.enums[type_name].type]
        item = self.project.types.get(type_name)
        if item is None or type_name in self.cyclic:
            return None
        key = (type_name, variant.name, maximum)
        if key not in self._bits_cache:
            self._bits_cache[key] = self._fields_bits(item.fields, variant, maximum=maximum)
        return self._bits_cache[key]

    def present(self, item: Field, variant: Variant) -> bool:
        """Return whether the field's `if:` condition holds in `variant` (true when unknown)."""
        if item.condition is None:
            return True
        try:
            return expr.evaluate(expr.parse_condition(item.condition), variant.scope) != 0
        except ExprError:
            return True

    def _fields_bits(
        self, fields: tuple[Field, ...], variant: Variant, *, maximum: bool
    ) -> int | None:
        total = 0
        for item in fields:
            if not self.present(item, variant):
                continue
            bits = self._field_bits(item, variant, maximum=maximum)
            if bits is None:
                return None
            total += bits
        return total

    def _field_bits(self, item: Field, variant: Variant, *, maximum: bool) -> int | None:
        element = item.bits or self._type_bits(item.type, variant, maximum=maximum)
        if element is None or item.occurs is None:
            return element
        dynamic = item.occurs.count is not None or item.occurs.fill
        if dynamic and not maximum:
            return None
        count = self.static_value(item.occurs.max, variant)
        return None if count is None else element * count

    # --- structs --------------------------------------------------------------------------------

    def _struct(self, context: _Context, item: Struct | None = None) -> None:
        if item is not None:
            self._condition(item.condition, _At(item.source, "/if"))
        seen: set[str] = set()
        self._current = context
        for index, field in enumerate(context.fields):
            at = _At(field.source)
            self._check_identifier(field.name, field.source, f"{context.owner}: field")
            if field.name in seen:
                self.report(at, "SR-2", f"{context.owner}: duplicate field {field.name!r}")
            seen.add(field.name)
            self._field_bits_rules(field, at)
            self._field_occurs(field, index, context)
            self._field_role(field, context)
            self._field_checksum(field, index, context)
            self._field_values(field, at)
            self._condition(field.condition, at.below("/if"), context)
        pure_bits_type = item is not None and item.message is None and self._pure_bits(item)
        self._bit_groups(context, check_total=not pure_bits_type)

    def _pure_bits(self, item: Struct) -> bool:
        return bool(item.fields) and all(field.bits is not None for field in item.fields)

    def _field_bits_rules(self, field: Field, at: _At) -> None:
        if field.bits is None:
            return
        base = (
            self.project.enums[field.type].type if field.type in self.project.enums else field.type
        )
        if base not in INTEGER_TYPES and base != "bool":
            message = f"bits is only allowed on integer, bool and enum fields ({field.name})"
            self.report(at, "SR-10", message, "/bits")
        elif field.bits > BUILTIN_BITS[base]:
            message = f"field {field.name}: bits {field.bits} exceeds the width of {base}"
            self.report(at, "SR-10", message, "/bits")
        if field.occurs is not None:
            self.report(at, "SR-11", f"field {field.name}: occurs cannot be combined with bits")

    def _group_bits(self, field: Field) -> int | None:
        """Bits contributed to a bit group, or None when the field is byte-oriented."""
        if field.bits is not None:
            return field.bits if field.occurs is None else None
        item = self.project.types.get(field.type)
        if item is None or field.occurs is not None or not self._pure_bits(item):
            return None
        return sum(inner.bits or 0 for inner in item.fields)

    def _bit_groups(self, context: _Context, *, check_total: bool) -> None:
        group: list[Field] = []
        for field in [*context.fields, None]:
            bits = None if field is None else self._group_bits(field)
            if bits is not None and field is not None:
                group.append(field)
                continue
            if group:
                self._close_group(context, group, check_total=check_total)
            group = []

    def _close_group(self, context: _Context, group: list[Field], *, check_total: bool) -> None:
        total = sum(self._group_bits(field) or 0 for field in group)
        first = group[0]
        if check_total and total % BYTE_BITS:
            message = (
                f"{context.owner}: bit group starting at {first.name} totals {total} bits; "
                "add reserved fields up to a multiple of 8"
            )
            self.report(first.source, "SR-10", message)
        if total > MAX_GROUP_BITS:
            message = f"{context.owner}: bit group starting at {first.name} exceeds 64 bits"
            self.report(first.source, "SR-10", message)
        for field in group:
            if field.condition is not None:
                message = (
                    f"{context.owner}: conditional field {field.name} inside a bit group would "
                    "change the bit layout per profile; make the whole group conditional"
                )
                self.report(field.source, "SR-17", message, "/if")

    def _field_occurs(self, field: Field, index: int, context: _Context) -> None:
        occurs = field.occurs
        if occurs is None:
            return
        at = _At(field.source, "/occurs")
        maxima = self._evaluate(occurs.max, at.below("/max"), LAYOUT) or {}
        minima = {} if occurs.min is None else self._evaluate(occurs.min, at.below("/min"), LAYOUT)
        for variant in self.variants:
            high = maxima.get(variant.name)
            low = (minima or {}).get(variant.name, 0)
            if high is not None and high <= 0:
                message = f"field {field.name}: occurs.max must be positive{variant.label()}"
                self.report(at, "SR-8", message, "/max")
            if high is not None and not 0 <= low <= high:
                message = f"field {field.name}: occurs.min must lie in 0..max{variant.label()}"
                self.report(at, "SR-8", message, "/min")
        if occurs.count is not None:
            self._evaluate(occurs.count, at.below("/count"), COUNT, context)
            self._count_references(field, index, context, at.below("/count"))
        if occurs.fill:
            self._fill(field, index, context, at)

    def _count_references(self, field: Field, index: int, context: _Context, at: _At) -> None:
        count = field.occurs.count if field.occurs is not None else None
        try:
            node = None if count is None or isinstance(count, float) else expr.as_expression(count)
        except ExprError:
            node = None  # already reported by _evaluate
        for name in sorted(expr.referenced_names(node) if node is not None else ()):
            position = context.index_of(name)
            if position is None:
                continue
            target = context.fields[position]
            base = self.project.enums.get(target.type)
            scalar = target.type in INTEGER_TYPES or target.type == "bool" or base is not None
            if position >= index:
                message = f"count field {name!r} must precede {field.name}"
                self.report(at, "SR-7", message)
            elif not scalar or target.occurs is not None:
                message = f"count field {name!r} must be a single integer or bool"
                self.report(at, "SR-7", message)

    def _fill(self, field: Field, index: int, context: _Context, at: _At) -> None:
        if not context.is_message or index != len(context.fields) - 1:
            message = f"field {field.name}: fill is only allowed on the last field of a message"
            self.report(at, "SR-8", message, "/fill")
            return
        for variant in self.variants:
            element = field.bits or self._type_bits(field.type, variant, maximum=False)
            if element is None:
                message = f"field {field.name}: fill needs a constant element size{variant.label()}"
                self.report(at, "SR-8", message, "/fill")
                return

    def _field_role(self, field: Field, context: _Context) -> None:
        if field.role is not None and not context.frame_part:
            message = f"{context.owner}: role {field.role!r} is only allowed in headers/trailers"
            self.report(field.source, "SR-13", message, "/role")

    # --- checksum fields --------------------------------------------------------------------------

    def _field_checksum(self, field: Field, index: int, context: _Context) -> None:
        use = field.checksum
        if use is None:
            return
        at = _At(field.source, "/checksum")
        algo = self.project.checksums.get(use.use)
        width = field.bits or BUILTIN_BITS.get(field.type, 0)
        if algo is None:
            self.report(at, "SR-4", f"field {field.name}: unknown checksum {use.use!r}", "/use")
        elif field.type not in UNSIGNED_TYPES or field.occurs is not None:
            message = f"checksum field {field.name} must be a single unsigned integer"
            self.report(at, "SR-12", message)
        elif width != _checksum_bits(algo):
            message = (
                f"checksum field {field.name} has {width} bits but {algo.name} produces "
                f"{_checksum_bits(algo)}"
            )
            self.report(at, "SR-12", message)
        if use.scope in {"payload", "header_and_payload"} and not context.frame_part:
            message = f"covers scope {use.scope!r} is only allowed in headers/trailers"
            self.report(at, "SR-12", message, "/covers/scope")
        if use.first is not None and use.last is not None:
            self._covers_range(field, index, context, at.below("/covers"))

    def _covers_range(self, field: Field, index: int, context: _Context, at: _At) -> None:
        use = field.checksum
        if use is None or use.first is None or use.last is None:
            return
        first = context.index_of(use.first)
        last = context.index_of(use.last)
        for name, position in ((use.first, first), (use.last, last)):
            if position is None:
                self.report(at, "SR-4", f"covers names unknown field {name!r}")
        if first is not None and last is not None and not first <= last < index:
            message = (
                f"covers {use.first}..{use.last} must be ordered and precede the checksum "
                f"field {field.name}"
            )
            self.report(at, "SR-12", message)

    # --- values and ranges ------------------------------------------------------------------------

    def _value_kind(self, field: Field) -> str:
        if field.type in self.project.enums:
            return "enum"
        if field.type in FLOAT_TYPES:
            return "float"
        if field.type in INTEGER_TYPES:
            return "int"
        if field.type in {"bool", "char"}:
            return field.type
        return "struct"

    def _type_range(self, field: Field, kind: str) -> tuple[float, float]:
        if kind == "float":
            limit = FLOAT32_MAX if field.type == "f32" else sys.float_info.max
            return -limit, limit
        if kind in {"bool", "char"}:
            return 0, 1 if kind == "bool" else 255
        base = self.project.enums[field.type].type if kind == "enum" else field.type
        return _int_bounds(base, field.bits or BUILTIN_BITS[base])

    def _field_values(self, field: Field, at: _At) -> None:
        kind = self._value_kind(field)
        given = [
            key for key in ("min", "max", "default", "const") if getattr(field, key) is not None
        ]
        if kind == "struct":
            for key in given:
                self.report(at, "SR-6", f"field {field.name}: {key} needs a scalar type", f"/{key}")
            return
        type_range = self._type_range(field, kind)
        lows = self._bound(field, "min", kind, at)
        highs = self._bound(field, "max", kind, at)
        self._check_bounds(field, at, (lows, highs), type_range)
        limits = (lows, highs, type_range)
        if field.default is not None:
            self._check_default(field, at.below("/default"), kind, limits)
        if field.const is not None:
            if field.occurs is not None:
                self.report(at, "SR-6", f"field {field.name}: const cannot have occurs", "/const")
            self._check_scalar(field, field.const, at.below("/const"), kind, limits)

    def _bound(self, field: Field, key: str, kind: str, at: _At) -> dict[str, float]:
        raw = getattr(field, key)
        if raw is None:
            return {}
        if isinstance(raw, float) and kind != "float":
            message = f"field {field.name}: {key} {raw} must be an integer for type {field.type}"
            self.report(at, "SR-6", message, f"/{key}")
            return {}
        if isinstance(raw, (int, float)):
            return {variant.name: raw for variant in self.variants}
        return dict(self._evaluate(raw, at.below(f"/{key}"), RANGE, self._current) or {})

    def _check_bounds(
        self,
        field: Field,
        at: _At,
        bounds: tuple[dict[str, float], dict[str, float]],
        type_range: tuple[float, float],
    ) -> None:
        lows, highs = bounds
        for variant in self.variants:
            low = lows.get(variant.name)
            high = highs.get(variant.name)
            if low is not None and high is not None and low > high:
                message = f"field {field.name}: min {low} > max {high}{variant.label()}"
                self.report(at, "SR-6", message, "/min")
            for key, value in (("min", low), ("max", high)):
                if value is not None and not type_range[0] <= value <= type_range[1]:
                    message = (
                        f"field {field.name}: {key} {value} is outside what {field.type}"
                        f"{f' with {field.bits} bits' if field.bits else ''} can hold"
                    )
                    self.report(at, "SR-6", message, f"/{key}")

    def _check_default(
        self,
        field: Field,
        at: _At,
        kind: str,
        limits: tuple[dict[str, float], dict[str, float], tuple[float, float]],
    ) -> None:
        default = field.default
        text = isinstance(default, str) and kind == "char" and field.occurs is not None
        if not isinstance(default, list) and not text:
            self._check_scalar(field, default, at, kind, limits)
            return
        if field.occurs is None:
            self.report(at, "SR-6", f"field {field.name}: a list default needs occurs")
            return
        elements: list[Scalar] = list(default) if isinstance(default, (list, str)) else []
        for variant in self.variants:
            capacity = self.static_value(field.occurs.max, variant)
            if capacity is not None and len(elements) > capacity:
                message = (
                    f"field {field.name}: default has {len(elements)} elements, "
                    f"max is {capacity}{variant.label()}"
                )
                self.report(at, "SR-6", message)
        for position, element in enumerate(elements):
            self._check_scalar(field, element, at.below(f"/{position}"), kind, limits)

    def _scalar_number(
        self, field: Field, value: Scalar | list[Scalar] | None, kind: str, at: _At
    ) -> float | None:
        number: float | None = None
        if kind == "enum" and isinstance(value, str):
            number = self.project.enums[field.type].values.get(value)
            if number is None:
                self.report(
                    at, "SR-4", f"field {field.name}: {value!r} is not a member of {field.type}"
                )
                return None
        elif kind == "enum" and isinstance(value, int) and not isinstance(value, bool):
            if value in self.project.enums[field.type].values.values():
                number = value
        elif kind == "bool" and isinstance(value, bool):
            number = int(value)
        elif kind == "char" and isinstance(value, str) and len(value) == 1:
            number = ord(value)
        elif (kind in {"char", "int"} and isinstance(value, int)) or (
            kind == "float" and isinstance(value, (int, float))
        ):
            number = None if isinstance(value, bool) else value
        if number is None:
            self.report(
                at, "SR-6", f"field {field.name}: {value!r} is not a valid {field.type} value"
            )
        return number

    def _check_scalar(
        self,
        field: Field,
        value: Scalar | list[Scalar] | None,
        at: _At,
        kind: str,
        limits: tuple[dict[str, float], dict[str, float], tuple[float, float]],
    ) -> None:
        number = self._scalar_number(field, value, kind, at)
        if number is None:
            return
        lows, highs, type_range = limits
        if not type_range[0] <= number <= type_range[1]:
            message = f"field {field.name}: {value!r} does not fit {field.type}"
            self.report(at, "SR-6", message)
            return
        for variant in self.variants:
            low = lows.get(variant.name, type_range[0])
            high = highs.get(variant.name, type_range[1])
            if not low <= number <= high:
                message = (
                    f"field {field.name}: {value!r} is outside [{low}, {high}]{variant.label()}"
                )
                self.report(at, "SR-6", message)
                return

    # --- interfaces -------------------------------------------------------------------------------

    def _interface(self, interface: Interface) -> None:
        if interface.header is not None:
            owner = f"header of interface {interface.name}"
            self._struct(_Context(owner, interface.header, frame_part=True))
            self._header_roles(interface)
        if interface.trailer:
            owner = f"trailer of interface {interface.name}"
            self._struct(_Context(owner, interface.trailer, frame_part=True))
        self._framing(interface)
        self._interface_messages(interface)
        self._frame_sizes(interface)

    def _header_roles(self, interface: Interface) -> None:
        header = interface.header or ()
        for role in SINGLE_ROLES:
            holders = [field for field in header if field.role == role]
            for extra in holders[1:]:
                message = f"interface {interface.name}: role {role} appears more than once"
                self.report(extra.source, "SR-13", message, "/role")
        for field in header:
            if field.role == "sync" and field.const is None:
                message = f"interface {interface.name}: sync field {field.name} needs a const value"
                self.report(field.source, "SR-13", message)
            if field.role in ROLE_TYPES_UNSIGNED and field.type not in UNSIGNED_TYPES:
                message = f"interface {interface.name}: {field.role} field must be unsigned"
                self.report(field.source, "SR-13", message, "/type")

    def _framing(self, interface: Interface) -> None:
        at = _At(interface.source)
        roles = {field.role for field in interface.header or ()}
        needed = {"sync_length": {"sync", "payload_length"}, "length_prefixed": {"payload_length"}}
        missing = needed.get(interface.framing.kind, set()) - roles
        if missing:
            message = (
                f"interface {interface.name}: framing {interface.framing.kind} needs header "
                f"fields with role {', '.join(sorted(missing))}"
            )
            self.report(at, "SR-14", message, "/framing/kind")
        if len(interface.messages) > 1 and "message_id" not in roles:
            message = (
                f"interface {interface.name} carries {len(interface.messages)} messages and "
                "needs a header field with role message_id (raw interfaces carry at most one)"
            )
            self.report(at, "SR-14", message, "/messages")
        sizes = [("framing/frame_size", interface.framing.frame_size)]
        sizes.append(("framing/max_frame_size", interface.framing.max_frame_size))
        sizes += [(f"buffers/{key}", value) for key, value in interface.buffers.items()]
        for path, raw in sizes:
            if raw is None:
                continue
            for name, value in (self._evaluate(raw, at.below(f"/{path}"), LAYOUT) or {}).items():
                if value <= 0:
                    label = f" in profile {name!r}" if name else ""
                    self.report(at, "SR-16", f"{path} must be positive{label}", f"/{path}")

    def _message_ids(self, interface: Interface) -> list[tuple[int, str, Source]]:
        ids = []
        for entry in interface.messages:
            message = self.project.messages.get(entry.name)
            if message is None:
                continue
            declared = message.message.id if message.message is not None else None
            identifier = entry.id if entry.id is not None else declared
            if identifier is None:
                text = f"message {entry.name} has no id on interface {interface.name}"
                self.report(entry.source, "SR-9", text)
            else:
                ids.append((identifier, entry.name, entry.source))
        return ids

    def _interface_messages(self, interface: Interface) -> None:
        seen: set[str] = set()
        for entry in interface.messages:
            if entry.name not in self.project.messages:
                self.report(entry.source, "SR-4", f"unknown message {entry.name!r}")
            if entry.name in seen:
                self.report(entry.source, "SR-9", f"message {entry.name} is listed twice")
            seen.add(entry.name)
            self._condition(entry.condition, _At(entry.source, "/if"))
        id_field = next((f for f in interface.header or () if f.role == "message_id"), None)
        if id_field is None:
            return
        width = id_field.bits or BUILTIN_BITS.get(id_field.type, 0)
        owners: dict[int, str] = {}
        for identifier, name, source in self._message_ids(interface):
            if identifier >= 1 << width:
                self.report(
                    source, "SR-9", f"id {identifier:#x} of {name} does not fit {width} bits"
                )
            if identifier in owners:
                text = f"id {identifier:#x} of {name} is already used by {owners[identifier]}"
                self.report(source, "SR-9", text)
            owners.setdefault(identifier, name)

    def _frame_sizes(self, interface: Interface) -> None:
        framing = interface.framing
        limit_raw = framing.frame_size if framing.kind == "fixed" else framing.max_frame_size
        if limit_raw is None:
            return
        for variant in self.variants:
            limit = self.static_value(limit_raw, variant)
            frame = self._fields_bits(
                (*(interface.header or ()), *interface.trailer), variant, maximum=True
            )
            if limit is None or frame is None:
                continue
            for entry in interface.messages:
                message = self.project.messages.get(entry.name)
                payload = (
                    None
                    if message is None
                    else self._fields_bits(message.fields, variant, maximum=True)
                )
                if payload is not None and (frame + payload) // BYTE_BITS > limit:
                    text = (
                        f"interface {interface.name}: message {entry.name} needs up to "
                        f"{(frame + payload) // BYTE_BITS} bytes per frame, more than {limit}"
                        f"{variant.label()}"
                    )
                    self.report(entry.source, "SR-16", text)

    # --- user includes ----------------------------------------------------------------------------

    def _user_includes(self) -> None:
        for directory, source in self.project.include_dirs:
            if not _is_relative_path(directory):
                text = f"include directory {directory!r} must be relative without '..'"
                self.report(source, "SR-20", text)
        for header in self.project.headers:
            if not _is_relative_path(header.path):
                text = f"header {header.path!r} must be relative without '..'"
                self.report(header.source, "SR-20", text, "/path")
            self._condition(header.condition, _At(header.source, "/if"))


def validate_project(location: Path) -> list[Issue]:
    """Load and validate a project: structural issues first, semantic issues after."""
    result = load_project(location)
    if result.project is None:
        return result.issues
    return result.issues + Validator(result.project).run()


def main(argv: list[str] | None = None) -> int:
    """Validate project directories and print one line per issue."""
    paths = [Path(arg) for arg in (sys.argv[1:] if argv is None else argv)]
    failures = 0
    for path in paths:
        issues = validate_project(path)
        failures += len([issue for issue in issues if issue.severity == "error"])
        for issue in issues:
            sys.stdout.write(f"{issue}\n")
        if not issues:
            sys.stdout.write(f"{path}: OK\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
