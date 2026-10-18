"""C99 code of the types and messages of a project: `model.h` and `model.c`.

The rules are those of the C++ code (`generate.py`, `cpp_codec.py`): the same layout on the wire,
the same checks in the same order, the same statuses. What differs is the shape: a struct has fixed
arrays and a `<name>_count` member for every array of variable size (a string, an optional field, a
counted array, a fill array), an enum is an integer type with one macro per member, and every
function is global and starts with the prefix of the project (`c_names.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wireforge.c_names import C_KEYWORDS, CGenerationError, CNames, used_names
from wireforge.cpp import Writer, guard, plain_count, string_literal, type_order, wire_only
from wireforge.model import (
    BUILTIN_BITS,
    FLOAT_TYPES,
    SIGNED_TYPES,
    Checksum,
    Field,
    Interface,
    Project,
    Struct,
    Value,
)
from wireforge.scaling import (
    function_names,
    is_float,
    lower_check,
    offset_of,
    scale_of,
    scaled_fields,
    upper_check,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from wireforge.generate import Banner

BYTE_BITS = 8
CRC32 = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)


def _description(text: str | None, fallback: str) -> str:
    return " ".join((text or fallback).split())


def checksum_size(checksum: Checksum) -> int:
    """Return the number of bytes a checksum takes on the wire."""
    if checksum.algo == "crc32":
        return 4
    if checksum.algo == "custom":
        return checksum.size_bytes or 0
    return ((checksum.width or 0) + 7) // 8


def encoding_initializer(project: Project, item: Struct, interface: Interface | None = None) -> str:
    """Return the C initializer of the wf_encoding of a message (message, interface, project)."""
    info = item.message
    defaults = project.defaults
    iface_endian = interface.endian if interface else None
    iface_order = interface.bit_order if interface else None
    endian = (info.endian if info else None) or iface_endian or defaults.get("endian") or "big"
    order = (info.bit_order if info else None) or iface_order or defaults.get("bit_order")
    order = order or "msb_first"
    checks: dict[str, object] = {"encode": True, "decode": True}
    configured = defaults.get("range_check")
    checks.update(configured if isinstance(configured, dict) else {})
    checks.update((info.range_check if info else None) or {})
    endian_name = "WF_BIG_ENDIAN" if endian == "big" else "WF_LITTLE_ENDIAN"
    order_name = "WF_MSB_FIRST" if order == "msb_first" else "WF_LSB_FIRST"
    flags = ", ".join("true" if checks[key] else "false" for key in ("encode", "decode"))
    trailing = "true" if interface is not None and interface.allow_trailing_bytes else "false"
    return f"{{{endian_name}, {order_name}, {flags}, {trailing}}}"


@dataclass(frozen=True)
class _Leaf:
    """One field of a bit group: the model field and the C expression of its value."""

    field: Field
    subject: str


class CModel:
    """Writes `model.h` and `model.c` of one project."""

    def __init__(self, project: Project, banner: Banner) -> None:
        """Bind to the project; refuse what generated C cannot express yet."""
        self.project = project
        self.names = CNames(project)
        self.banner = banner
        self.header = Writer()
        self.source = Writer()
        self._check_supported()
        self.called = self._called_types()
        self.used_types, self.used_enums = used_names(project)

    # --- limits of the C generator ----------------------------------------------------------------

    def _check_supported(self) -> None:
        project = self.project
        if project.user_types:
            message = "generated C does not support user types yet"
            raise CGenerationError(message)
        for checksum in project.checksums.values():
            if checksum.algo == "custom" and not (checksum.c_impl and checksum.c_include):
                message = (
                    f"checksum {checksum.name} is custom: generated C needs `c_impl` (a function "
                    "uint64_t f(const uint8_t *data, size_t length)) and `c_include`"
                )
                raise CGenerationError(message)
        for item in [*project.types.values(), *project.messages.values()]:
            for field in item.fields:
                if field.name in C_KEYWORDS:
                    message = f"{item.name}.{field.name}: {field.name} is a word of the C language"
                    raise CGenerationError(message)

    def _called_types(self) -> set[str]:
        """Return the types whose encode and decode functions are called by other code.

        A struct of bit-fields only is written into the bit group of the struct that holds it,
        so a type that is used only that way never needs functions of its own.
        """
        called: set[str] = set()
        for item in [*self.project.types.values(), *self.project.messages.values()]:
            for field in item.fields:
                if field.type in self.project.types and self.group_bits(field) is None:
                    called.add(field.type)
        return called

    def has_functions(self, item: Struct) -> bool:
        """Whether a struct gets functions (init, validate, ...): messages and used types."""
        return item.message is not None or item.name in self.used_types

    def has_codec_functions(self, item: Struct) -> bool:
        """Whether a struct has its own encode and decode functions (messages and called types)."""
        return item.message is not None or item.name in self.called

    # --- helpers --------------------------------------------------------------------------------

    def _members(self, item: Struct) -> list[Field]:
        hidden = wire_only(item)
        return [field for field in item.fields if field.name not in hidden]

    def conditional(self, out: Writer, field: Field | Struct, lines: list[str]) -> None:
        """Write lines wrapped in the #if of the field."""
        if not lines:
            return
        condition = field.condition
        if condition is not None:
            out.line(f"#if {self.names.condition(condition)}")
        for text in lines:
            out.line(text)
        if condition is not None:
            out.line("#endif")

    def _enum_valid(self, type_name: str) -> str:
        return self.names.function(type_name, "is_valid")

    # --- model.h ------------------------------------------------------------------------------

    def render_header(self) -> str:
        """Return the text of model.h."""
        out = self.header
        project, names = self.project, self.names
        out.line("/**")
        out.line(f" * @file {project.name}/model.h")
        out.line(
            f" * @brief Types of the {project.name} messages: defaults, validation, codec (C99)."
        )
        out.line(" *")
        for text in self.banner.lines():
            out.line(text)
        out.line(" */")
        out.line(f"#ifndef {guard(project.name, 'MODEL_H')}")
        out.line(f"#define {guard(project.name, 'MODEL_H')}")
        out.line()
        out.line("#include <stdbool.h>")
        out.line("#include <stddef.h>")
        out.line("#include <stdint.h>")
        out.line()
        out.line('#include "wireforge/c/status.h"')
        out.line()
        out.line("struct wf_encoder;")
        out.line("struct wf_decoder;")
        out.line("struct wf_encoding;")
        out.line()
        self._header_defines()
        self._header_enums()
        for item in type_order(project):
            self._header_struct(item, "Type")
        for item in project.messages.values():
            self._header_struct(item, "Message")
        for checksum in project.checksums.values():
            out.line(
                f"/** Checksum {checksum.name} ({checksum.algo}) over [data, data + length). */"
            )
            function = names.checksum_function(checksum.name)
            out.line(f"uint64_t {function}(const uint8_t *data, size_t length);")
        if project.checksums:
            out.line()
        out.line(f"#endif /* {guard(project.name, 'MODEL_H')} */")
        return out.text()

    def _header_defines(self) -> None:
        out = self.header
        for define in self.project.defines.values():
            macro = self.names.define(define.name)
            out.line(f"#if defined({define.name})")
            out.line(f"#define {macro} {define.name}")
            out.line("#else")
            out.line(f"/** Value of define {define.name} (default {define.value}). */")
            out.line(f"#define {macro} {define.value}")
            out.line("#endif")
        if self.project.defines:
            out.line()
        for constant in self.project.constants.values():
            self._header_constant_check(constant.name)

    def _header_constant_check(self, name: str) -> None:
        """Check a user constant at compile time (a negative array size is a compile error)."""
        constant = self.project.constants[name]
        if constant.c is None:
            return
        out = self.header
        branches = [
            (f"defined(WIREFORGE_PROFILE_{profile})", value)
            for profile, value in constant.profile_values.items()
        ]
        if not branches and constant.value is None:
            return
        for index, (condition, value) in enumerate(branches):
            out.line(f"#{'if' if index == 0 else 'elif'} {condition}")
            out.line(self._constant_assert(constant.c, value))
        if constant.value is not None:
            if branches:
                out.line("#else")
            out.line(self._constant_assert(constant.c, constant.value))
        if branches:
            out.line("#endif")
        out.line()

    def _constant_assert(self, spelling: str, value: int) -> str:
        macro = self.names.macro(f"constant_{spelling}")
        return (
            f"typedef char {macro}[((int64_t)({spelling}) == INT64_C({value})) ? 1 : -1];"
            f" /* {spelling} differs from the value in the WireForge model */"
        )

    def _header_enums(self) -> None:
        out, names = self.header, self.names
        for enum in self.project.enums.values():
            type_name = names.tname(enum.name)
            out.line(f"/** Enumeration {enum.name}. */")
            out.line(f"typedef {names.scalar(enum.type)} {type_name};")
            signed = enum.type.startswith("i")
            for member, value in enum.values.items():
                literal = str(value) if signed or value < 0 else f"{value}U"
                out.line(f"#define {type_name}_{member} (({type_name})({literal}))")
            if enum.name in self.used_enums:
                out.line(f"/** true when @p value is a member of {enum.name}. */")
                out.line(f"bool {self._enum_valid(enum.name)}({type_name} value);")
            out.line()

    def _header_struct(self, item: Struct, kind: str) -> None:
        out, names = self.header, self.names
        name = names.tname(item.name)
        if item.condition is not None:
            out.line(f"#if {names.condition(item.condition)}")
        out.line(f"/** {_description(item.description, f'{kind} {item.name}.')} */")
        out.line(f"typedef struct {name} {{")
        out.indent()
        if all(member.condition is not None for member in self._members(item)):
            out.line("uint8_t unused_; /**< Placeholder: a C struct needs a member. */")
        for field in self._members(item):
            doc = _description(field.description, field.name)
            if field.units:
                doc += f" [{field.units}]"
            self.conditional(out, field, [f"{names.member_declaration(field)} /**< {doc} */"])
        out.dedent()
        out.line(f"}} {name};")
        out.line()
        self._header_sizes(item)
        if self.has_functions(item):
            self._header_prototypes(item)
        if item.condition is not None:
            out.line("#endif")
            out.line()

    # --- wire sizes -----------------------------------------------------------------------------

    def group_bits(self, item: Field) -> int | None:
        """Return the bits a field adds to a bit group, or None when it is byte-oriented."""
        if item.occurs is not None:
            return None
        if item.bits is not None:
            return item.bits
        inner = self.project.types.get(item.type)
        if inner is None or not inner.fields or any(f.bits is None for f in inner.fields):
            return None
        return sum(f.bits or 0 for f in inner.fields)

    def element_size(self, item: Field, which: str) -> str:
        """Return the C expression of the smallest or largest encoded size of one element."""
        if item.type in self.project.types:
            return f"{self.names.tname(item.type)}_{which.upper()}_WIRE_SIZE"
        return f"{BUILTIN_BITS[self.names.base(item.type)] // BYTE_BITS}U"

    def _field_size(self, item: Field, which: str) -> str:
        element = self.element_size(item, which)
        occurs = item.occurs
        if occurs is None:
            return element
        dynamic = occurs.count is not None or occurs.fill
        count = occurs.max if which == "max" or not dynamic else (occurs.min or 0)
        if count == 0:
            return "0U"
        if isinstance(count, int):
            return f"((size_t){count}U * {element})"
        return f"({self.names.capacity(count)} * {element})"

    def _size_lines(self, item: Struct, which: str) -> list[tuple[Field, str]]:
        lines: list[tuple[Field, str]] = []
        group: list[Field] = []
        for field in [*item.fields, None]:
            bits = None if field is None else self.group_bits(field)
            if bits is not None and field is not None:
                group.append(field)
                continue
            if group:
                total = sum(self.group_bits(member) or 0 for member in group)
                lines.append((group[0], f"{(total + BYTE_BITS - 1) // BYTE_BITS}U"))
                group = []
            if field is not None:
                lines.append((field, self._field_size(field, which)))
        return lines

    def _header_sizes(self, item: Struct) -> None:
        out, names = self.header, self.names
        for which, text in (("min", "Smallest"), ("max", "Largest")):
            macro = f"{names.tname(item.name)}_{which.upper()}_WIRE_SIZE"
            out.line(f"/** {text} encoded size of {item.name} in bytes. */")
            parts = [(field, size) for field, size in self._size_lines(item, which) if size != "0U"]
            if not any(field.condition is not None for field, _ in parts):
                total = " + ".join(size for _, size in parts) or "0U"
                out.line(f"#define {macro} ((size_t)({total}))")
                out.line()
                continue
            previous = "0U"
            for index, (field, size) in enumerate(parts):
                step = f"{macro}_{index}"
                if field.condition is not None:
                    out.line(f"#if {names.condition(field.condition)}")
                    out.line(f"#define {step} ({previous} + {size})")
                    out.line("#else")
                    out.line(f"#define {step} ({previous})")
                    out.line("#endif")
                else:
                    out.line(f"#define {step} ({previous} + {size})")
                previous = step
            out.line(f"#define {macro} ((size_t)({previous}))")
            out.line()

    def _header_prototypes(self, item: Struct) -> None:
        out, names = self.header, self.names
        type_name = names.tname(item.name)
        out.line(f"/** Sets @p value to the defaults of {item.name}. */")
        out.line(f"void {names.function(item.name, 'init')}({type_name} *value);")
        out.line("/** Checks ranges, enum values and element counts of @p value. */")
        out.line(f"wf_status {names.function(item.name, 'validate')}(const {type_name} *value);")
        if self.has_codec_functions(item):
            out.line("/** Appends the wire form of @p value to @p encoder. */")
            out.line(
                f"void {names.function(item.name, 'encode')}(const {type_name} *value, "
                "struct wf_encoder *encoder);"
            )
            out.line("/** Reads @p value from @p decoder. */")
            out.line(
                f"void {names.function(item.name, 'decode')}({type_name} *value, "
                "struct wf_decoder *decoder);"
            )
        for field in scaled_fields(item):
            getter, setter = function_names(item.name, field)
            owner = names.tname(item.name)
            out.line(f"/** Physical value of {item.name}.{field.name}: raw * scale + offset. */")
            self.conditional(
                out,
                field,
                [
                    f"double {names.prefix}_{getter}(const {owner} *value);",
                    f"bool {names.prefix}_{setter}({owner} *value, double physical);",
                ],
            )
        if item.message is not None:
            out.line(f"/** Encoding of {item.name} without interface overrides. */")
            out.line(f"extern const struct wf_encoding {type_name}_encoding;")
            out.line(
                "/** Encodes the payload of a message into @p buffer; @p size is 0 on error. */"
            )
            out.line(
                f"wf_status {names.function(item.name, 'serialize')}(const {type_name} *value, "
                "uint8_t *buffer, size_t capacity, size_t *size);"
            )
            out.line("/** The same with an explicit encoding (byte order, bit order, checks). */")
            function = names.function(item.name, "serialize_with")
            out.line(
                f"wf_status {function}(const {type_name} *value, uint8_t *buffer, "
                "size_t capacity, size_t *size, const struct wf_encoding *encoding);"
            )
            out.line("/** Decodes the payload of a message; @p value is partly filled on error. */")
            out.line(
                f"wf_status {names.function(item.name, 'deserialize')}(const uint8_t *payload, "
                f"size_t size, {type_name} *value);"
            )
            out.line("/** The same with an explicit encoding. */")
            function = names.function(item.name, "deserialize_with")
            out.line(
                f"wf_status {function}(const uint8_t *payload, size_t size, "
                f"{type_name} *value, const struct wf_encoding *encoding);"
            )
        out.line()

    # --- model.c --------------------------------------------------------------------------------

    def render_source(self) -> str:
        """Return the text of model.c."""
        out = self.source
        project = self.project
        out.line("/**")
        out.line(f" * @file {project.name}/model.c")
        out.line(f" * @brief Defaults, validation and codec of the {project.name} messages (C99).")
        out.line(" *")
        for text in self.banner.lines():
            out.line(text)
        out.line(" */")
        out.line(f'#include "{project.name}/model.h"')
        out.line()
        out.line('#include "wireforge/c/wireforge.h"')
        for header in sorted({c.c_include for c in project.checksums.values() if c.c_include}):
            out.line(f'#include "{header}"')
        out.line("#include <float.h>")
        out.line("#include <string.h>")
        out.line()
        for checksum in project.checksums.values():
            self._source_checksum(checksum)
        for enum in project.enums.values():
            if enum.name in self.used_enums:
                self._source_enum(enum.name)
        for item in type_order(project):
            self._source_struct(item)
        for item in project.messages.values():
            self._source_struct(item)
        return out.text()

    def _source_checksum(self, checksum: Checksum) -> None:
        out, names = self.source, self.names
        function = names.checksum_function(checksum.name)
        out.line(f"uint64_t {function}(const uint8_t *data, size_t length)")
        out.line("{")
        out.indent()
        if checksum.algo == "custom":
            out.line(f"return {checksum.c_impl}(data, length);")
        else:
            width, poly, init, refin, refout, xorout = (
                CRC32
                if checksum.algo == "crc32"
                else (
                    checksum.width or 0,
                    checksum.poly or 0,
                    checksum.init,
                    checksum.refin,
                    checksum.refout,
                    checksum.xorout,
                )
            )
            flags = f"{'true' if refin else 'false'}, {'true' if refout else 'false'}"
            out.line(
                f"static const wf_crc_params params = {{{width}U, {poly:#x}U, {init:#x}U, "
                f"{flags}, {xorout:#x}U}};"
            )
            out.line("return wf_crc(&params, data, length);")
        out.dedent()
        out.line("}")
        out.line()

    def _source_enum(self, name: str) -> None:
        out, names = self.source, self.names
        enum = self.project.enums[name]
        type_name = names.tname(name)
        out.line(f"bool {self._enum_valid(name)}({type_name} value)")
        out.line("{")
        out.indent()
        terms = [f"(int)(value == {type_name}_{member})" for member in enum.values]
        for position, term in enumerate(terms):
            prefix = "return (" if position == 0 else "       "
            suffix = ") != 0;" if position == len(terms) - 1 else " |"
            out.line(f"{prefix}{term}{suffix}")
        out.dedent()
        out.line("}")
        out.line()

    def _source_struct(self, item: Struct) -> None:
        out = self.source
        if not self.has_functions(item):
            return
        if item.condition is not None:
            out.line(f"#if {self.names.condition(item.condition)}")
        self._init(item)
        self._validate(item)
        if self.has_codec_functions(item):
            self._codec().encode_struct(item)
            self._codec().decode_struct(item)
        self._physical(item)
        if item.message is not None:
            self._message_api(item)
        if item.condition is not None:
            out.line("#endif")
            out.line()

    def _codec(self) -> _CCodec:
        return _CCodec(self)

    # --- defaults -------------------------------------------------------------------------------

    def _init_lines(self, field: Field) -> list[str]:
        """Return the statements that give one member its default."""
        names = self.names
        target = f"value->{field.name}"
        inner = self.project.types.get(field.type)
        if field.occurs is None:
            if inner is not None:
                return [f"{names.function(inner.name, 'init')}(&{target});"]
            default = field.default
            if default is None or isinstance(default, list):
                return []
            return [f"{target} = {names.literal(field, default)};"]
        if field.type == "char":
            return self._init_string(field, target)
        return self._init_array(field, target, inner)

    @staticmethod
    def _init_string(field: Field, target: str) -> list[str]:
        default = field.default
        if not isinstance(default, str):
            return []
        length = len(default.encode("latin-1"))
        return [
            f"(void)memcpy({target}, {string_literal(default)}, {length}U);",
            f"{target}_count = {length}U;",
        ]

    def _init_array(self, field: Field, target: str, inner: Struct | None) -> list[str]:
        names, occurs = self.names, field.occurs
        if occurs is None:
            return []
        counted = names.counted(field)
        default = field.default
        capacity = names.capacity(occurs.max)
        if inner is not None and not counted:
            init = names.function(inner.name, "init")
            return self._loop(capacity, f"{init}(&{target}[index]);")
        if isinstance(default, list):
            lines = [
                f"{target}[{index}] = {names.literal(field, value)};"
                for index, value in enumerate(default)
            ]
            if counted:
                lines.append(f"{target}_count = {len(default)}U;")
            return lines
        if default is None or counted:
            return []
        return self._loop(capacity, f"{target}[index] = {names.literal(field, default)};")

    @staticmethod
    def _loop(count: str, statement: str) -> list[str]:
        return [f"for (size_t index = 0U; index < {count}; ++index) {{", f"    {statement}", "}"]

    def _init(self, item: Struct) -> None:
        out, names = self.source, self.names
        type_name = names.tname(item.name)
        out.line(f"void {names.function(item.name, 'init')}({type_name} *value)")
        out.line("{")
        out.indent()
        out.line("(void)memset(value, 0, sizeof(*value));")
        for field in self._members(item):
            self.conditional(out, field, self._init_lines(field))
        out.dedent()
        out.line("}")
        out.line()

    # --- validate -------------------------------------------------------------------------------

    def _limits(self, item: Field) -> tuple[Value | None, Value | None]:
        """Return the min and max of a field that are tighter than its type."""
        low, high = item.min, item.max
        limits = self.names.type_limits(item)
        if limits is not None and isinstance(low, int) and low <= limits[0]:
            low = None
        if limits is not None and isinstance(high, int) and high >= limits[1]:
            high = None
        return low, high

    def _bound_checks(self, item: Field, subject: str, members: set[str]) -> list[str]:
        names = self.names
        base = names.base(item.type)
        low, high = self._limits(item)
        if base in {"bool", "char"} or (low is None and high is None):
            return []
        if base in FLOAT_TYPES:
            value, suffix, cast = f"((double)({subject}))", "f", "(double)"
        elif base == "u64":
            value, suffix, cast = f"((uint64_t)({subject}))", "u", ""
        else:
            value, suffix, cast = f"((int64_t)({subject}))", "s", ""

        def bound(raw: Value) -> str:
            text = names.bound(item, raw, members)
            return f"({cast}({text}))" if cast else text

        if low is not None and high is not None:
            return [f"wf_check_range_{suffix}({value}, {bound(low)}, {bound(high)})"]
        if low is not None:
            return [f"wf_check_min_{suffix}({value}, {bound(low)})"]
        return [f"wf_check_max_{suffix}({value}, {bound(high)})"] if high is not None else []

    def value_checks(self, item: Field, subject: str, members: set[str]) -> list[str]:
        """Return the checks of one scalar value (`subject` is its C expression)."""
        names = self.names
        checks: list[str] = []
        if item.type in self.project.enums:
            checks.append(f"wf_check_true({self._enum_valid(item.type)}({subject}))")
        base = names.base(item.type)
        if item.bits is not None and item.bits < BUILTIN_BITS[base] and base != "bool":
            if base in SIGNED_TYPES:
                checks.append(f"wf_check_bits_s(((int64_t)({subject})), {item.bits}U)")
            else:
                checks.append(f"wf_check_bits_u(((uint64_t)({subject})), {item.bits}U)")
        checks += self._bound_checks(item, subject, members)
        return checks

    def _field_checks(self, item: Field, owner: Struct, members: set[str]) -> list[str]:
        """Return the statements (each assigns `status`) that check one field."""
        names = self.names
        subject = f"value->{item.name}"
        if item.occurs is None:
            if names.is_struct(item):
                return [self._chain(f"{names.function(item.type, 'validate')}(&{subject})")]
            return [self._chain(check) for check in self.value_checks(item, subject, members)]
        lines = [self._chain(check) for check in self._count_checks(item, owner, members)]
        if item.type == "char":
            return lines
        count = f"{subject}_count" if names.counted(item) else names.capacity(item.occurs.max)
        if names.is_struct(item):
            body = [self._chain(f"{names.function(item.type, 'validate')}(&{subject}[index])")]
        else:
            body = [
                self._chain(check)
                for check in self.value_checks(item, f"{subject}[index]", members)
            ]
        if body:
            lines += [f"for (size_t index = 0U; index < {count}; ++index) {{", *body, "}"]
        return lines

    @staticmethod
    def _chain(check: str) -> str:
        return f"status = wf_first_error(status, {check});"

    def _count_checks(self, item: Field, owner: Struct, members: set[str]) -> list[str]:
        occurs = item.occurs
        if occurs is None or (occurs.count is None and not occurs.fill):
            return []
        names = self.names
        size = f"value->{item.name}_count"
        checks = []
        low = occurs.min or 0
        if low != 0:
            checks.append(
                f"wf_check_count({size}, {names.capacity(low)}, {names.capacity(occurs.max)})"
            )
        counter = plain_count(item)
        if counter is None and occurs.count is not None:
            expected = names.value(occurs.count, members)
            checks.append(f"wf_check_count_equals({size}, {expected})")
        elif counter is not None and counter not in members:
            checks += self._counter_checks(item, owner, counter, size)
        return checks

    def _counter_checks(self, item: Field, owner: Struct, counter: str, size: str) -> list[str]:
        """Range of a wire-only count field, and equal sizes of arrays sharing it."""
        target = next(field for field in owner.fields if field.name == counter)
        checks = self._bound_checks(target, size, set())
        first = next(field for field in owner.fields if plain_count(field) == counter)
        if first.name != item.name:
            checks.append(f"wf_check_count_equals({size}, ((int64_t)(value->{first.name}_count)))")
        return checks

    def _validate(self, item: Struct) -> None:
        out, names = self.source, self.names
        type_name = names.tname(item.name)
        members = {field.name for field in self._members(item)}
        out.line(f"wf_status {names.function(item.name, 'validate')}(const {type_name} *value)")
        out.line("{")
        out.indent()
        out.line("wf_status status = WF_OK;")
        used = False
        for field in self._members(item):
            lines = self._field_checks(field, item, members)
            used = used or bool(lines)
            self.conditional(out, field, lines)
        if not used:
            out.line("(void)value;")
        out.line("return status;")
        out.dedent()
        out.line("}")
        out.line()

    # --- physical values ------------------------------------------------------------------------

    def _physical(self, item: Struct) -> None:
        out, names = self.source, self.names
        owner = names.tname(item.name)
        for field in scaled_fields(item):
            getter, setter = function_names(item.name, field)
            scale, offset = repr(scale_of(field)), repr(offset_of(field))
            member = f"value->{field.name}"
            if is_float(field):
                largest = "((double)FLT_MAX)" if field.type == "f32" else "DBL_MAX"
                checks = f"raw >= -{largest} && raw <= {largest}"
                store = "raw"
            else:
                checks = f"{lower_check(field)} && {upper_check(field)}"
                store = "raw < 0.0 ? raw - 0.5 : raw + 0.5"
            kind = names.scalar(field.type)
            lines = [
                f"double {names.prefix}_{getter}(const {owner} *value)",
                "{",
                f"    return (double)({member}) * ({scale}) + ({offset});",
                "}",
                "",
                f"bool {names.prefix}_{setter}({owner} *value, double physical)",
                "{",
                f"    const double raw = (physical - ({offset})) / ({scale});",
                f"    const bool fits = {checks};",
                "    if (!fits) {",
                "        return false;",
                "    }",
                f"    {member} = ({kind})({store});",
                "    return true;",
                "}",
                "",
            ]
            self.conditional(out, field, lines)

    # --- messages -------------------------------------------------------------------------------

    def _message_api(self, item: Struct) -> None:
        out, names = self.source, self.names
        type_name = names.tname(item.name)
        init = encoding_initializer(self.project, item)
        out.line(f"const wf_encoding {type_name}_encoding = {init};")
        out.line()
        function = names.function(item.name, "serialize_with")
        out.line(
            f"wf_status {function}(const {type_name} *value, uint8_t *buffer, size_t capacity, "
            "size_t *size, const wf_encoding *encoding)"
        )
        out.line("{")
        out.indent()
        out.line("wf_encoder encoder;")
        out.line("wf_encoder_init(&encoder, buffer, capacity, encoding);")
        out.line(f"{names.function(item.name, 'encode')}(value, &encoder);")
        out.line("*size = wf_encoder_size(&encoder);")
        out.line("return encoder.status;")
        out.dedent()
        out.line("}")
        out.line()
        out.line(
            f"wf_status {names.function(item.name, 'serialize')}(const {type_name} *value, "
            "uint8_t *buffer, size_t capacity, size_t *size)"
        )
        out.line("{")
        out.indent()
        out.line(f"return {function}(value, buffer, capacity, size, &{type_name}_encoding);")
        out.dedent()
        out.line("}")
        out.line()
        function = names.function(item.name, "deserialize_with")
        out.line(
            f"wf_status {function}(const uint8_t *payload, size_t size, {type_name} *value, "
            "const wf_encoding *encoding)"
        )
        out.line("{")
        out.indent()
        out.line("wf_decoder decoder;")
        out.line("wf_decoder_init(&decoder, payload, size, encoding);")
        out.line(f"{names.function(item.name, 'decode')}(value, &decoder);")
        out.line("wf_dec_finish(&decoder);")
        out.line("return decoder.status;")
        out.dedent()
        out.line("}")
        out.line()
        out.line(
            f"wf_status {names.function(item.name, 'deserialize')}(const uint8_t *payload, "
            f"size_t size, {type_name} *value)"
        )
        out.line("{")
        out.indent()
        out.line(f"return {function}(payload, size, value, &{type_name}_encoding);")
        out.dedent()
        out.line("}")
        out.line()


class _CCodec:
    """Writes `<prefix>_<Type>_encode` and `_decode` of one struct (same rules as cpp_codec.py)."""

    def __init__(self, model: CModel) -> None:
        """Bind to the model whose source is written."""
        self.model = model
        self.project = model.project
        self.names = model.names
        self.out = model.source

    # --- shared helpers -------------------------------------------------------------------------

    def _width(self, item: Field) -> int:
        return BUILTIN_BITS[self.names.base(item.type)] // BYTE_BITS

    def _zero_literal(self, item: Field) -> str:
        if item.default is not None and not isinstance(item.default, list):
            return self.names.literal(item, item.default)
        if item.type in self.project.enums:
            return f"(({self.names.tname(item.type)})(0))"
        if item.type == "bool":
            return "false"
        return self.names.literal(item, 0.0 if item.type in FLOAT_TYPES else 0)

    def _const_literal(self, item: Field) -> str:
        if item.const is None:
            return self._zero_literal(item)
        return self.names.literal(item, item.const)

    def _group_bits(self, item: Field) -> int | None:
        return self.model.group_bits(item)

    def _groups(self, item: Struct) -> dict[str, list[_Leaf]]:
        """Bit groups keyed by the name of their first field."""
        result: dict[str, list[_Leaf]] = {}
        current: list[_Leaf] | None = None
        hidden = wire_only(item)
        for field in item.fields:
            if self._group_bits(field) is None:
                current = None
                continue
            if current is None:
                current = result.setdefault(field.name, [])
            current += self._leaves(field, hidden)
        return result

    def _leaves(self, field: Field, hidden: set[str]) -> list[_Leaf]:
        if field.bits is not None:
            return [_Leaf(field, "" if field.name in hidden else f"value->{field.name}")]
        inner = self.project.types[field.type]
        inner_hidden = wire_only(inner)
        return [
            _Leaf(leaf, "" if leaf.name in inner_hidden else f"value->{field.name}.{leaf.name}")
            for leaf in inner.fields
        ]

    def _positions(self, item: Struct) -> tuple[set[str], set[str], bool]:
        """Fields whose start / end offsets checksums need, and whether `start` is needed."""
        starts: set[str] = set()
        ends: set[str] = set()
        preceding = False
        for field in item.fields:
            use = field.checksum
            if use is None:
                continue
            if use.first is not None and use.last is not None:
                starts.add(use.first)
                ends.add(use.last)
            preceding = preceding or use.scope == "preceding"
        return starts, ends, preceding

    def _inline_bits(self, field: Field) -> bool:
        return field.bits is None and self._group_bits(field) is not None

    def _conditional(self, field: Field, lines: list[str]) -> None:
        self.model.conditional(self.out, field, lines)

    @staticmethod
    def _block(header: str, body: list[str]) -> list[str]:
        return [f"{header} {{", *[f"    {line}" for line in body], "}"]

    def _checksum_span(self, field: Field, coder: str) -> tuple[str, str]:
        use = field.checksum
        if use is not None and use.first is not None and use.last is not None:
            return f"at_{use.first}", f"end_{use.last}"
        side = "encoder" if coder == "encoder" else "decoder"
        return "start", f"wf_{side}_position({coder})"

    def _emit_fields(
        self,
        item: Struct,
        side: str,
        statements: Callable[[Field, Struct, set[str]], list[str]],
    ) -> None:
        """Emit the statements of every field (bit groups once), wrapping #if and offsets."""
        out = self.out
        coder = "encoder" if side == "encode" else "decoder"
        position = f"wf_{coder}_position({coder})"
        hidden = wire_only(item)
        members = {field.name for field in item.fields if field.name not in hidden}
        starts, ends, preceding = self._positions(item)
        groups = self._groups(item)
        in_group = {leaf.field.name for leaves in groups.values() for leaf in leaves}
        if preceding:
            out.line(f"const size_t start = {position};")
        uses_value = False
        for field in item.fields:
            lines: list[str] = []
            if field.name in starts:
                lines.append(f"const size_t at_{field.name} = {position};")
            if field.name in groups:
                lines += self._group(groups[field.name], side, members)
                uses_value = uses_value or any(leaf.subject for leaf in groups[field.name])
            elif field.name not in in_group and not self._inline_bits(field):
                lines += statements(field, item, members)
                uses_value = uses_value or field.name in members or plain_count(field) is not None
            if field.name in ends:
                lines.append(f"const size_t end_{field.name} = {position};")
            self._conditional(field, lines)
        if not uses_value:
            out.line("(void)value;")

    # --- wire values ----------------------------------------------------------------------------

    def _write(self, item: Field, expression: str) -> str:
        base = self.names.base(item.type)
        if base in FLOAT_TYPES:
            return f"wf_enc_{base}(encoder, {expression});"
        if base == "bool":
            return f"wf_enc_bool(encoder, {expression});"
        if base == "char":
            return f"wf_enc_unsigned(encoder, (uint64_t)(uint8_t)({expression}), 1U);"
        if base in SIGNED_TYPES:
            return f"wf_enc_signed(encoder, (int64_t)({expression}), {self._width(item)}U);"
        return f"wf_enc_unsigned(encoder, (uint64_t)({expression}), {self._width(item)}U);"

    def _read(self, item: Field) -> str:
        base = self.names.base(item.type)
        ctype = self.names.scalar(item.type)
        if base in FLOAT_TYPES:
            return f"wf_dec_{base}(decoder)"
        if base == "bool":
            return "wf_dec_bool(decoder)"
        if base == "char":
            return "(char)(uint8_t)wf_dec_unsigned(decoder, 1U)"
        if base in SIGNED_TYPES:
            return f"({ctype})wf_dec_signed(decoder, {self._width(item)}U)"
        return f"({ctype})wf_dec_unsigned(decoder, {self._width(item)}U)"

    def _expect(self, item: Field, literal: str) -> str:
        base = self.names.base(item.type)
        if base in FLOAT_TYPES:
            return f"wf_dec_expect_{base}(decoder, {literal});"
        if base == "bool":
            return f"wf_dec_expect_bool(decoder, {literal});"
        if base == "char":
            return f"wf_dec_expect_unsigned(decoder, (uint64_t)(uint8_t)({literal}), 1U);"
        if base in SIGNED_TYPES:
            return f"wf_dec_expect_signed(decoder, (int64_t)({literal}), {self._width(item)}U);"
        return f"wf_dec_expect_unsigned(decoder, (uint64_t)({literal}), {self._width(item)}U);"

    @staticmethod
    def _count_limit(base: str) -> str:
        if base == "bool":
            return "1U"
        bits = BUILTIN_BITS[base]
        if base in SIGNED_TYPES:
            return f"UINT64_C({(1 << (bits - 1)) - 1})"
        return f"UINT64_C({(1 << bits) - 1})"

    # --- encode ---------------------------------------------------------------------------------

    def encode_struct(self, item: Struct) -> None:
        """Emit `<prefix>_<Type>_encode`."""
        out, names = self.out, self.names
        type_name = names.tname(item.name)
        function = names.function(item.name, "encode")
        out.line(f"void {function}(const {type_name} *value, wf_encoder *encoder)")
        out.line("{")
        out.indent()
        self._emit_fields(item, "encode", self._encode_field)
        out.dedent()
        out.line("}")
        out.line()

    def _encode_field(self, field: Field, owner: Struct, members: set[str]) -> list[str]:
        if field.checksum is not None:
            begin, end = self._checksum_span(field, "encoder")
            checksum = self.project.checksums[field.checksum.use]
            function = self.names.checksum_function(checksum.name)
            size = checksum_size(checksum)
            return [f"wf_enc_checksum(encoder, {begin}, {end}, {function}, {size}U);"]
        if field.const is not None or field.reserved:
            return [self._write(field, self._const_literal(field))]
        if field.name not in members:
            return self._encode_count(field, owner)
        subject = f"value->{field.name}"
        if field.occurs is None:
            return self._encode_value(field, subject, members)
        return self._encode_occurs(field, subject, members)

    def _count_owner(self, item: Struct, counter: str) -> Field:
        return next(field for field in item.fields if plain_count(field) == counter)

    def _encode_count(self, field: Field, owner: Struct) -> list[str]:
        array = self._count_owner(owner, field.name)
        size = f"value->{array.name}_count"
        occurs = array.occurs
        low = self.names.capacity(occurs.min or 0) if occurs is not None else "0U"
        high = self.names.capacity(occurs.max) if occurs is not None else "0U"
        lines = [f"wf_enc_check(encoder, wf_check_count({size}, {low}, {high}));"]
        lines += [
            f"wf_enc_check_value(encoder, {check});"
            for check in self.model.value_checks(field, size, set())
        ]
        base = self.names.base(field.type)
        lines.append(
            f"wf_enc_count(encoder, {size}, {self._count_limit(base)}, {self._width(field)}U);"
        )
        return lines

    def _encode_value(self, field: Field, subject: str, members: set[str]) -> list[str]:
        if self.names.is_struct(field):
            return [f"{self.names.function(field.type, 'encode')}(&{subject}, encoder);"]
        checks = [
            f"wf_enc_check_value(encoder, {check});"
            for check in self.model.value_checks(field, subject, members)
        ]
        return [*checks, self._write(field, subject)]

    def _encode_occurs(self, field: Field, subject: str, members: set[str]) -> list[str]:
        occurs = field.occurs
        if occurs is None:
            return []
        names = self.names
        lines: list[str] = []
        dynamic = occurs.count is not None or occurs.fill
        size = f"{subject}_count"
        if dynamic and (occurs.min or 0) != 0:
            low, high = names.capacity(occurs.min or 0), names.capacity(occurs.max)
            lines.append(f"wf_enc_check(encoder, wf_check_count({size}, {low}, {high}));")
        if occurs.count is not None and plain_count(field) is None:
            expected = names.value(occurs.count, members)
            lines.append(f"wf_enc_check(encoder, wf_check_count_equals({size}, {expected}));")
        if field.type == "char":
            width = size if dynamic else names.capacity(occurs.max)
            return [*lines, f"wf_enc_text(encoder, {subject}, {size}, {width});"]
        count = size if names.counted(field) else names.capacity(occurs.max)
        body = self._encode_value(field, f"{subject}[index]", members)
        return [*lines, *self._block(f"for (size_t index = 0U; index < {count}; ++index)", body)]

    def _group(self, leaves: list[_Leaf], side: str, members: set[str]) -> list[str]:
        total = sum(leaf.field.bits or 0 for leaf in leaves)
        if side == "encode":
            body = ["wf_bit_packer group;", f"wf_enc_group(encoder, &group, {total}U);"]
            for leaf in leaves:
                field = leaf.field
                signed = self.names.base(field.type) in SIGNED_TYPES
                put = "wf_enc_put_signed" if signed else "wf_enc_put"
                cast = "int64_t" if signed else "uint64_t"
                if not leaf.subject:
                    literal = self._const_literal(field)
                    body.append(f"{put}(encoder, &group, ({cast})({literal}), {field.bits}U);")
                    continue
                checks = self.model.value_checks(field, leaf.subject, members)
                body += [f"wf_enc_check_value(encoder, {check});" for check in checks]
                body.append(f"{put}(encoder, &group, ({cast})({leaf.subject}), {field.bits}U);")
            body.append("wf_enc_write_bits(encoder, &group);")
        else:
            body = ["wf_bit_unpacker group;", f"wf_dec_group(decoder, &group, {total}U);"]
            for leaf in leaves:
                body += self._decode_leaf(leaf, members)
        return ["{", *[f"    {line}" for line in body], "}"]

    # --- decode ---------------------------------------------------------------------------------

    def _decode_leaf(self, leaf: _Leaf, members: set[str]) -> list[str]:
        field = leaf.field
        base = self.names.base(field.type)
        signed = base in SIGNED_TYPES
        if field.const is not None:
            literal = self._const_literal(field)
            if signed:
                call = f"(int64_t)({literal}), {field.bits}U"
                return [f"wf_dec_expect_bits_signed(decoder, &group, {call});"]
            return [f"wf_dec_expect_bits(decoder, &group, (uint64_t)({literal}), {field.bits}U);"]
        if not leaf.subject:
            return [f"wf_dec_skip_bits(decoder, &group, {field.bits}U);"]
        ctype = self.names.scalar(field.type)
        if base == "bool":
            line = f"{leaf.subject} = wf_dec_get_bool(decoder, &group, {field.bits}U);"
        elif signed:
            line = f"{leaf.subject} = ({ctype})wf_dec_get_signed(decoder, &group, {field.bits}U);"
        else:
            line = f"{leaf.subject} = ({ctype})wf_dec_get(decoder, &group, {field.bits}U);"
        checks = self.model.value_checks(field, leaf.subject, members)
        return [line, *[f"wf_dec_check_value(decoder, {check});" for check in checks]]

    def decode_struct(self, item: Struct) -> None:
        """Emit `<prefix>_<Type>_decode`."""
        out, names = self.out, self.names
        type_name = names.tname(item.name)
        out.line(
            f"void {names.function(item.name, 'decode')}({type_name} *value, wf_decoder *decoder)"
        )
        out.line("{")
        out.indent()
        self._emit_fields(item, "decode", self._decode_field)
        out.dedent()
        out.line("}")
        out.line()

    def _decode_field(self, field: Field, _owner: Struct, members: set[str]) -> list[str]:
        names = self.names
        if field.checksum is not None:
            begin, end = self._checksum_span(field, "decoder")
            checksum = self.project.checksums[field.checksum.use]
            function = names.checksum_function(checksum.name)
            size = checksum_size(checksum)
            return [f"wf_dec_verify_checksum(decoder, {begin}, {end}, {function}, {size}U);"]
        if field.const is not None:
            return [self._expect(field, self._const_literal(field))]
        if field.reserved:
            return [f"wf_dec_skip(decoder, {self._width(field)}U);"]
        if field.name not in members:
            count = f"count_{field.name}"
            base = names.base(field.type)
            if base == "bool":
                read = "wf_dec_count_bool(decoder)"
            else:
                read = f"wf_dec_count(decoder, {self._width(field)}U)"
            lines = [f"const size_t {count} = {read};"]
            checks = self.model.value_checks(field, count, set())
            return lines + [f"wf_dec_check_value(decoder, {check});" for check in checks]
        subject = f"value->{field.name}"
        if field.occurs is None:
            return self._decode_value(field, subject, members)
        return self._decode_occurs(field, subject, members)

    def _decode_value(self, field: Field, subject: str, members: set[str]) -> list[str]:
        if self.names.is_struct(field):
            return [f"{self.names.function(field.type, 'decode')}(&{subject}, decoder);"]
        checks = [
            f"wf_dec_check_value(decoder, {check});"
            for check in self.model.value_checks(field, subject, members)
        ]
        return [f"{subject} = {self._read(field)};", *checks]

    def _decode_count(self, field: Field, members: set[str]) -> str | None:
        occurs = field.occurs
        if occurs is None or not (occurs.count is not None or occurs.fill):
            return None
        if occurs.fill:
            element = self.model.element_size(field, "max")
            return f"wf_dec_fill_count(decoder, {element})"
        counter = plain_count(field)
        if counter is not None and counter not in members:
            return f"count_{counter}"
        expected = self.names.value(occurs.count or 0, members)
        return f"((size_t)({expected}))"

    def _decode_occurs(self, field: Field, subject: str, members: set[str]) -> list[str]:
        occurs = field.occurs
        if occurs is None:
            return []
        names = self.names
        count = self._decode_count(field, members)
        low = names.capacity(occurs.min or 0)
        capacity = names.capacity(occurs.max)
        if field.type == "char":
            if count is None:
                arguments = f"{subject}, {capacity}, &{subject}_count, {capacity}, true"
                return [f"wf_dec_text(decoder, {arguments});"]
            return [
                f"wf_dec_text(decoder, {subject}, {capacity}, &{subject}_count, {count}, false);"
            ]
        lines = []
        if count is not None:
            lines.append(f"wf_dec_resize(decoder, &{subject}_count, {count}, {low}, {capacity});")
        limit = f"{subject}_count" if names.counted(field) else capacity
        body = self._decode_value(field, f"{subject}[index]", members)
        if names.counted(field) and names.is_struct(field):
            # An element of a counted array starts with the defaults of its type, as in C++.
            init = names.function(field.type, "init")
            body = [f"{init}(&{subject}[index]);", *body]
        return [*lines, *self._block(f"for (size_t index = 0U; index < {limit}; ++index)", body)]
