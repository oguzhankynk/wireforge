"""C++ spelling of model elements: types, literals, expressions and preprocessor conditions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from wireforge import expr
from wireforge.model import (
    BUILTIN_BITS,
    FLOAT_TYPES,
    INTEGER_TYPES,
    SIGNED_TYPES,
    Field,
    Project,
    Scalar,
    Struct,
    Value,
)

INT64_MIN = -(2**63)
SCALAR_TYPES = {
    "bool": "bool",
    "char": "char",
    "u8": "std::uint8_t",
    "u16": "std::uint16_t",
    "u32": "std::uint32_t",
    "u64": "std::uint64_t",
    "i8": "std::int8_t",
    "i16": "std::int16_t",
    "i32": "std::int32_t",
    "i64": "std::int64_t",
    "f32": "float",
    "f64": "double",
}
INDENT = "    "
PRINTABLE_FIRST = 0x20
PRINTABLE_END = 0x7F


@dataclass
class Writer:
    """Accumulates indented lines; preprocessor lines are never indented."""

    lines: list[str] = field(default_factory=list)
    depth: int = 0

    def line(self, text: str = "") -> None:
        """Append one line at the current indentation."""
        if not text:
            self.lines.append("")
        elif text.startswith("#"):
            self.lines.append(text)
        else:
            self.lines.append(INDENT * self.depth + text)

    def indent(self) -> None:
        """Increase the indentation."""
        self.depth += 1

    def dedent(self) -> None:
        """Decrease the indentation."""
        self.depth -= 1

    def text(self) -> str:
        """Return the accumulated text with a final newline."""
        return "\n".join(self.lines).rstrip("\n") + "\n"


def guard(*parts: str) -> str:
    """Include-guard macro for a generated header."""
    return "_".join(re.sub(r"[^A-Za-z0-9]", "_", part).upper() for part in parts)


def float_literal(value: float, type_name: str) -> str:
    """C++ literal of a floating-point value (with an F suffix for f32)."""
    text = repr(float(value))
    if "inf" in text or "nan" in text:
        message = f"{value} has no C++ literal"
        raise ValueError(message)
    if "." not in text and "e" not in text:
        text += ".0"
    return text + ("F" if type_name == "f32" else "")


def int_literal(value: int, *, unsigned: bool) -> str:
    """C++ integer literal; INT64_MIN is spelled as an expression."""
    if value == INT64_MIN:
        return "(-9223372036854775807 - 1)"
    if value < 0:
        return str(value)
    return f"{value}U" if unsigned else str(value)


def string_literal(text: str) -> str:
    """C++ string literal; non-printable and non-ASCII bytes use octal escapes."""
    out = []
    for char in text:
        code = ord(char)
        if char in {'"', "\\"}:
            out.append("\\" + char)
        elif PRINTABLE_FIRST <= code < PRINTABLE_END:
            out.append(char)
        else:
            out.append(f"\\{code:03o}")
    return '"' + "".join(out) + '"'


class Names:
    """C++ names and expressions of one project."""

    def __init__(self, project: Project) -> None:
        """Index the project's defines, constants and profile macros."""
        self.project = project
        self.profile_macros = {
            name for profile in project.profiles.values() for name in profile.defines
        } - set(project.defines)

    # --- types ----------------------------------------------------------------------------------

    def base(self, type_name: str) -> str:
        """Underlying builtin type of a builtin or enum type."""
        enum = self.project.enums.get(type_name)
        return enum.type if enum is not None else type_name

    def scalar(self, type_name: str) -> str:
        """C++ type of one element of a field."""
        user = self.project.user_types.get(type_name)
        if user is not None:
            return user.cpp
        return SCALAR_TYPES.get(type_name, type_name)

    def member_type(self, item: Field) -> str:
        """C++ type of a struct member (with occurrence storage)."""
        element = self.scalar(item.type)
        if item.occurs is None:
            return element
        capacity = self.capacity(item.occurs.max)
        if item.type == "char":
            return f"wireforge::FixedString<{capacity}>"
        if item.occurs.count is not None or item.occurs.fill:
            return f"wireforge::StaticVector<{element}, {capacity}>"
        return f"std::array<{element}, {capacity}>"

    def is_struct(self, item: Field) -> bool:
        """Whether the field's type is a struct."""
        return item.type in self.project.types

    def is_user_type(self, item: Field) -> bool:
        """Whether the field's type is a user type (opaque, user-owned encode/decode)."""
        return item.type in self.project.user_types

    def has_codec(self, item: Field) -> bool:
        """Whether the field's type has its own encode/decode functions (struct or user type)."""
        return self.is_struct(item) or self.is_user_type(item)

    # --- expressions ----------------------------------------------------------------------------

    def define(self, name: str) -> str:
        """C++ constant holding a define (overridable with -DNAME=value)."""
        return f"defines::k{name}"

    def _name(self, name: str, fields: set[str]) -> str:
        if name in fields:
            return f"static_cast<std::int64_t>(value.{name})"
        if name in self.project.defines:
            return self.define(name)
        constant = self.project.constants.get(name)
        if constant is not None:
            return f"static_cast<std::int64_t>({constant.cpp})"
        return f"static_cast<std::int64_t>({name})"

    def value(self, raw: Value, fields: set[str] | None = None) -> str:
        """Return an int64 C++ expression (field names refer to members of `value`)."""
        if isinstance(raw, int) and not isinstance(raw, bool):
            return int_literal(raw, unsigned=False) if raw < 2**63 else f"{raw}U"
        known = fields or set()
        naming = expr.CppNaming(
            name=lambda name: self._name(name, known),
            member=lambda enum, member: f"static_cast<std::int64_t>({enum}::{member})",
            size_of=lambda type_name: (
                f"static_cast<std::int64_t>(max_wire_size("
                f"wireforge::type_tag<{self.scalar(type_name)}>))"
            ),
        )
        if isinstance(raw, float):
            message = f"{raw} is not an integer expression"
            raise TypeError(message)
        return expr.to_cpp(expr.as_expression(raw), naming)

    def capacity(self, raw: Value) -> str:
        """Return a std::size_t constant expression (array capacities)."""
        if isinstance(raw, int) and not isinstance(raw, bool):
            return f"{raw}U"
        return f"static_cast<std::size_t>({self.value(raw)})"

    def condition(self, raw: str) -> str:
        """`#if` expression; root defines fall back to their default when not defined."""
        return self._preprocessor(expr.parse_condition(raw))

    def _preprocessor(self, node: expr.Node) -> str:
        if isinstance(node, expr.Name):
            define = self.project.defines.get(node.name)
            if define is None:
                return node.name
            return f"(defined({node.name}) ? {node.name} : {define.value})"
        if isinstance(node, expr.Unary):
            return f"({node.op}{self._preprocessor(node.operand)})"
        if isinstance(node, expr.Binary):
            left, right = self._preprocessor(node.left), self._preprocessor(node.right)
            return f"({left} {node.op} {right})"
        return expr.to_preprocessor(node)

    # --- literals -------------------------------------------------------------------------------

    def literal(self, item: Field, value: Scalar, scope: str = "") -> str:
        """C++ literal of one element value of a field (`scope` qualifies enum names)."""
        enum = self.project.enums.get(item.type)
        if enum is not None:
            if isinstance(value, str):
                return f"{scope}{item.type}::{value}"
            return f"static_cast<{scope}{item.type}>({value})"
        if item.type == "bool":
            return "true" if value is True or value == 1 else "false"
        if item.type == "char":
            code = ord(value) if isinstance(value, str) else int(value)
            return f"static_cast<char>({code})"
        if item.type in FLOAT_TYPES:
            return float_literal(float(value), item.type)
        return int_literal(int(value), unsigned=item.type not in SIGNED_TYPES)

    def bound(self, item: Field, raw: Value, fields: set[str] | None = None) -> str:
        """C++ expression of a min/max bound."""
        if isinstance(raw, float):
            return float_literal(raw, "f64")
        if isinstance(raw, int) and not isinstance(raw, bool):
            unsigned = raw >= 0 and self.base(item.type) not in SIGNED_TYPES
            return int_literal(raw, unsigned=unsigned)
        return self.value(raw, fields)

    def type_limits(self, item: Field) -> tuple[int, int] | None:
        """Integer range of the member type, or None for non-integers."""
        base = self.base(item.type)
        if base not in INTEGER_TYPES:
            return None
        bits = BUILTIN_BITS[base]
        if base in SIGNED_TYPES:
            return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        return 0, (1 << bits) - 1


def plain_count(item: Field) -> str | None:
    """Field name when `occurs.count` is a single field name."""
    count = item.occurs.count if item.occurs is not None else None
    if isinstance(count, str) and count.strip().isidentifier():
        return count.strip()
    return None


def wire_only(item: Struct) -> set[str]:
    """Fields that exist only on the wire: consts, reserved, checksums and plain count fields.

    A count field is wire-only when every reference to it is a plain `occurs.count` name; the
    element count of the array is then its value.
    """
    plain = {name for name in (plain_count(f) for f in item.fields) if name is not None}
    referenced: set[str] = set()
    for inner in item.fields:
        count = inner.occurs.count if inner.occurs is not None else None
        if count is not None and plain_count(inner) is None and not isinstance(count, float):
            referenced |= expr.referenced_names(expr.as_expression(count))
    hidden = {f.name for f in item.fields if f.const is not None or f.reserved or f.checksum}
    return hidden | (plain - referenced)
