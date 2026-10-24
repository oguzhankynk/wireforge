"""MAVLink XML message definitions (dialects) as a MAVLink 2 project: `wireforge import-mavlink`.

A dialect file and the files it includes give the messages and enums. The project has the frame of
the published MAVLink 2 model (`protocols/mavlink2`): the interface `link` with the 24-bit message
id, payloads without trailing zero bytes and the checksum with the CRC_EXTRA byte of every
message, which is computed here from the message definition as the MAVLink generators do.

The fields of a message are put in the order MAVLink sends them: the base fields by the size of
their type, largest first (a stable sort, so equal sizes keep their order), then the extension
fields in the order they are written. Enums become enums of the project, but fields keep their
integer type, because a MAVLink receiver must accept enum values it does not know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

from wireforge import resources
from wireforge.issues import Issue
from wireforge.validate import CPP_KEYWORDS

if TYPE_CHECKING:
    from pathlib import Path

    from wireforge.importers import Project

# MAVLink type -> (WireForge type, size in bytes).
TYPES: dict[str, tuple[str, int]] = {
    "char": ("char", 1),
    "uint8_t": ("u8", 1),
    "int8_t": ("i8", 1),
    "uint16_t": ("u16", 2),
    "int16_t": ("i16", 2),
    "uint32_t": ("u32", 4),
    "int32_t": ("i32", 4),
    "uint64_t": ("u64", 8),
    "int64_t": ("i64", 8),
    "float": ("f32", 4),
    "double": ("f64", 8),
}
MAVLINK_VERSION_TYPE = "uint8_t_mavlink_version"
MAVLINK_VERSION = 3
MAX_PAYLOAD = 255
_ARRAY = re.compile(r"^(?P<type>\w+)\[(?P<length>\d+)\]$")
_POWER = re.compile(r"^(?P<base>\d+)\s*\*\*\s*(?P<exponent>\d+)$")
_SPACES = re.compile(r"\s+")
# Files copied from the published MAVLink 2 model; crc_extra.h and the Lua table are written here.
COPIED = (
    "app/include/mav2sum/crc.h",
    "app/include/mav2sum/crc.hpp",
    "plugins/__init__.py",
    "plugins/mavlink2_crc.py",
)
CRC_EXTRA_HEADER = "app/include/mav2sum/crc_extra.h"
LUA_HOOK = "wireshark/mav2sum.lua"


class MavlinkImportError(Exception):
    """A dialect that cannot be read or expressed."""

    def __init__(self, source: str, message: str) -> None:
        """Keep the problem as an issue of `source`."""
        super().__init__(message)
        self.issues = [Issue(source, None, "", message)]


@dataclass(frozen=True)
class XmlField:
    """One field of a message as the XML defines it."""

    name: str
    type: str  # the MAVLink base type (`uint8_t`, `char`, ...), without the array length
    array: int | None
    extension: bool
    units: str | None = None
    description: str = ""
    enum: str | None = None
    version_field: bool = False  # `uint8_t_mavlink_version`

    @property
    def size(self) -> int:
        """Return the size of one element in bytes."""
        return TYPES[self.type][1]


@dataclass
class XmlMessage:
    """One message of a dialect."""

    id: int
    name: str
    description: str
    fields: list[XmlField]
    source: str


@dataclass
class XmlEnum:
    """One enum of a dialect (entries of all files that define it, in order)."""

    name: str
    description: str
    entries: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Dialect:
    """The messages and enums of a dialect and the files it was read from."""

    messages: dict[int, XmlMessage] = field(default_factory=dict)
    enums: dict[str, XmlEnum] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)


def _text(element: ET.Element | None) -> str:
    """Return the text of an element with its whitespace collapsed."""
    if element is None:
        return ""
    return _SPACES.sub(" ", "".join(element.itertext())).strip()


def _number(text: str, where: str) -> int:
    power = _POWER.match(text)
    if power is not None:
        return int(int(power.group("base")) ** int(power.group("exponent")))
    try:
        return int(text, 0)
    except ValueError:
        raise MavlinkImportError(where, f"value {text!r} is not an integer") from None


def _field(element: ET.Element, *, extension: bool, where: str) -> XmlField:
    raw = element.get("type", "")
    name = element.get("name", "")
    array = None
    match = _ARRAY.match(raw)
    if match is not None:
        raw, array = match.group("type"), int(match.group("length"))
    version_field = raw == MAVLINK_VERSION_TYPE
    if version_field:
        raw = "uint8_t"
    if raw not in TYPES or not name:
        raise MavlinkImportError(where, f"field {name!r} has the unknown type {raw!r}")
    return XmlField(
        name,
        raw,
        array,
        extension,
        element.get("units"),
        _text(element),
        element.get("enum"),
        version_field,
    )


def _message(element: ET.Element, source: str) -> XmlMessage:
    name = element.get("name", "")
    where = f"{source}: message {name}"
    fields: list[XmlField] = []
    extension = False
    for child in element:
        if child.tag == "extensions":
            extension = True
        elif child.tag == "field":
            fields.append(_field(child, extension=extension, where=where))
    identifier = _number(element.get("id", ""), where)
    return XmlMessage(identifier, name, _text(element.find("description")), fields, source)


def _enum(dialect: Dialect, element: ET.Element, source: str) -> None:
    name = element.get("name", "")
    enum = dialect.enums.setdefault(name, XmlEnum(name, _text(element.find("description"))))
    following = (enum.entries[-1][1] + 1) if enum.entries else 0
    for entry in element.findall("entry"):
        value_text = entry.get("value")
        where = f"{source}: enum {name}"
        value = following if value_text is None else _number(value_text, where)
        enum.entries.append((entry.get("name", ""), value))
        following = value + 1


def _read(dialect: Dialect, path: Path, seen: set[Path]) -> None:
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as error:
        raise MavlinkImportError(str(path), f"cannot read the dialect: {error}") from None
    # MAVLink definitions have no document type: refusing one keeps entity expansion out.
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise MavlinkImportError(str(path), "a document type or entity declaration is not allowed")
    try:
        root = ET.fromstring(text)  # noqa: S314 (no DTD, so no entities to expand)
    except ET.ParseError as error:
        raise MavlinkImportError(str(path), f"cannot read the dialect: {error}") from None
    for include in root.findall("include"):
        _read(dialect, resolved.parent / _text(include), seen)
    dialect.files.append(resolved)
    for element in root.findall("enums/enum"):
        _enum(dialect, element, str(path))
    for element in root.findall("messages/message"):
        message = _message(element, str(path))
        earlier = dialect.messages.get(message.id)
        if earlier is not None and earlier.name != message.name:
            text = f"message id {message.id} is both {earlier.name} and {message.name}"
            raise MavlinkImportError(str(path), text)
        dialect.messages[message.id] = message


def parse(path: Path) -> Dialect:
    """Read a dialect file and every file it includes (each once)."""
    dialect = Dialect()
    _read(dialect, path, set())
    return dialect


# --- the MAVLink rules -------------------------------------------------------------------------


def wire_order(message: XmlMessage) -> list[XmlField]:
    """Return the fields in the order they are sent: base fields by size, then the extensions."""
    base = [item for item in message.fields if not item.extension]
    extensions = [item for item in message.fields if item.extension]
    return sorted(base, key=lambda item: -item.size) + extensions


def _x25(crc: int, data: bytes) -> int:
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def crc_extra(message: XmlMessage) -> int:
    """Return the CRC_EXTRA byte of a message: a hash of its name and its base fields."""
    crc = _x25(0xFFFF, f"{message.name} ".encode())
    for item in wire_order(message):
        if item.extension:
            continue
        crc = _x25(crc, f"{item.type} {item.name} ".encode())
        if item.array is not None:
            crc = _x25(crc, bytes([item.array]))
    return (crc & 0xFF) ^ (crc >> 8)


def payload_size(message: XmlMessage) -> int:
    """Return the size of the whole payload (extensions included) in bytes."""
    return sum(item.size * (item.array or 1) for item in message.fields)


# --- the project -------------------------------------------------------------------------------


def camel(name: str, *, upper: bool) -> str:
    """Return HEARTBEAT as Heartbeat (upper) and time_boot_ms as timeBootMs."""
    parts = [part for part in name.lower().split("_") if part]
    if not parts:
        return name
    head = parts[0].capitalize() if upper else parts[0]
    return head + "".join(part.capitalize() for part in parts[1:])


def _field_name(item: XmlField, taken: set[str], notes: list[str], where: str) -> str:
    name = camel(item.name, upper=False)
    if name in CPP_KEYWORDS or name in taken:
        renamed = f"{name}Value"
        notes.append(f"{where}: field {item.name} is called {renamed}")
        name = renamed
    taken.add(name)
    return name


def _field_entry(item: XmlField, name: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "type": TYPES[item.type][0]}
    if item.array is not None:
        entry["occurs"] = {"max": item.array}
    if item.version_field:
        entry["const"] = MAVLINK_VERSION
    if item.units:
        entry["units"] = item.units
    description = item.description
    if item.enum:
        description = f"{description} (enum {item.enum})".strip()
    if item.extension:
        description = f"{description} (extension)".strip()
    if description:
        entry["description"] = description
    return entry


def _message_entry(message: XmlMessage, notes: list[str]) -> dict[str, Any]:
    taken: set[str] = set()
    where = f"message {message.name}"
    fields = [
        _field_entry(item, _field_name(item, taken, notes, where)) for item in wire_order(message)
    ]
    entry: dict[str, Any] = {"id": message.id}
    if message.description:
        entry["description"] = message.description
    entry["fields"] = fields
    return entry


def _enum_type(values: list[int]) -> str:
    top = max(values, default=0)
    if min(values, default=0) < 0:
        return "i64" if top >= 1 << 31 else "i32"
    for name, bits in (("u8", 8), ("u16", 16), ("u32", 32)):
        if top < 1 << bits:
            return name
    return "u64"


def _enum_entry(enum: XmlEnum, notes: list[str]) -> dict[str, Any] | None:
    values: dict[str, int] = {}
    used: dict[int, str] = {}
    for name, value in enum.entries:
        if not name or name in values:
            continue
        if value in used:
            notes.append(f"enum {enum.name}: {name} has the value of {used[value]} and is left out")
            continue
        values[name] = value
        used[value] = name
    if not values:
        return None
    entry: dict[str, Any] = {"type": _enum_type(list(values.values()))}
    if enum.description:
        entry["description"] = enum.description
    entry["values"] = values
    return entry


def _interface(names: list[str]) -> dict[str, Any]:
    """Return the MAVLink 2 interface of protocols/mavlink2 (without its requirement ids)."""
    return {
        "version": "1.0.0",
        "direction": "both",
        "header": {
            "fields": [
                {"name": "start", "type": "u8", "const": 0xFD, "role": "sync"},
                {"name": "length", "type": "u8", "role": "payload_length"},
                {
                    "name": "incompatFlags",
                    "type": "u8",
                    "const": 0,
                    "description": "Signed frames (0x01) are refused.",
                },
                {"name": "compatFlags", "type": "u8"},
                {"name": "sequence", "type": "u8", "role": "sequence"},
                {"name": "systemId", "type": "u8", "default": 1},
                {"name": "componentId", "type": "u32", "bits": 8, "default": 1},
                {"name": "messageId", "type": "u32", "bits": 24, "role": "message_id"},
            ]
        },
        "trailer": {
            "fields": [
                {
                    "name": "checksum",
                    "type": "u16",
                    "checksum": {"use": "mavlinkCrc", "covers": {"scope": "header_and_payload"}},
                }
            ]
        },
        "framing": {"kind": "sync_length", "max_frame_size": 267, "resync": True},
        "trim_trailing_zeros": True,
        "messages": names,
    }


def _crc_extra_header(messages: list[XmlMessage], dialect_name: str) -> str:
    lines = [
        "/**",
        " * @file crc_extra.h",
        " * @brief The CRC_EXTRA byte of every message, one list for C, C++ and Python.",
        " *",
        f" * Written by `wireforge import-mavlink` from {dialect_name}. A message added to",
        " * the model needs its line here; `plugins/mavlink2_crc.py` reads this file.",
        " */",
        "#ifndef MAV2SUM_CRC_EXTRA_H",
        "#define MAV2SUM_CRC_EXTRA_H",
        "",
        "/* clang-format off */",
        "#define MAV2SUM_CRC_EXTRA(X) \\",
        *[
            f"    X({message.id}U, {crc_extra(message)}U) /* {message.name} */ \\"
            for message in messages
        ],
        "    /* add new messages above this line */",
        "/* clang-format on */",
        "",
        "#endif /* MAV2SUM_CRC_EXTRA_H */",
        "",
    ]
    return "\n".join(lines)


def _lua_hook(template: str, messages: list[XmlMessage]) -> str:
    table = ", ".join(f"[{message.id}] = {crc_extra(message)}" for message in messages)
    pattern = re.compile(r"^local CRC_EXTRA = \{.*\}$", re.MULTILINE)
    return pattern.sub(lambda _: f"local CRC_EXTRA = {{ {table} }}", template, count=1)


README = """\
# MAVLink 2 from {source}

Imported by `wireforge import-mavlink` from {files}: {messages} messages and {enums} enums.

The frame is the one of the published MAVLink 2 model (`wireforge init --protocol mavlink2`):
the 24-bit message id, payloads without their trailing zero bytes and the checksum with the
CRC_EXTRA byte of every message (`app/include/mav2sum/crc_extra.h`, computed from the
definitions). Signed frames are refused. Fields keep their integer types; the enum a field takes
its values from is named in its description. Import the dialect again to follow a new version.

MAVLink is a project of the Dronecode Foundation; this project is not affiliated with or endorsed
by it.
"""


def _readme(dialect: Dialect, messages: list[XmlMessage], enums: int) -> str:
    source = dialect.files[-1].name if dialect.files else "the dialect"
    files = ", ".join(path.name for path in dialect.files)
    return README.format(source=source, files=files, messages=len(messages), enums=enums)


def to_project(
    dialect: Dialect, options: Project, selected: list[str] | None = None
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Return the project document, its other files (path -> text) and notes about the import.

    `selected` takes only these messages (MAVLink names such as HEARTBEAT) and the enums they use.
    """
    notes: list[str] = []
    messages = sorted(dialect.messages.values(), key=lambda message: message.id)
    if selected:
        wanted = {name.upper() for name in selected}
        missing = sorted(wanted - {message.name for message in messages})
        if missing:
            where, text = "--message", f"not in the dialect: {', '.join(missing)}"
            raise MavlinkImportError(where, text)
        messages = [message for message in messages if message.name in wanted]
    document: dict[str, Any] = {"schema_version": 1, "project": options.name}
    document["version"] = options.version
    document["description"] = f"MAVLink 2 messages imported from {dialect.files[-1].name}."
    document["namespace"] = options.namespace or f"{options.name}::mavlink"
    document["defaults"] = {
        "endian": "little",
        "bit_order": "lsb_first",
        "range_check": {"encode": True, "decode": True},
    }
    document["user_includes"] = {"include_dirs": ["app/include"]}
    document["checksums"] = {
        "mavlinkCrc": {
            "algo": "custom",
            "impl": "mav2sum::Crc",
            "include": "mav2sum/crc.hpp",
            "c_impl": "mav2sum_crc",
            "c_include": "mav2sum/crc.h",
            "size_bytes": 2,
        }
    }
    used = {item.enum for message in messages for item in message.fields if item.enum}
    enums: dict[str, Any] = {}
    for name, enum in dialect.enums.items():
        if selected and name not in used:
            continue
        entry = _enum_entry(enum, notes)
        if entry is not None:
            enums[name] = entry
    if enums:
        document["enums"] = enums
    entries: dict[str, Any] = {}
    for message in messages:
        if payload_size(message) > MAX_PAYLOAD:
            notes.append(f"message {message.name}: more than {MAX_PAYLOAD} payload bytes, left out")
            continue
        entries[camel(message.name, upper=True)] = _message_entry(message, notes)
    document["messages"] = entries
    document["interfaces"] = {"link": _interface(list(entries))}
    kept = [message for message in messages if camel(message.name, upper=True) in entries]
    template = resources.protocols_dir() / "mavlink2"
    files = {path: (template / path).read_text(encoding="utf-8") for path in COPIED}
    files[CRC_EXTRA_HEADER] = _crc_extra_header(kept, dialect.files[-1].name)
    files[LUA_HOOK] = _lua_hook((template / LUA_HOOK).read_text(encoding="utf-8"), kept)
    files["README.md"] = _readme(dialect, kept, len(enums))
    return document, files, notes
