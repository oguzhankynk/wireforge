"""Expected results of the generated C++ tests, computed with the reference codec.

For every profile of a project this module derives, per message:

- the encoding of the default value (`T{}`);
- boundary cases: every scalar member (and the first element of every array) at its minimum and
  maximum, one step outside its range when the C++ member type can hold that value, and an invalid
  value for enum members;
- element counts: every variable-size array emptied (the pattern value already fills arrays to
  their capacity);
- decode cases: single-byte mutations of the pattern payload, the first one for every distinct
  decode error and field (bad checksum, count above the maximum, wrong constant, value out of
  range, ...), plus a truncated and an extended payload;

and per interface the pattern frame of every message and a corrupted frame. The reference codec
is the oracle: every expectation is either the payload it produces or the status it reports.
"""

from __future__ import annotations

import dataclasses
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from wireforge import expr
from wireforge.codec import FLOAT32_MAX, CodecError, ReferenceCodec
from wireforge.cpp import Names, wire_only
from wireforge.expr import ExprError
from wireforge.golden import Sampler
from wireforge.model import BUILTIN_BITS, FLOAT_TYPES, Field, Interface, Project, Scalar, Struct

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from wireforge.codec import ChecksumPlugin
    from wireforge.validate import Variant

OK = "Ok"
BYTE_BITS = 8
BYTE_MASK = 0xFF
MUTATIONS: tuple[tuple[str, Callable[[int], int]], ...] = (
    ("inc", lambda byte: (byte + 1) & BYTE_MASK),
    ("x80", lambda byte: byte ^ 0x80),
    ("xff", lambda byte: byte ^ BYTE_MASK),
)
GARBAGE_BYTES = 3

# Payload bytes, or the name of the status the operation fails with.
Outcome = bytes | str


class Clear:
    """Edit value: empty the array (or string)."""

    def __repr__(self) -> str:
        """Readable form for test output."""
        return "Clear()"


CLEAR = Clear()


@dataclass(frozen=True)
class Edit:
    """Change applied to the pattern value: `target` is the C++ member path below the value."""

    field: Field
    target: str
    value: Scalar | Clear


@dataclass(frozen=True)
class EncodeCase:
    """Pattern value with one edit: expected serialize() outcome and validate() status."""

    name: str
    edit: Edit
    encoded: Outcome
    validated: str


@dataclass(frozen=True)
class DecodeCase:
    """Payload bytes and the status deserialize() must return."""

    name: str
    payload: bytes
    status: str


@dataclass(frozen=True)
class MessageCases:
    """All cases of one message in one profile."""

    message: Struct
    pattern: dict[str, Any]
    default_encoded: Outcome
    default_validated: str
    encodes: tuple[EncodeCase, ...]
    decodes: tuple[DecodeCase, ...]


@dataclass(frozen=True)
class InterfaceCases:
    """Frames of one interface in one profile.

    `frames` holds the pattern frame of every message (index into the interface's message list).
    `corrupt` is the first frame with its last byte inverted and `corrupt_status` the status it
    is rejected with (`Ok` when the change is harmless). `exact` is false when a frame decoder
    that resynchronizes could find a frame start inside the corrupted frame, so the number of
    errors and the recovery are not predictable byte by byte. `default_frame_size` is the frame
    size of the first message's default value, `overhead` the size of header and trailer.
    """

    interface: Interface
    frames: tuple[tuple[int, bytes], ...]
    corrupt: bytes
    corrupt_status: str
    exact: bool
    sync: bytes
    default_frame_size: int
    overhead: int


@dataclass(frozen=True)
class ProfileCases:
    """Cases of one profile (`None` for a project without profiles)."""

    profile: str | None
    messages: tuple[MessageCases, ...]
    interfaces: tuple[InterfaceCases, ...]


@dataclass(frozen=True)
class _Slot:
    """A member reachable from the message value: C++ path and JSON path."""

    field: Field
    target: str
    path: tuple[str | int, ...]
    element: bool


def _outcome(action: Callable[[], bytes]) -> Outcome:
    try:
        return action()
    except CodecError as error:
        return error.status


def _status(action: Callable[[], object]) -> str:
    try:
        action()
    except CodecError as error:
        return error.status
    return OK


def _holds(condition: str | None, variant: Variant) -> bool:
    if condition is None:
        return True
    try:
        return expr.evaluate(expr.parse_condition(condition), variant.scope) != 0
    except ExprError:
        return True


def _identifier(target: str) -> str:
    return target.replace("[", "_").replace("U]", "").replace("]", "").replace(".", "_")


def _f32(value: float) -> float:
    return float(struct.unpack(">f", struct.pack(">f", value))[0])


def _replace(value: object, path: tuple[str | int, ...], new: object) -> object:
    """Return a copy of a JSON-like value with the element at `path` replaced."""
    if not path:
        return new
    head, rest = path[0], path[1:]
    if isinstance(value, dict) and isinstance(head, str):
        return {**value, head: _replace(value[head], rest, new)}
    if isinstance(value, list) and isinstance(head, int):
        copy = list(value)
        copy[head] = _replace(copy[head], rest, new)
        return copy
    message = f"cannot follow {path!r}"
    raise TypeError(message)


def _edited(pattern: dict[str, Any], slot: _Slot, value: Scalar | Clear) -> dict[str, Any]:
    """Return the pattern value with the member at `slot` set to `value`."""
    new: object = value
    if isinstance(value, Clear):
        new = "" if slot.field.type == "char" else []
    edited = _replace(pattern, slot.path, new)
    if not isinstance(edited, dict):
        message = "a message value is a mapping"
        raise TypeError(message)
    return edited


class _ProfileOracle:
    """Computes the cases of one profile."""

    def __init__(self, project: Project, profile: str | None, plugins: dict[str, Any]) -> None:
        self.project = project
        self.profile = profile
        self.codec = ReferenceCodec(project, profile, plugins)
        self.sampler = Sampler(self.codec)
        self.names = Names(project)

    # --- messages -------------------------------------------------------------------------------

    def messages(self) -> tuple[MessageCases, ...]:
        variant = self.codec.variant
        return tuple(
            self.message(item)
            for item in self.project.messages.values()
            if _holds(item.condition, variant)
        )

    def encode(self, message: str, value: dict[str, Any]) -> Outcome:
        return _outcome(lambda: self.codec.encode_message(message, value))

    def validated(self, message: str, value: dict[str, Any]) -> str:
        settings = dataclasses.replace(self.codec.message_settings(message), check_encode=True)
        return _status(lambda: self.codec.encode_message(message, value, settings=settings))

    def message(self, item: Struct) -> MessageCases:
        pattern = self.sampler.struct(item.fields)
        default = self.codec.default_value(item.name)
        encoded = self.encode(item.name, pattern)
        cases: list[EncodeCase] = []
        for slot in self.slots(item, pattern, "", ()):
            for suffix, value in self.edits(slot):
                edited = _edited(pattern, slot, value)
                cases.append(
                    EncodeCase(
                        f"{_identifier(slot.target)}_{suffix}",
                        Edit(slot.field, slot.target, value),
                        self.encode(item.name, edited),
                        self.validated(item.name, edited),
                    )
                )
        decodes = self.decodes(item.name, encoded) if isinstance(encoded, bytes) else ()
        return MessageCases(
            item,
            pattern,
            self.encode(item.name, default),
            self.validated(item.name, default),
            tuple(cases),
            decodes,
        )

    def slots(
        self, item: Struct, value: dict[str, Any], prefix: str, path: tuple[str | int, ...]
    ) -> Iterator[_Slot]:
        hidden = wire_only(item)
        for field in item.fields:
            if field.name in hidden or field.name not in value:
                continue
            target = f"{prefix}{field.name}"
            here = (*path, field.name)
            inner = self.project.types.get(field.type)
            current = value[field.name]
            if field.occurs is None:
                if inner is not None:
                    yield from self.slots(inner, current, f"{target}.", here)
                else:
                    yield _Slot(field, target, here, element=False)
                continue
            yield _Slot(field, target, here, element=False)
            if not isinstance(current, list) or not current:
                continue
            if inner is not None:
                yield from self.slots(inner, current[0], f"{target}[0U].", (*here, 0))
            else:
                yield _Slot(field, f"{target}[0U]", (*here, 0), element=True)

    def edits(self, slot: _Slot) -> list[tuple[str, Scalar | Clear]]:
        item = slot.field
        if item.occurs is not None and not slot.element:
            occurs = item.occurs
            dynamic = occurs.count is not None or occurs.fill or item.type == "char"
            return [("empty", CLEAR)] if dynamic else []
        if item.type in self.project.user_types:
            return []  # opaque N-byte value: no boundary/mutation strategy
        if item.type in self.project.enums:
            return self.enum_edits(item)
        if item.type in FLOAT_TYPES:
            return self.float_edits(item)
        if item.type in {"bool", "char"}:
            return []
        return self.int_edits(item)

    def enum_edits(self, item: Field) -> list[tuple[str, Scalar | Clear]]:
        """Every member once (each is a valid value), then the first value that is no member."""
        enum = self.project.enums[item.type]
        limits = self.names.type_limits(item)
        members = set(enum.values.values())
        high = limits[1] if limits is not None else 0
        invalid = next((value for value in range(high + 1) if value not in members), None)
        edits: list[tuple[str, Scalar | Clear]] = [
            (f"member_{name}", value) for name, value in enum.values.items()
        ]
        if invalid is not None:
            edits.append(("invalid", invalid))
        return edits

    def int_edits(self, item: Field) -> list[tuple[str, Scalar | Clear]]:
        low, high = self.codec.int_range(item)
        lower, upper = self.codec.bound(item.min), self.codec.bound(item.max)
        if lower is not None:
            low = max(low, int(lower))
        if upper is not None:
            high = min(high, int(upper))
        limits = self.names.type_limits(item)
        candidates = [("min", low), ("max", high)]
        if limits is not None and low - 1 >= limits[0]:
            candidates.append(("below_min", low - 1))
        if limits is not None and high + 1 <= limits[1]:
            candidates.append(("above_max", high + 1))
        return _unique(candidates)

    def float_edits(self, item: Field) -> list[tuple[str, Scalar | Clear]]:
        lower, upper = self.codec.bound(item.min), self.codec.bound(item.max)
        candidates: list[tuple[str, float]] = []
        if lower is not None:
            candidates += [("min", lower), ("below_min", lower - 1.0)]
        if upper is not None:
            candidates += [("max", upper), ("above_max", upper + 1.0)]
        if item.type == "f32":
            candidates = [
                (name, _f32(value)) for name, value in candidates if abs(value) <= FLOAT32_MAX
            ]
        return _unique(candidates)

    def decodes(self, message: str, payload: bytes) -> tuple[DecodeCase, ...]:
        def failure(data: bytes) -> tuple[str, str]:
            try:
                self.codec.decode_message(message, data)
            except CodecError as error:
                return error.status, error.path
            return OK, ""

        def status(data: bytes) -> str:
            return failure(data)[0]

        cases: dict[tuple[str, str], DecodeCase] = {}
        for index, original in enumerate(payload):
            for name, change in MUTATIONS:
                data = payload[:index] + bytes([change(original)]) + payload[index + 1 :]
                found = failure(data)
                if found[0] != OK and found not in cases:
                    cases[found] = DecodeCase(f"byte{index}_{name}", data, found[0])
        extra = [DecodeCase("extended", payload + b"\x00", status(payload + b"\x00"))]
        if payload:
            extra.insert(0, DecodeCase("truncated", payload[:-1], status(payload[:-1])))
        return (*cases.values(), *extra)

    # --- interfaces -----------------------------------------------------------------------------

    def interfaces(self) -> tuple[InterfaceCases, ...]:
        result = []
        for _, interface in sorted(self.project.interfaces.items()):
            cases = self.interface(interface)
            if cases is not None:
                result.append(cases)
        return tuple(result)

    def interface(self, interface: Interface) -> InterfaceCases | None:
        frames: list[tuple[int, bytes]] = []
        variant = self.codec.variant
        for index, entry in enumerate(interface.messages):
            message = self.project.messages[entry.name]
            if not _holds(entry.condition, variant) or not _holds(message.condition, variant):
                continue
            frame = self.frame(interface, message)
            if isinstance(frame, bytes) and frame:
                frames.append((index, frame))
        if not frames:
            return None
        first = frames[0][1]
        corrupt = first[:-1] + bytes([first[-1] ^ BYTE_MASK])
        status = _status(lambda: self.codec.decode_frame(interface.name, corrupt))
        sync = self.sync_bytes(interface)
        resync = interface.framing.resync and interface.framing.kind != "none"
        exact = not resync or (bool(sync) and sync not in corrupt[1:])
        first_message = interface.messages[frames[0][0]].name
        default = _outcome(lambda: self.codec.encode_frame(interface.name, first_message))
        size = len(default) if isinstance(default, bytes) else 0
        payload = self.codec.encode_message(first_message, None, interface.name)
        overhead = size - len(payload) if size else 0
        return InterfaceCases(
            interface, tuple(frames), corrupt, status, exact, sync, size, overhead
        )

    def frame(self, interface: Interface, message: Struct) -> Outcome:
        pattern = self.sampler.struct(message.fields)
        return _outcome(lambda: self.codec.encode_frame(interface.name, message.name, pattern))

    def sync_bytes(self, interface: Interface) -> bytes:
        settings = self.codec.interface_settings(interface)
        order: Literal["big", "little"] = "big" if settings.endian == "big" else "little"
        result = b""
        for item in interface.header or ():
            if item.role == "sync" and isinstance(item.const, int):
                width = BUILTIN_BITS[item.type] // BYTE_BITS
                result += item.const.to_bytes(width, order)
        return result


def _unique(candidates: list[tuple[str, Any]]) -> list[tuple[str, Scalar | Clear]]:
    seen: set[object] = set()
    result: list[tuple[str, Scalar | Clear]] = []
    for name, value in candidates:
        if value not in seen:
            seen.add(value)
            result.append((name, value))
    return result


def cases(
    project: Project, plugins: dict[str, ChecksumPlugin] | None = None
) -> tuple[ProfileCases, ...]:
    """Cases of every profile of a valid project (one entry without profiles)."""
    result = []
    profiles: list[str | None] = [*project.profiles] or [None]
    for profile in profiles:
        oracle = _ProfileOracle(project, profile, dict(plugins or {}))
        result.append(ProfileCases(profile, oracle.messages(), oracle.interfaces()))
    return tuple(result)
