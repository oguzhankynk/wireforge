"""Low-level requirements derived from the model, and the map from generated tests to them.

The model is the low-level design: every enum, checksum, struct, message, field attribute and
interface behaviour implies a testable statement. `derive()` writes these statements down as
`Llr` entries; nothing is stored. The key of an entry is built from names only
(`SensorReading.temperature.RANGE`), so reordering fields keeps it and renaming an element changes
it. Fields of a nested type belong to the type (`Vec3.x.RANGE`), not to every message that uses it.
Header and trailer fields of an interface use `<Interface>.header.<field>` and
`<Interface>.trailer.<field>`.

`display_id()` is the only place that turns a key into the id shown in code, tests, reports and
documents. `Verifier` maps every generated test case to the keys it verifies; the generated test
sources record them (`RecordProperty`) and the trace report reads them back from there.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from wireforge import layout, runtime_reqs
from wireforge.model import BUILTIN_BITS, INTEGER_TYPES
from wireforge.test_cases import CLEAR, OK
from wireforge.validate import Validator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from wireforge.model import (
        Checksum,
        Enum,
        Field,
        IdMap,
        Interface,
        Project,
        Source,
        Struct,
        Value,
    )
    from wireforge.test_cases import DecodeCase, EncodeCase

KINDS: tuple[str, ...] = (
    "VALUES",
    "ALGORITHM",
    "LAYOUT",
    "SIZE",
    "TRUNCATED",
    "TRAILING",
    "DEFAULT",
    "RANGE",
    "ENUM",
    "BITS",
    "COUNT",
    "CHARS",
    "CONST",
    "CHECKSUM",
    "FRAME_ENCODE",
    "FRAME_SYNC",
    "FRAME_LENGTH",
    "FRAME_RESYNC",
    "FRAME_MAX",
    "DISPATCH",
    "DISPATCH_UNKNOWN",
    "RAW",
)
# Requirements the runtime tests verify; the generated tests of a project do not cover them.
RUNTIME_VERIFIED = frozenset({"DISPATCH_UNKNOWN"})
GENERATED_ONLY = runtime_reqs.GENERATED_ONLY
ID_PREFIX = "LLR-"
NOTE_WIDTH = 96  # 100 columns minus the `/// ` prefix
_NUMBER = re.compile(r"-?(\d+|0[xX][0-9a-fA-F]+)")
_POINTER = re.compile(r"^/(types|messages|interfaces)/([^/]+)/(?:(header|trailer)/)?fields/\d+$")
_COVERS = {
    "preceding": "all bytes before it",
    "payload": "the payload",
    "header_and_payload": "the header and the payload",
}
_BY_STATUS = {
    "OutOfRange": ("RANGE", "ENUM", "BITS"),
    "CountExceedsMax": ("COUNT",),
    "ConstMismatch": ("CONST",),
    "ChecksumMismatch": ("CHECKSUM",),
}
# Adds one requirement of the field being described: (kind, text, extra parent ids).
_Add = Callable[..., None]


@dataclass(frozen=True)
class Llr:
    """One low-level requirement."""

    key: str
    kind: str
    element: str
    text: str
    parents: tuple[str, ...]
    source: Source
    runtime_verified: bool = False
    realized_by: tuple[str, ...] = ()

    @property
    def derived(self) -> bool:
        """Whether no high-level requirement is behind it."""
        return not self.parents


def display_id(key: str, ids: IdMap | None = None) -> str:
    """Return the id shown for a key; the only place that decides how ids look.

    With a lock file (`ids`) a numbered key shows its number, any other key shows `LLR-<key>`.
    """
    if ids is not None and key in ids.ids:
        return ids.ids[key]
    return ID_PREFIX + key


def owner_of(field: Field) -> str:
    """Return the key prefix of the struct, header or trailer a field belongs to."""
    match = _POINTER.match(field.source.pointer)
    if match is None:
        message = f"{field.name}: unexpected source pointer {field.source.pointer!r}"
        raise ValueError(message)
    section, name, part = match.groups()
    return f"{name}.{part}" if section == "interfaces" else name


def field_at(project: Project, owner: Struct, path: str) -> tuple[Struct, Field] | None:
    """Return a codec error path's last field and the struct (type or message) declaring it.

    The path looks like `/<field>/<index>/<field>`; indices are skipped and nested types are
    entered.
    """
    current = owner
    found: tuple[Struct, Field] | None = None
    for part in path.split("/"):
        if not part or part.isdigit():
            continue
        item = next((f for f in current.fields if f.name == part), None)
        if item is None:
            return None
        found = (current, item)
        current = project.types.get(item.type, current)
    return found


def _unique(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({item for group in groups for item in group}))


def _quote(value: object) -> str:
    return f'"{value}"' if isinstance(value, str) else str(value).lower()


def _literal(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(_quote(item) for item in value)
    return _quote(value)


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


class _Deriver:
    """Builds the list of requirements of one project."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.validator = Validator(project)
        self.report = layout.analyze(project)
        self.found: list[Llr] = []

    # --- helpers --------------------------------------------------------------------------------

    def add(
        self, key: str, element: str, text: str, parents: tuple[str, ...], source: Source
    ) -> None:
        kind = key.rsplit(".", 1)[1]
        verified = kind in RUNTIME_VERIFIED
        runtime = () if kind in GENERATED_ONLY else runtime_reqs.realized_by_kind().get(kind, ())
        self.found.append(Llr(key, kind, element, text, parents, source, verified, runtime))

    def value(self, raw: Value) -> str:
        """Write a number as given and an expression as given plus its value per profile."""
        text = str(raw)
        if isinstance(raw, (int, float)) or _NUMBER.fullmatch(text):
            return text
        values = [
            (variant.name, self.validator.static_value(raw, variant))
            for variant in self.validator.variants
        ]
        known = [(name, number) for name, number in values if number is not None]
        if not known:
            return text
        if not self.project.profiles:
            return str(known[0][1])
        return f"{text} ({', '.join(f'{name}: {number}' for name, number in known)})"

    def directions(self, item: Struct | None) -> str:
        """Name the directions in which range checks are enabled ('' when in none)."""
        checks: dict[str, Any] = {"encode": True, "decode": True}
        configured = self.project.defaults.get("range_check")
        checks.update(configured if isinstance(configured, dict) else {})
        if item is not None and item.message is not None:
            checks.update(item.message.range_check or {})
        names = [
            name for name, key in (("encoding", "encode"), ("decoding", "decode")) if checks[key]
        ]
        return _capitalize(" and ".join(names))

    # --- enums and checksums --------------------------------------------------------------------

    def enum(self, item: Enum) -> None:
        members = ", ".join(f"{name} = {number}" for name, number in item.values.items())
        text = f"`{item.name}` shall define the members {members} with underlying type {item.type}."
        self.add(
            f"{item.name}.VALUES", f"/enums/{item.name}", text, _unique(item.reqs), item.source
        )

    def checksum(self, item: Checksum) -> None:
        if item.algo == "crc32":
            how = "CRC-32 (IEEE)"
        elif item.algo == "crc":
            how = (
                f"CRC-{item.width} (poly 0x{item.poly or 0:X}, init 0x{item.init:X}, "
                f"refin {_quote(item.refin)}, refout {_quote(item.refout)}, "
                f"xorout 0x{item.xorout:X})"
            )
        else:
            how = f"computed by `{item.impl}` (user code)"
        text = f"The checksum `{item.name}` shall be {how}."
        pointer = f"/checksums/{item.name}"
        self.add(f"{item.name}.ALGORITHM", pointer, text, _unique(item.reqs), item.source)

    # --- structs --------------------------------------------------------------------------------

    def describe(self, item: Field) -> str:
        if item.type in self.project.user_types:
            user = self.project.user_types[item.type]
            what = f"{user.size_bytes} opaque bytes encoded by `{user.cpp}`"
        else:
            what = item.type + (f", {item.bits} bits" if item.bits else "")
        if item.occurs is not None:
            what += f", up to {self.value(item.occurs.max)} times"
        if item.reserved:
            what += ", reserved"
        text = f"{item.name} ({what})"
        return text + (f" only when {item.condition}" if item.condition else "")

    def layout_text(self, item: Struct) -> str:
        fields = ", ".join(self.describe(member) for member in item.fields)
        info = item.message
        if info is None:
            order = "the endian and bit order of the enclosing message"
        else:
            endian = info.endian or "the endian of the carrying interface"
            order = f"endian {endian}, bit order {info.bit_order or 'of the carrying interface'}"
        return (
            f"`{item.name}` shall be encoded as the fields {fields} in this order with no "
            f"padding, {order}."
        )

    def struct(self, item: Struct, section: str) -> None:
        pointer = f"/{section}/{item.name}"
        reqs = _unique(item.reqs)
        self.add(f"{item.name}.LAYOUT", pointer, self.layout_text(item), reqs, item.source)
        if item.message is not None:
            self.message_rules(item, pointer, reqs)
        for index, member in enumerate(item.fields):
            self.field(item.name, member, item, f"{pointer}/fields/{index}", reqs)

    def message_rules(self, item: Struct, pointer: str, reqs: tuple[str, ...]) -> None:
        name = item.name
        self.size(item, pointer, reqs)
        short = (
            f"Decoding `{name}` from fewer bytes than its fields need shall fail with `Truncated`."
        )
        long = f"Decoding `{name}` shall fail with `TrailingBytes` when payload bytes remain."
        self.add(f"{name}.TRUNCATED", pointer, short, reqs, item.source)
        self.add(f"{name}.TRAILING", pointer, long, reqs, item.source)

    def size(self, item: Struct, pointer: str, reqs: tuple[str, ...]) -> None:
        sizes = [
            (profile.profile, message.size_min, message.size_max)
            for profile in self.report.profiles
            for message in profile.messages
            if message.name == item.name
        ]
        if not sizes:
            return

        def span(low: int, high: int) -> str:
            return f"exactly {low}" if low == high else f"between {low} and {high}"

        subject = f"The wire size of `{item.name}` shall be"
        if len({(low, high) for _, low, high in sizes}) == 1:
            text = f"{subject} {span(sizes[0][1], sizes[0][2])} bytes."
        else:
            parts = ", ".join(f"{name}: {span(low, high)}" for name, low, high in sizes)
            text = f"{subject}, per profile, {parts} bytes."
        self.add(f"{item.name}.SIZE", pointer, text, reqs, item.source)

    def field(
        self, owner: str, item: Field, container: Struct | None, pointer: str, reqs: tuple[str, ...]
    ) -> None:
        """Add the requirements of one field of a struct, header or trailer."""
        parents = _unique(item.reqs, reqs)

        def add(kind: str, text: str, more: tuple[str, ...] = ()) -> None:
            key = f"{owner}.{item.name}.{kind}"
            self.add(key, pointer, text, _unique(parents, more), item.source)

        name = f"`{item.name}`"
        if item.default is not None:
            add("DEFAULT", f"The default value of {name} shall be {_literal(item.default)}.")
        self.checked(item, container, add)
        if item.occurs is not None:
            self.counted(item, add)
        if item.const is not None:
            text = f"{name} shall be written as {_quote(item.const)}"
            add("CONST", f"{text}; decoding shall fail with `ConstMismatch` otherwise.")
        if item.checksum is not None:
            use = item.checksum
            covers = (
                _COVERS[use.scope] if use.scope else f"the fields {use.first} through {use.last}"
            )
            text = f"{name} shall hold the `{use.use}` checksum over {covers}"
            add(
                "CHECKSUM",
                f"{text}; decoding shall fail with `ChecksumMismatch` otherwise.",
                self.project.checksums[use.use].reqs,
            )

    def checked(self, item: Field, container: Struct | None, add: _Add) -> None:
        """Range, enum membership and bit width checks of a field."""
        who = self.directions(container)
        if not who:
            return
        fail = f"{who} shall fail with `OutOfRange` when `{item.name}`"
        if item.min is not None or item.max is not None:
            add("RANGE", f"{fail} is {self.bounds(item)}.")
        if item.type in self.project.enums:
            enum = self.project.enums[item.type]
            add("ENUM", f"{fail} is not a member of `{item.type}`.", enum.reqs)
        width = BUILTIN_BITS.get(item.type) if item.type in INTEGER_TYPES else None
        narrowed = item.bits is not None and width is not None and item.bits < width
        if narrowed and item.min is None and item.max is None and not item.reserved:
            add("BITS", f"{fail} does not fit {item.bits} bits.")

    def bounds(self, item: Field) -> str:
        units = f" {item.units}" if item.units else ""
        if item.min is not None and item.max is not None:
            return f"outside [{self.value(item.min)}, {self.value(item.max)}]{units}"
        if item.min is not None:
            return f"below {self.value(item.min)}{units}"
        return f"above {self.value(item.max or 0)}{units}"

    def counted(self, item: Field, add: _Add) -> None:
        """Element count and string rules of an array field."""
        occurs = item.occurs
        if occurs is None:
            return
        name = f"`{item.name}`"
        top = self.value(occurs.max)
        error = "otherwise `CountExceedsMax`"
        if occurs.fill:
            add(
                "COUNT",
                f"The elements of {name} shall fill the remaining payload, at most {top}; {error}.",
            )
        elif occurs.count is not None or occurs.min is not None:
            source = f" taken from `{occurs.count}`" if occurs.count is not None else ""
            low = self.value(occurs.min) if occurs.min is not None else "0"
            between = f"between {low} and {top}"
            add("COUNT", f"The number of {name} elements shall be{source} and {between}; {error}.")
        elif item.type == "char":
            add("COUNT", f"{name} shall hold at most {top} characters; {error}.")
        if item.type == "char":
            pad = "zero padded on encode and stripped of trailing zeros on decode"
            add("CHARS", f"{name} shall be {pad}.")

    # --- interfaces -----------------------------------------------------------------------------

    def interface(self, item: Interface) -> None:
        pointer = f"/interfaces/{item.name}"
        own = _unique(item.reqs)
        for part, fields in (("header", item.header or ()), ("trailer", item.trailer)):
            for index, member in enumerate(fields):
                where = f"{pointer}/{part}/fields/{index}"
                self.field(f"{item.name}.{part}", member, None, where, own)
        reqs = _unique(item.reqs, item.framing.reqs)
        for kind, text in self.frame_rules(item):
            self.add(f"{item.name}.{kind}", pointer, text, reqs, item.source)
        self.dispatch(item, pointer, reqs)

    def frame_rules(self, item: Interface) -> Iterator[tuple[str, str]]:
        """Framing requirements as (kind, text), in KIND order."""
        name = f"`{item.name}`"
        header = item.header or ()
        framing = item.framing
        written = "the header (ids, lengths, constants), " if item.header else ""
        written += "the payload" + (" and the trailer" if item.trailer else "")
        yield (
            "FRAME_ENCODE",
            (
                f"Encoding a frame of {name} shall write {written}; it shall fail with "
                "`BufferTooSmall` when the buffer cannot hold it."
            ),
        )
        sync = [member for member in header if member.role == "sync"]
        if sync:
            values = ", ".join(_quote(member.const) for member in sync)
            yield (
                "FRAME_SYNC",
                f"The receiver of {name} shall skip bytes until the sync value {values}.",
            )
        length = next((member for member in header if member.role == "payload_length"), None)
        if framing.kind == "fixed":
            size = self.value(framing.frame_size or 0)
            yield "FRAME_LENGTH", f"Every frame of {name} shall be exactly {size} bytes."
        elif length is not None or framing.kind != "none":
            where = f"`{length.name}`" if length is not None else "the length prefix"
            counts = framing.length_counts.replace("_", " ")
            taken = f"shall be taken from {where} counting the {counts}"
            yield "FRAME_LENGTH", f"The length of a frame of {name} {taken}."
        if framing.kind != "none":
            after = f"After a rejected frame the receiver of {name} shall"
            how = (
                "skip one byte and scan for the next frame"
                if framing.resync
                else "drop all buffered bytes"
            )
            yield "FRAME_RESYNC", f"{after} {how}."
        limit = framing.max_frame_size if framing.max_frame_size is not None else framing.frame_size
        if limit is not None:
            rejected = f"larger than {self.value(limit)} bytes shall be rejected"
            yield "FRAME_MAX", f"Frames of {name} {rejected} with `InvalidLength`."
        else:
            yield (
                "FRAME_MAX",
                (
                    f"The largest frame of {name} shall be the header, the largest payload and "
                    "the trailer of its messages."
                ),
            )

    def dispatch(self, item: Interface, pointer: str, reqs: tuple[str, ...]) -> None:
        name = f"`{item.name}`"
        routes = []
        for entry in item.messages:
            message = self.project.messages[entry.name]
            number = entry.id
            if number is None and message.message is not None:
                number = message.message.id
            ident = f" (id {number})" if number is not None else ""
            routes.append(f"{entry.name}{ident} to `on{entry.name}Received`")
        parents = _unique(
            reqs,
            *(entry.reqs for entry in item.messages),
            *(self.project.messages[entry.name].reqs for entry in item.messages),
        )
        text = (
            f"Every valid frame of {name} shall be delivered to the handler: {', '.join(routes)}."
        )
        self.add(f"{item.name}.DISPATCH", pointer, text, parents, item.source)
        if any(member.role == "message_id" for member in item.header or ()):
            text = (
                f"A frame of {name} with an unknown message id shall be reported as "
                "`UnknownMessageId` and not dispatched."
            )
            self.add(f"{item.name}.DISPATCH_UNKNOWN", pointer, text, reqs, item.source)
        text = (
            f"Every valid frame of {name} shall also reach the raw hook `on<Message>Raw` with its "
            "payload and the whole frame; the default of the hook shall decode the payload and "
            "call `on<Message>Received`."
        )
        self.add(f"{item.name}.RAW", pointer, text, reqs, item.source)

    def run(self) -> list[Llr]:
        project = self.project
        for name in sorted(project.enums):
            self.enum(project.enums[name])
        for name in sorted(project.checksums):
            self.checksum(project.checksums[name])
        for name in sorted(project.types):
            self.struct(project.types[name], "types")
        for name in sorted(project.messages):
            self.struct(project.messages[name], "messages")
        for name in sorted(project.interfaces):
            self.interface(project.interfaces[name])
        return self.found


def derive(project: Project) -> list[Llr]:
    """Return every low-level requirement of a valid project, in a stable order."""
    return _Deriver(project).run()


def requirement_notes(project: Project) -> dict[str, list[str]]:
    """Return the Doxygen lines naming the requirements behind each generated element.

    The key is the name of the enum, checksum, struct or interface. A project without a
    `requirements` catalog gets no notes, so its generated code is the same as before.
    """
    if not project.requirements:
        return {}
    owned: dict[str, list[Llr]] = {}
    for item in derive(project):
        owned.setdefault(item.key.split(".", 1)[0], []).append(item)
    notes: dict[str, list[str]] = {}
    for owner, items in owned.items():
        highs = ", ".join(sorted({parent for item in items for parent in item.parents}))
        lows = ", ".join(display_id(item.key, project.llr_ids) for item in items)
        text = f"Requirements: {highs}; {lows}" if highs else f"Requirements: {lows}"
        wrapped = textwrap.wrap(
            text, width=NOTE_WIDTH, break_long_words=False, break_on_hyphens=False
        )
        notes[owner] = [f"/// {line}" for line in wrapped]
    return notes


# --- tests -> requirements ---------------------------------------------------------------------


class Verifier:
    """Requirement keys verified by each generated test case of one project."""

    def __init__(self, project: Project, llrs: list[Llr] | None = None) -> None:
        """Index the keys of `llrs` (derived from `project` when not given)."""
        self.project = project
        self.known = {item.key for item in (derive(project) if llrs is None else llrs)}

    def shown(self, keys: tuple[str, ...]) -> tuple[str, ...]:
        """Return the displayed ids of keys (numbers from the lock file, if the project has one)."""
        return tuple(display_id(key, self.project.llr_ids) for key in keys)

    def keep(self, *keys: str) -> tuple[str, ...]:
        """Return the given keys that exist, without repeats, in the order given."""
        result: list[str] = []
        for key in keys:
            if key in self.known and key not in result:
                result.append(key)
        return tuple(result)

    def first(self, *keys: str) -> tuple[str, ...]:
        """Return the first of the given keys that exists."""
        return self.keep(*keys)[:1]

    def nested(self, item: Struct) -> Iterator[Struct]:
        """Yield the types used (directly or through other types) by the fields of a struct."""
        for member in item.fields:
            inner = self.project.types.get(member.type)
            if inner is not None:
                yield inner
                yield from self.nested(inner)

    @staticmethod
    def frame_parts(interface: Interface) -> tuple[str, ...]:
        """Header constants and trailer checksums (with their algorithms) of an interface."""
        keys = [
            f"{interface.name}.header.{member.name}.CONST"
            for member in interface.header or ()
            if member.const is not None
        ]
        for member in interface.trailer:
            if member.checksum is not None:
                keys += [
                    f"{interface.name}.trailer.{member.name}.CHECKSUM",
                    f"{member.checksum.use}.ALGORITHM",
                ]
        return tuple(keys)

    @staticmethod
    def checksum_keys(owner: Struct) -> Iterator[str]:
        """Checksum requirements and algorithms of the fields of a struct."""
        for member in owner.fields:
            if member.checksum is not None:
                yield f"{owner.name}.{member.name}.CHECKSUM"
                yield f"{member.checksum.use}.ALGORITHM"

    def golden(self, vector: dict[str, Any]) -> tuple[str, ...]:
        """Golden vector: layouts, frame encoding, dispatch, header constants and checksums."""
        message = self.project.messages[vector["message"]]
        interface = self.project.interfaces[vector["interface"]]
        owners = (message, *self.nested(message))
        return self.keep(
            f"{message.name}.LAYOUT",
            *(f"{inner.name}.LAYOUT" for inner in owners[1:]),
            f"{interface.name}.FRAME_ENCODE",
            f"{interface.name}.DISPATCH",
            *(key for owner in owners for key in self.checksum_keys(owner)),
            *self.frame_parts(interface),
        )

    def defaults(self, message: Struct) -> tuple[str, ...]:
        """Default value test: layout and every declared default."""
        keys = [f"{message.name}.LAYOUT"]
        for owner in (message, *self.nested(message)):
            keys += [
                f"{owner.name}.{f.name}.DEFAULT" for f in owner.fields if f.default is not None
            ]
        return self.keep(*keys)

    def wire_size(self, message: Struct) -> tuple[str, ...]:
        """Wire size assertion of a message."""
        return self.keep(f"{message.name}.SIZE")

    def encode_case(self, message: Struct, case: EncodeCase) -> tuple[str, ...]:
        """Boundary, enum member and emptied-array tests of one field."""
        item = case.edit.field
        owner = owner_of(item)
        base = f"{owner}.{item.name}"
        layout_key = f"{message.name}.LAYOUT"
        if case.edit.value is CLEAR:
            return self.keep(f"{base}.COUNT", layout_key)
        if item.type in self.project.enums:
            members = (f"{item.type}.VALUES",) if "_member_" in case.name else ()
            return self.keep(f"{base}.ENUM", *members)
        found = self.first(f"{base}.RANGE", f"{base}.BITS", f"{owner}.LAYOUT")
        return self.keep(*found, layout_key)

    def decode_case(self, message: Struct, case: DecodeCase) -> tuple[str, ...]:
        """Decode test: the requirement behind the status the mutation provokes."""
        name = message.name
        if case.name == "truncated" or case.status == "Truncated":
            return self.first(f"{name}.TRUNCATED")
        if case.name == "extended":
            return self.first(f"{name}.TRAILING" if case.status != OK else f"{name}.LAYOUT")
        if case.status == "TrailingBytes":
            return self.first(f"{name}.TRAILING")
        located = field_at(self.project, message, case.path)
        if located is None:
            return self.first(f"{name}.LAYOUT")
        owner, item = located
        kinds = _BY_STATUS.get(case.status, ())
        found = self.first(
            *(f"{owner.name}.{item.name}.{kind}" for kind in kinds), f"{owner.name}.LAYOUT"
        )
        if case.status == "ChecksumMismatch" and item.checksum is not None:
            return self.keep(*found, f"{item.checksum.use}.ALGORITHM")
        return found

    def interface_test(
        self,
        interface: Interface,
        test: str,
        *,
        corrupt_status: str = OK,
        decode_keys: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        """Map a test of the `Interface_<I>` suite.

        `corrupt_status` is the status the corrupted frame is rejected with; `decode_keys` the
        message requirements behind a raw decode error.
        """
        name = interface.name

        def key(kind: str) -> str:
            return f"{name}.{kind}"

        by_name = {
            "DispatchesEveryMessage": (key("DISPATCH"), key("FRAME_LENGTH")),
            "DeliversByteByByte": (key("FRAME_LENGTH"), key("FRAME_SYNC")),
            "SkipsBytesBeforeSync": (key("FRAME_SYNC"),),
            "EncodeFrameNeedsRoom": (key("FRAME_ENCODE"),),
            "WireSize": (key("FRAME_MAX"),),
        }
        if test == "RejectsCorruptedFrame":
            checksums = [
                f"{name}.trailer.{member.name}.CHECKSUM"
                for member in interface.trailer
                if member.checksum is not None and corrupt_status == "ChecksumMismatch"
            ]
            return self.keep(*checksums, key("FRAME_RESYNC")) or self.keep(key("DISPATCH"))
        if test == "RejectsAFrameWithAWrongConstantOrChecksum":
            constants = [
                f"{name}.header.{member.name}.CONST"
                for member in interface.header or ()
                if member.const is not None
            ]
            sums = [
                f"{name}.trailer.{member.name}.CHECKSUM"
                for member in interface.trailer
                if member.checksum is not None
            ]
            return self.keep(*constants, *sums) or self.keep(key("DISPATCH"))
        if test.startswith("RawDecodeErrorOf"):
            return self.keep(key("RAW"), *decode_keys)
        # DeliversRawBytesWithoutDecoding and RawCanForwardToDecoder fall through to RAW.
        return self.keep(*by_name.get(test, (key("RAW"),)))
