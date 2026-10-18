"""Names, types, literals and expressions of generated C code.

The C code of a project uses one prefix for everything that is global: the namespace of the project
with `::` replaced by `_` (`gnss::ubx` gives `gnss_ubx_`), so that two projects can be linked into
one program. Types are `<prefix>_<Name>`, functions `<prefix>_<Name>_<function>`, macros
`<prefix>_<NAME>`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wireforge import expr
from wireforge.cpp import Names, float_literal, int_literal
from wireforge.model import FLOAT_TYPES, INTEGER_TYPES, SIGNED_TYPES, Field, Project, Scalar, Value

if TYPE_CHECKING:
    from wireforge.model import Struct

INT64_LIMIT = 2**63
C_SCALARS = {
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u32": "uint32_t",
    "u64": "uint64_t",
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "bool": "bool",
    "char": "char",
    "f32": "float",
    "f64": "double",
}
# Words of the C language (and of the headers generated code includes) that a field must not use.
C_KEYWORDS = frozenset(
    {
        "auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else",
        "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long", "register",
        "restrict", "return", "short", "signed", "sizeof", "static", "struct", "switch",
        "typedef", "union", "unsigned", "void", "volatile", "while", "bool", "true", "false",
    }
)  # fmt: skip


class CGenerationError(Exception):
    """The project uses something generated C cannot express yet."""


def prefix_of(project: Project) -> str:
    """Return the global prefix of the C names of a project."""
    return (project.namespace or project.name).replace("::", "_")


class CNames(Names):
    """C names and expressions of one project (the C++ ones are `wireforge.cpp.Names`)."""

    def __init__(self, project: Project) -> None:
        """Index the project and build the prefix."""
        super().__init__(project)
        self.prefix = prefix_of(project)

    # --- names ----------------------------------------------------------------------------------

    def tname(self, name: str) -> str:
        """Return the global C name of a type, enum or message."""
        return f"{self.prefix}_{name}"

    def macro(self, name: str) -> str:
        """Return the global name of a macro."""
        return f"{self.prefix}_{name}"

    def function(self, owner: str, name: str) -> str:
        """Return the global name of a function of a type."""
        return f"{self.prefix}_{owner}_{name}"

    def checksum_function(self, name: str) -> str:
        """Return the global name of the function of a checksum declaration."""
        return f"{self.prefix}_checksum_{name}"

    # --- types ----------------------------------------------------------------------------------

    def scalar(self, type_name: str) -> str:
        """Return the C type of one element of a field."""
        if type_name in self.project.user_types:
            message = f"user type {type_name} is not supported in generated C"
            raise CGenerationError(message)
        if type_name in C_SCALARS:
            return C_SCALARS[type_name]
        return self.tname(type_name)

    def member_declaration(self, item: Field) -> str:
        """Return the declaration of a struct member, with the count member of dynamic storage."""
        element = self.scalar(item.type)
        occurs = item.occurs
        if occurs is None:
            return f"{element} {item.name};"
        capacity = self.capacity(occurs.max)
        text = f"{element} {item.name}[{capacity}];"
        if self.counted(item):
            text += f" size_t {item.name}_count;"
        return text

    def counted(self, item: Field) -> bool:
        """Whether the array of a field has a count member (variable size, or a string)."""
        occurs = item.occurs
        if occurs is None:
            return False
        return occurs.count is not None or occurs.fill or item.type == "char"

    # --- expressions ----------------------------------------------------------------------------

    def define(self, name: str) -> str:
        """Return the macro holding a define (overridable with -D<NAME>=<value>)."""
        return self.macro(name)

    def constant(self, name: str) -> str:
        """Return the C spelling of a user constant."""
        constant = self.project.constants[name]
        if constant.c is not None:
            return constant.c
        if constant.value is not None and not constant.profile_values:
            return int_literal(constant.value, unsigned=False)
        message = f"constant {name} needs a `c` spelling to be used in generated C"
        raise CGenerationError(message)

    def _name(self, name: str, fields: set[str]) -> str:
        if name in fields:
            return f"((int64_t)(value->{name}))"
        if name in self.project.defines:
            return f"((int64_t)({self.define(name)}))"
        if name in self.project.constants:
            return f"((int64_t)({self.constant(name)}))"
        return f"((int64_t)({name}))"

    def value(self, raw: Value, fields: set[str] | None = None) -> str:
        """Return an int64_t C expression (field names refer to members of `value`)."""
        if isinstance(raw, int) and not isinstance(raw, bool):
            return int_literal(raw, unsigned=False) if raw < INT64_LIMIT else f"{raw}U"
        known = fields or set()
        naming = expr.CNaming(
            name=lambda name: self._name(name, known),
            member=lambda enum, member: f"((int64_t)({self.tname(enum)}_{member}))",
            size_of=lambda type_name: f"((int64_t)({self.tname(type_name)}_MAX_WIRE_SIZE))",
        )
        if isinstance(raw, float):
            message = f"{raw} is not an integer expression"
            raise TypeError(message)
        return expr.to_c(expr.as_expression(raw), naming)

    def capacity(self, raw: Value) -> str:
        """Return a size_t constant expression (array capacities)."""
        if isinstance(raw, int) and not isinstance(raw, bool):
            return f"{raw}U"
        return f"((size_t)({self.value(raw)}))"

    # --- literals -------------------------------------------------------------------------------

    def literal(self, item: Field, value: Scalar, _scope: str = "") -> str:
        """Return the C literal of one element value of a field."""
        enum = self.project.enums.get(item.type)
        if enum is not None:
            if isinstance(value, str):
                return self.tname(f"{item.type}_{value}")
            return f"(({self.tname(item.type)})({value}))"
        if item.type == "bool":
            return "true" if value is True or value == 1 else "false"
        if item.type == "char":
            code = ord(value) if isinstance(value, str) else int(value)
            return f"((char)({code}))"
        if item.type in FLOAT_TYPES:
            return float_literal(float(value), item.type)
        return int_literal(int(value), unsigned=item.type not in SIGNED_TYPES)

    def is_signed(self, item: Field) -> bool:
        """Whether the wire type of a field is a signed integer."""
        return self.base(item.type) in SIGNED_TYPES

    def is_float(self, item: Field) -> bool:
        """Whether the wire type of a field is a floating-point number."""
        return self.base(item.type) in FLOAT_TYPES

    def is_integer(self, item: Field) -> bool:
        """Whether the wire type of a field is an integer (an enum counts by its type)."""
        return self.base(item.type) in INTEGER_TYPES

    def struct_of(self, item: Field) -> Struct:
        """Return the struct a field is made of."""
        return self.project.types[item.type]


def used_names(project: Project) -> tuple[set[str], set[str]]:
    """Return the types and enums that a message or an interface uses, directly or not.

    Only they get functions: code for a type that nothing uses could not be tested, and the
    generated code is held to 100 percent coverage.
    """
    types: set[str] = set()
    enums: set[str] = set()
    pending = [field for item in project.messages.values() for field in item.fields]
    for interface in project.interfaces.values():
        pending += [*(interface.header or ()), *interface.trailer]
    while pending:
        field = pending.pop()
        if field.type in project.enums:
            enums.add(field.type)
        elif field.type in project.types and field.type not in types:
            types.add(field.type)
            pending += project.types[field.type].fields
    return types, enums
