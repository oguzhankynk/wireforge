"""In-memory project model built by `wireforge.loader`.

The model keeps the values as written (expressions stay strings) together with their source
location, so the validator and later stages can report `file:line: pointer` for every element.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wireforge.issues import Issue

# Wire width in bits of every builtin type.
BUILTIN_BITS = {
    "bool": 8,
    "char": 8,
    "u8": 8,
    "u16": 16,
    "u32": 32,
    "u64": 64,
    "i8": 8,
    "i16": 16,
    "i32": 32,
    "i64": 64,
    "f32": 32,
    "f64": 64,
}
UNSIGNED_TYPES = frozenset({"u8", "u16", "u32", "u64"})
SIGNED_TYPES = frozenset({"i8", "i16", "i32", "i64"})
INTEGER_TYPES = UNSIGNED_TYPES | SIGNED_TYPES
FLOAT_TYPES = frozenset({"f32", "f64"})

# Raw value of a model attribute that accepts a number or an expression string.
Value = int | float | str
Scalar = int | float | bool | str


@dataclass(frozen=True)
class Source:
    """Where an element was written."""

    file: str
    line: int | None
    pointer: str

    def issue(self, rule: str, message: str, suffix: str = "") -> Issue:
        """Error located at this element (optionally at a sub-pointer)."""
        return Issue(self.file, self.line, self.pointer + suffix, f"[{rule}] {message}")


@dataclass(frozen=True)
class Occurs:
    """How often a field occurs: optional (max 1), fixed or counted array, or payload fill."""

    max: Value
    min: Value | None = None
    count: Value | None = None
    fill: bool = False


@dataclass(frozen=True)
class ChecksumUse:
    """`checksum: {use, covers}` of a field."""

    use: str
    scope: str | None = None
    first: str | None = None
    last: str | None = None


@dataclass(frozen=True)
class Field:
    """One field of a struct, message, header or trailer."""

    name: str
    type: str
    source: Source
    bits: int | None = None
    min: Value | None = None
    max: Value | None = None
    default: Scalar | list[Scalar] | None = None
    const: Scalar | None = None
    reserved: bool = False
    occurs: Occurs | None = None
    checksum: ChecksumUse | None = None
    role: str | None = None
    condition: str | None = None
    description: str | None = None
    units: str | None = None


@dataclass(frozen=True)
class MessageInfo:
    """Attributes only messages carry."""

    id: int | None = None
    endian: str | None = None
    bit_order: str | None = None
    range_check: dict[str, bool] | None = None


@dataclass(frozen=True)
class Struct:
    """A `types` entry, or a message when `message` is set."""

    name: str
    fields: tuple[Field, ...]
    source: Source
    condition: str | None = None
    message: MessageInfo | None = None
    description: str | None = None


@dataclass(frozen=True)
class Enum:
    """Enumeration with an integer underlying type."""

    name: str
    type: str
    values: dict[str, int]
    source: Source


@dataclass(frozen=True)
class Checksum:
    """Checksum algorithm declaration."""

    name: str
    algo: str
    source: Source
    width: int | None = None
    poly: int | None = None
    init: int = 0
    refin: bool = False
    refout: bool = False
    xorout: int = 0
    check: int | None = None
    impl: str | None = None
    include: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class Framing:
    """Framing of one interface."""

    kind: str = "none"
    frame_size: Value | None = None
    max_frame_size: Value | None = None
    length_counts: str = "payload"
    resync: bool = False


@dataclass(frozen=True)
class InterfaceMessage:
    """Entry of an interface `messages` list."""

    name: str
    source: Source
    id: int | None = None
    condition: str | None = None


@dataclass(frozen=True)
class Interface:
    """One communication endpoint. `header` is None for `header: none`."""

    name: str
    version: str
    direction: str
    source: Source
    messages: tuple[InterfaceMessage, ...]
    header: tuple[Field, ...] | None = None
    trailer: tuple[Field, ...] = ()
    framing: Framing = field(default_factory=Framing)
    buffers: dict[str, Value] = field(default_factory=dict)
    endian: str | None = None
    bit_order: str | None = None
    allow_trailing_bytes: bool = False
    description: str | None = None


@dataclass(frozen=True)
class Profile:
    """Named set of define values."""

    name: str
    defines: dict[str, int]
    source: Source


@dataclass(frozen=True)
class UserConstant:
    """Integer constant owned by application code."""

    name: str
    cpp: str
    source: Source
    value: int | None = None
    profile_values: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class UserType:
    """A field type whose wire form is opaque.

    `size_bytes` raw bytes, backed by the user's own C++ type and its own `encode`/`decode` free
    functions (found by ADL, like a generated struct).
    """

    name: str
    cpp: str
    size_bytes: int
    source: Source
    include: str | None = None
    style: str = "quote"
    description: str | None = None


@dataclass(frozen=True)
class UserHeader:
    """Header included by the generated user prelude."""

    path: str
    source: Source
    style: str = "quote"
    condition: str | None = None


@dataclass(frozen=True)
class Define:
    """Named integer constant emitted as an overridable macro."""

    name: str
    value: int
    source: Source


@dataclass
class Project:
    """A loaded project: root file plus merged fragments."""

    name: str
    source: Source
    version: str | None = None
    namespace: str | None = None
    defaults: dict[str, object] = field(default_factory=dict)
    defines: dict[str, Define] = field(default_factory=dict)
    profiles: dict[str, Profile] = field(default_factory=dict)
    checksums: dict[str, Checksum] = field(default_factory=dict)
    enums: dict[str, Enum] = field(default_factory=dict)
    types: dict[str, Struct] = field(default_factory=dict)
    user_types: dict[str, UserType] = field(default_factory=dict)
    messages: dict[str, Struct] = field(default_factory=dict)
    interfaces: dict[str, Interface] = field(default_factory=dict)
    constants: dict[str, UserConstant] = field(default_factory=dict)
    headers: list[UserHeader] = field(default_factory=list)
    include_dirs: list[tuple[str, Source]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    def struct(self, name: str) -> Struct | None:
        """Return the `types` entry with this name (messages are not usable as field types)."""
        return self.types.get(name)
