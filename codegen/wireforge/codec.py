"""Reference codec: encodes and decodes any message or frame of a project by interpreting the model.

It is the oracle the generated C++ is tested against, and the playground backend. Values are plain
Python data:

- integers, floats and bools as such; enum fields as member names (encoding also accepts integers);
- `char` arrays as `str` (fixed-size ones are zero padded on the wire, trailing zeros are stripped
  on decode);
- fields with `occurs` as lists (an optional field is a list of length 0 or 1);
- struct fields as dicts.

Missing fields take their default. When `occurs.count` is the name of a single field, the encoder
writes the list length into that field; other count expressions must equal the list length.
Errors raise CodecError carrying the runtime status name (e.g. "OutOfRange").
"""

from __future__ import annotations

import dataclasses
import math
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol

from wireforge import expr
from wireforge.expr import ExprError, Scope
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
from wireforge.validate import Validator, Variant

if TYPE_CHECKING:
    from collections.abc import Mapping

    from typing_extensions import Self

BYTE_BITS = 8
FLOAT32_MAX = struct.unpack(">f", bytes.fromhex("7f7fffff"))[0]
CRC32 = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)


class WireNaN(float):
    """A NaN read from the wire that keeps its exact bit pattern (`bits`, big-endian).

    Encoding a WireNaN of the same width writes these bits again, so NaN payloads (signaling
    NaNs included) round-trip bit-exactly like in the generated code.
    """

    bits: bytes

    def __new__(cls, bits: bytes) -> Self:
        """Create the NaN with the big-endian bit pattern `bits`."""
        value = super().__new__(cls, float("nan"))
        value.bits = bits
        return value


class CodecError(Exception):
    """Encoding or decoding failed; `status` is the runtime status name, `path` the field."""

    def __init__(self, status: str, path: str, detail: str) -> None:
        """Store the status, the field path and a human-readable detail."""
        super().__init__(f"{status} at {path or '/'}: {detail}")
        self.status = status
        self.path = path


def _fail(status: str, path: str, detail: str) -> NoReturn:
    raise CodecError(status, path, detail)


class ChecksumPlugin(Protocol):
    """Implementation of a custom checksum algorithm."""

    def __call__(self, data: bytes) -> int:
        """Return the checksum of `data` as an unsigned integer."""
        ...


def crc(checksum: Checksum, data: bytes) -> int:
    """Rocksoft-model CRC with the parameters of a `crc` or `crc32` declaration."""
    if checksum.algo == "crc32":
        width, poly, register, refin, refout, xorout = CRC32
    else:
        width, poly, register = checksum.width or 0, checksum.poly or 0, checksum.init
        refin, refout, xorout = checksum.refin, checksum.refout, checksum.xorout
    top = 1 << (width - 1)
    mask = (1 << width) - 1
    for byte in data:
        value = int(f"{byte:08b}"[::-1], 2) if refin else byte
        for bit in range(7, -1, -1):
            feedback = bool(register & top) ^ bool((value >> bit) & 1)
            register = ((register << 1) & mask) ^ (poly if feedback else 0)
    if refout:
        register = int(f"{register:0{width}b}"[::-1], 2)
    return register ^ xorout


@dataclass(frozen=True)
class Settings:
    """Effective byte order, bit order and range checks of one message (or header)."""

    endian: str = "big"
    bit_order: str = "msb_first"
    check_encode: bool = True
    check_decode: bool = True
    allow_trailing: bool = False


@dataclass
class _Struct:
    """One struct being encoded or decoded: its fields, values seen so far and byte ranges."""

    fields: tuple[Field, ...]
    start: int
    path: str
    values: dict[str, Any] = field(default_factory=dict)
    ranges: dict[str, tuple[int, int]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _Reader:
    """Cursor over the bytes being decoded."""

    data: bytes
    position: int = 0

    def take(self, count: int, path: str) -> bytes:
        if len(self.data) - self.position < count:
            _fail("Truncated", path, f"needs {count} more byte(s)")
        chunk = self.data[self.position : self.position + count]
        self.position += count
        return chunk

    @property
    def remaining(self) -> int:
        return len(self.data) - self.position


@dataclass(frozen=True)
class _Scopes:
    """Bytes covered by frame-level checksum scopes (payload, header_and_payload)."""

    payload: bytes = b""
    header_and_payload: bytes = b""


@dataclass(frozen=True)
class _Ctx:
    """Settings and checksum scopes shared by all fields of one encode or decode call."""

    settings: Settings
    scopes: _Scopes = field(default_factory=_Scopes)


@dataclass(frozen=True)
class DecodedFrame:
    """Result of decode_frame."""

    message: str
    value: dict[str, Any]
    header: dict[str, Any]
    trailer: dict[str, Any]


def _range_text(number: float, low: float | None, high: float | None) -> str:
    """Return the message for a value outside the bounds of its field."""
    if low is None:
        return f"{number} is above the maximum {high}"
    if high is None:
        return f"{number} is below the minimum {low}"
    return f"{number} is outside [{low}, {high}]"


def _int_bytes(number: int, width: int, settings: Settings) -> bytes:
    order: Literal["big", "little"] = "big" if settings.endian == "big" else "little"
    return (number % (1 << (width * BYTE_BITS))).to_bytes(width, order)


def _bytes_int(data: bytes, settings: Settings) -> int:
    return int.from_bytes(data, "big" if settings.endian == "big" else "little")


def _plain_name(count: Value | None) -> str | None:
    return count.strip() if isinstance(count, str) and count.strip().isidentifier() else None


class ReferenceCodec:
    """Interprets the model of one project variant (profile)."""

    def __init__(
        self,
        project: Project,
        profile: str | None = None,
        plugins: Mapping[str, ChecksumPlugin] | None = None,
    ) -> None:
        """Select the profile (required when the project defines profiles).

        `plugins` maps the `impl` name of custom checksums to their implementation.
        """
        self.project = project
        self.validator = Validator(project)
        self.plugins = dict(plugins or {})
        variants = {variant.name: variant for variant in self.validator.variants}
        if (profile or "") not in variants:
            message = f"unknown profile {profile!r}; choose one of {sorted(variants)}"
            raise ValueError(message)
        self.variant: Variant = variants[profile or ""]

    # --- model access ---------------------------------------------------------------------------

    def message_settings(self, message: str, interface: str | None = None) -> Settings:
        """Effective settings of a message on an interface (or with project defaults only)."""
        info = self._message(message).message
        iface = self.project.interfaces.get(interface or "")
        base = self.interface_settings(iface)
        checks = {"encode": base.check_encode, "decode": base.check_decode}
        checks.update((info.range_check if info else None) or {})
        return Settings(
            endian=(info.endian if info else None) or base.endian,
            bit_order=(info.bit_order if info else None) or base.bit_order,
            check_encode=checks["encode"],
            check_decode=checks["decode"],
            allow_trailing=base.allow_trailing,
        )

    def interface_settings(self, interface: Interface | None) -> Settings:
        """Return the settings of an interface's header and trailer (defaults of its messages)."""
        defaults = self.project.defaults
        checks: dict[str, Any] = {"encode": True, "decode": True}
        configured = defaults.get("range_check")
        checks.update(configured if isinstance(configured, dict) else {})
        endian = (interface.endian if interface else None) or defaults.get("endian") or "big"
        order = (interface.bit_order if interface else None) or defaults.get("bit_order")
        return Settings(
            endian=str(endian),
            bit_order=str(order or "msb_first"),
            check_encode=bool(checks["encode"]),
            check_decode=bool(checks["decode"]),
            allow_trailing=bool(interface.allow_trailing_bytes) if interface else False,
        )

    def _message(self, name: str) -> Struct:
        item = self.project.messages.get(name)
        if item is None:
            _fail("InvalidState", name, "unknown message")
        return item

    def _interface(self, name: str) -> Interface:
        item = self.project.interfaces.get(name)
        if item is None:
            _fail("InvalidState", name, "unknown interface")
        return item

    def _static(self, raw: Value, path: str) -> int:
        value = self.validator.static_value(raw, self.variant)
        if value is None:
            _fail("InvalidState", path, f"cannot evaluate {raw!r}")
        return value

    def _active(self, fields: tuple[Field, ...]) -> tuple[Field, ...]:
        return tuple(item for item in fields if self.validator.present(item, self.variant))

    def _pure_bits(self, type_name: str) -> Struct | None:
        item = self.project.types.get(type_name)
        if item is None:
            return None
        active = self._active(item.fields)
        return item if active and all(inner.bits is not None for inner in active) else None

    def _in_group(self, item: Field) -> bool:
        if item.occurs is not None:
            return False
        return item.bits is not None or self._pure_bits(item.type) is not None

    def _dynamic(self, item: Field) -> bool:
        return item.occurs is not None and (item.occurs.count is not None or item.occurs.fill)

    def _capacity(self, item: Field) -> int:
        return 1 if item.occurs is None else self._static(item.occurs.max, item.name)

    def _base(self, item: Field) -> str:
        enum = self.project.enums.get(item.type)
        return enum.type if enum is not None else item.type

    # --- default values -------------------------------------------------------------------------

    def default_value(self, message: str) -> dict[str, Any]:
        """Return the value `init()` produces for a message."""
        return self._default_struct(self._message(message).fields)

    def _default_struct(self, fields: tuple[Field, ...]) -> dict[str, Any]:
        return {item.name: self._default_field(item) for item in self._active(fields)}

    def _default_field(self, item: Field) -> object:
        if item.occurs is None:
            return self._default_scalar_field(item)
        return self._default_array_field(item)

    def _default_scalar_field(self, item: Field) -> object:
        user = self.project.user_types.get(item.type)
        if user is not None:
            return bytes(user.size_bytes)
        if item.type in self.project.types:
            return self._default_struct(self.project.types[item.type].fields)
        return self._zero(item) if item.default is None else item.default

    def _default_array_field(self, item: Field) -> object:
        if item.type == "char":
            return str(item.default or "")
        if self._dynamic(item):
            return list(item.default) if isinstance(item.default, list) else []
        user = self.project.user_types.get(item.type)
        if user is not None:
            return [bytes(user.size_bytes) for _ in range(self._capacity(item))]
        if item.type in self.project.types:
            single = self._default_struct(self.project.types[item.type].fields)
            return [dict(single) for _ in range(self._capacity(item))]
        given = item.default if isinstance(item.default, list) else []
        filler = item.default if item.default is not None and not given else self._zero(item)
        return given + [filler] * (self._capacity(item) - len(given))

    def _zero(self, item: Field) -> object:
        if item.const is not None:
            return item.const
        enum = self.project.enums.get(item.type)
        if enum is not None:
            return next((name for name, value in enum.values.items() if value == 0), 0)
        if item.type == "bool":
            return False
        return 0.0 if item.type in FLOAT_TYPES else 0

    # --- scalar values --------------------------------------------------------------------------

    def int_range(self, item: Field) -> tuple[int, int]:
        """Return the integer range of a field's wire representation (bits included)."""
        if item.type == "bool":
            return 0, 1
        base = self._base(item)
        bits = item.bits or BUILTIN_BITS[base]
        if base in SIGNED_TYPES:
            return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        return 0, (1 << bits) - 1

    def bound(self, raw: Value | None) -> float | None:
        """Return a min/max bound in this profile; None when absent or opaque."""
        if raw is None or isinstance(raw, (int, float)):
            return raw
        return self.validator.static_value(raw, self.variant)

    def _to_int(self, item: Field, value: object, path: str) -> int:
        enum = self.project.enums.get(item.type)
        number: int | None = None
        if enum is not None and isinstance(value, str):
            number = enum.values.get(value)
        elif item.type == "bool" and isinstance(value, bool):
            number = int(value)
        elif item.type == "char" and isinstance(value, str) and len(value) == 1:
            number = ord(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            number = value
        if number is None:
            _fail("OutOfRange", path, f"{value!r} is not a valid {item.type} value")
        low, high = self.int_range(item)
        if not low <= number <= high:
            _fail("OutOfRange", path, f"{value!r} does not fit {item.type}")
        return number

    def _check_range(self, item: Field, number: float, path: str, *, enabled: bool) -> None:
        if not enabled:
            return
        low, high = self.bound(item.min), self.bound(item.max)
        # Written as negated >= / <= so that NaN is out of range, like the generated C++.
        below = low is not None and not number >= low
        above = high is not None and not number <= high
        if below or above:
            _fail("OutOfRange", path, _range_text(number, low, high))
        enum = self.project.enums.get(item.type)
        if enum is not None and number not in enum.values.values():
            _fail("OutOfRange", path, f"{number} is not a member of {item.type}")

    def _from_int(self, item: Field, number: int, path: str, settings: Settings) -> object:
        if item.type == "bool":
            if number > 1:
                _fail("OutOfRange", path, f"bool value {number}")
            return number == 1
        self._check_range(item, number, path, enabled=settings.check_decode)
        if item.type == "char":
            return chr(number)
        enum = self.project.enums.get(item.type)
        if enum is not None:
            return next((name for name, value in enum.values.items() if value == number), number)
        return number

    def _encode_float(
        self, out: bytearray, item: Field, value: object, path: str, settings: Settings
    ) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail("OutOfRange", path, f"{value!r} is not a number")
        number = float(value)
        if item.type == "f32" and FLOAT32_MAX < abs(number) < float("inf"):
            _fail("OutOfRange", path, f"{number} does not fit f32")
        self._check_range(item, number, path, enabled=settings.check_encode)
        size = BUILTIN_BITS[item.type] // BYTE_BITS
        if isinstance(value, WireNaN) and len(value.bits) == size:
            out += value.bits if settings.endian == "big" else value.bits[::-1]
            return
        code = (">" if settings.endian == "big" else "<") + ("f" if item.type == "f32" else "d")
        out += struct.pack(code, number)

    def _encode_scalar(
        self, out: bytearray, item: Field, value: object, path: str, settings: Settings
    ) -> None:
        if item.type in FLOAT_TYPES:
            self._encode_float(out, item, value, path, settings)
            return
        number = self._to_int(item, value, path)
        self._check_range(item, number, path, enabled=settings.check_encode)
        out += _int_bytes(number, BUILTIN_BITS[self._base(item)] // BYTE_BITS, settings)

    def _decode_scalar(self, reader: _Reader, item: Field, path: str, settings: Settings) -> object:
        if item.type in FLOAT_TYPES:
            size = BUILTIN_BITS[item.type] // BYTE_BITS
            code = (">" if settings.endian == "big" else "<") + ("f" if item.type == "f32" else "d")
            data = reader.take(size, path)
            number = struct.unpack(code, data)[0]
            self._check_range(item, number, path, enabled=settings.check_decode)
            if math.isnan(number):
                return WireNaN(data if settings.endian == "big" else data[::-1])
            return number
        base = self._base(item)
        width = BUILTIN_BITS[base] // BYTE_BITS
        raw = _bytes_int(reader.take(width, path), settings)
        if base in SIGNED_TYPES and raw >= 1 << (width * BYTE_BITS - 1):
            raw -= 1 << (width * BYTE_BITS)
        return self._from_int(item, raw, path, settings)

    # --- checksums ------------------------------------------------------------------------------

    def _checksum(self, name: str, data: bytes, path: str) -> int:
        algo = self.project.checksums.get(name)
        if algo is None:
            _fail("InvalidState", path, f"unknown checksum {name!r}")
        if algo.algo != "custom":
            return crc(algo, data)
        plugin = self.plugins.get(algo.impl or "")
        if plugin is None:
            _fail("InvalidState", path, f"no plugin for custom checksum {algo.impl!r}")
        return plugin(data)

    def _checksum_value(
        self, item: Field, state: _Struct, data: bytes, position: int, scopes: _Scopes
    ) -> int:
        use = item.checksum
        path = f"{state.path}/{item.name}"
        if use is None:
            _fail("InvalidState", path, "not a checksum field")
        if use.scope == "payload":
            covered = scopes.payload
        elif use.scope == "header_and_payload":
            covered = scopes.header_and_payload
        elif use.scope == "preceding":
            covered = data[state.start : position]
        else:
            first = state.ranges[use.first or ""][0]
            covered = data[first : state.ranges[use.last or ""][1]]
        return self._checksum(use.use, bytes(covered), path)

    # --- encoding -------------------------------------------------------------------------------

    def _sequence(self, item: Field, value: object, path: str) -> list[Any]:
        if isinstance(value, str) and item.type == "char":
            return list(value)
        if isinstance(value, list):
            return value
        _fail("InvalidState", path, f"{item.name} needs a list")

    def _count_targets(self, state: _Struct, value: dict[str, Any]) -> dict[str, int]:
        """Count fields filled from list lengths: count field name -> element count."""
        targets: dict[str, int] = {}
        for item in state.fields:
            name = _plain_name(item.occurs.count if item.occurs is not None else None)
            if name is not None:
                path = f"{state.path}/{item.name}"
                current = value.get(item.name, self._default_field(item))
                targets[name] = len(self._sequence(item, current, path))
                self._check_count(item, targets[name], path)
        return targets

    def _field_value(self, state: _Struct, item: Field, value: dict[str, Any]) -> object:
        if item.name in state.counts:
            count = state.counts[item.name]
            return bool(count) if item.type == "bool" else count
        if item.const is not None:
            return item.const
        if item.reserved:
            return self._zero(item) if item.default is None else item.default
        return value[item.name] if item.name in value else self._default_field(item)

    def _encode_struct(
        self, out: bytearray, fields: tuple[Field, ...], value: object, path: str, ctx: _Ctx
    ) -> None:
        if not isinstance(value, dict):
            _fail("InvalidState", path, "a struct value must be a mapping")
        state = _Struct(self._active(fields), len(out), path)
        state.counts = self._count_targets(state, value)
        index = 0
        while index < len(state.fields):
            item = state.fields[index]
            if self._in_group(item):
                index = self._encode_group(out, state, index, value, ctx.settings)
                continue
            start = len(out)
            item_path = f"{path}/{item.name}"
            if item.checksum is not None:
                number = self._checksum_value(item, state, bytes(out), start, ctx.scopes)
                state.values[item.name] = number
                self._encode_scalar(out, item, number, item_path, ctx.settings)
            else:
                current = self._field_value(state, item, value)
                state.values[item.name] = current
                if item.occurs is None:
                    self._encode_element(out, item, current, item_path, ctx)
                else:
                    self._encode_occurs(out, state, item, current, ctx)
            state.ranges[item.name] = (start, len(out))
            index += 1

    def _encode_element(
        self, out: bytearray, item: Field, value: object, path: str, ctx: _Ctx
    ) -> None:
        user = self.project.user_types.get(item.type)
        if user is not None:
            if not isinstance(value, bytes) or len(value) != user.size_bytes:
                _fail("InvalidState", path, f"{item.name} needs exactly {user.size_bytes} bytes")
            out += value
            return
        inner = self.project.types.get(item.type)
        if inner is not None:
            self._encode_struct(out, inner.fields, value, path, ctx)
        else:
            self._encode_scalar(out, item, value, path, ctx.settings)

    def _encode_occurs(
        self, out: bytearray, state: _Struct, item: Field, value: object, ctx: _Ctx
    ) -> None:
        path = f"{state.path}/{item.name}"
        elements = self._sequence(item, value, path)
        capacity = self._capacity(item)
        if item.type == "char" and not self._dynamic(item) and len(elements) <= capacity:
            elements = elements + ["\0"] * (capacity - len(elements))
        if not self._dynamic(item) and len(elements) != capacity:
            text = f"exactly {capacity} elements required, got {len(elements)}"
            _fail("CountExceedsMax", path, text)
        self._check_count(item, len(elements), path)
        occurs = item.occurs
        count = occurs.count if occurs is not None else None
        if count is not None and _plain_name(count) is None:
            expected = self._count(item, state, path)
            if expected != len(elements):
                text = f"count expression gives {expected}, list has {len(elements)}"
                _fail("CountExceedsMax", path, text)
        for position, element in enumerate(elements):
            self._encode_element(out, item, element, f"{path}/{position}", ctx)

    # --- bit groups -----------------------------------------------------------------------------

    def _group_leaves(
        self, state: _Struct, index: int, value: dict[str, Any]
    ) -> tuple[list[tuple[Field, object, str]], int]:
        leaves: list[tuple[Field, object, str]] = []
        end = index
        while end < len(state.fields) and self._in_group(state.fields[end]):
            item = state.fields[end]
            path = f"{state.path}/{item.name}"
            if item.bits is not None:
                current = self._field_value(state, item, value)
                state.values[item.name] = current
                leaves.append((item, current, path))
            else:
                inner = self._pure_bits(item.type)
                given = value.get(item.name, {})
                nested = _Struct(self._active(inner.fields if inner else ()), 0, path)
                sub = given if isinstance(given, dict) else {}
                leaves += [
                    (leaf, self._field_value(nested, leaf, sub), f"{path}/{leaf.name}")
                    for leaf in nested.fields
                ]
            end += 1
        return leaves, end

    def _encode_group(
        self, out: bytearray, state: _Struct, index: int, value: dict[str, Any], settings: Settings
    ) -> int:
        leaves, end = self._group_leaves(state, index, value)
        total = sum(leaf.bits or 0 for leaf, _, _ in leaves)
        container = 0
        used = 0
        for leaf, current, path in leaves:
            bits = leaf.bits or 0
            number = self._to_int(leaf, current, path)
            self._check_range(leaf, number, path, enabled=settings.check_encode)
            shift = total - used - bits if settings.bit_order == "msb_first" else used
            container |= (number % (1 << bits)) << shift
            used += bits
        start = len(out)
        out += _int_bytes(container, total // BYTE_BITS, settings)
        for position in range(index, end):
            state.ranges[state.fields[position].name] = (start, len(out))
        return end

    def _decode_group(
        self,
        reader: _Reader,
        state: _Struct,
        index: int,
        result: dict[str, Any],
        settings: Settings,
    ) -> int:
        leaves, end = self._group_leaves(state, index, {})
        total = sum(leaf.bits or 0 for leaf, _, _ in leaves)
        start = reader.position
        container = _bytes_int(reader.take(total // BYTE_BITS, state.path), settings)
        used = 0
        for leaf, _, path in leaves:
            bits = leaf.bits or 0
            shift = total - used - bits if settings.bit_order == "msb_first" else used
            raw = (container >> shift) & ((1 << bits) - 1)
            if self._base(leaf) in SIGNED_TYPES and raw >= 1 << (bits - 1):
                raw -= 1 << bits
            used += bits
            decoded = self._from_int(leaf, raw, path, settings)
            self._check_const(leaf, decoded, path)
            parts = path[len(state.path) + 1 :].split("/")
            target = result.setdefault(parts[0], {}) if len(parts) > 1 else result
            target[parts[-1]] = decoded
        for position in range(index, end):
            name = state.fields[position].name
            state.ranges[name] = (start, reader.position)
            state.values[name] = result.get(name)
        return end

    # --- decoding -------------------------------------------------------------------------------

    def _check_const(self, item: Field, decoded: object, path: str) -> None:
        if item.const is None:
            return
        if item.type in FLOAT_TYPES:
            same = decoded == item.const
        else:
            same = self._to_int(item, decoded, path) == self._to_int(item, item.const, path)
        if not same:
            _fail("ConstMismatch", path, f"{decoded!r} != {item.const!r}")

    def _scope(self, state: _Struct) -> Scope:
        values = dict(self.variant.scope.values)
        for item in state.fields:
            current = state.values.get(item.name)
            if isinstance(current, (bool, int)):
                values[item.name] = int(current)
            elif isinstance(current, str) and item.type in self.project.enums:
                member = self.project.enums[item.type].values.get(current)
                if member is not None:
                    values[item.name] = member
        return dataclasses.replace(self.variant.scope, values=values)

    def _count(self, item: Field, state: _Struct, path: str) -> int:
        count = item.occurs.count if item.occurs is not None else None
        if count is None:
            return self._capacity(item)
        try:
            node = expr.as_expression(count) if not isinstance(count, float) else expr.Int(0)
            return expr.evaluate(node, self._scope(state))
        except ExprError as error:
            status = "InvalidState"
            raise CodecError(status, path, f"count: {error}") from error

    def _check_count(self, item: Field, count: int, path: str) -> None:
        occurs = item.occurs
        minimum = 0 if occurs is None or occurs.min is None else self._static(occurs.min, path)
        if not minimum <= count <= self._capacity(item):
            text = f"count {count} outside [{minimum}, {self._capacity(item)}]"
            _fail("CountExceedsMax", path, text)

    def _element_size(self, item: Field) -> int:
        user = self.project.user_types.get(item.type)
        if user is not None:
            return user.size_bytes
        inner = self.project.types.get(item.type)
        if inner is None:
            return BUILTIN_BITS[self._base(item)] // BYTE_BITS
        out = bytearray()
        self._encode_struct(
            out, inner.fields, self._default_struct(inner.fields), "", _Ctx(Settings())
        )
        return len(out)

    def _decode_occurs(self, reader: _Reader, state: _Struct, item: Field, ctx: _Ctx) -> object:
        path = f"{state.path}/{item.name}"
        if item.occurs is not None and item.occurs.fill:
            size = self._element_size(item)
            if reader.remaining % size:
                text = f"{reader.remaining} bytes left, not a multiple of {size}"
                _fail("InvalidLength", path, text)
            count = reader.remaining // size
        else:
            count = self._count(item, state, path)
        self._check_count(item, count, path)
        elements = [
            self._decode_element(reader, item, f"{path}/{position}", ctx)
            for position in range(count)
        ]
        if item.type != "char":
            return elements
        text = "".join(str(element) for element in elements)
        return text if self._dynamic(item) else text.rstrip("\0")

    def _decode_element(self, reader: _Reader, item: Field, path: str, ctx: _Ctx) -> object:
        user = self.project.user_types.get(item.type)
        if user is not None:
            return bytes(reader.take(user.size_bytes, path))
        inner = self.project.types.get(item.type)
        if inner is not None:
            return self._decode_struct(reader, inner.fields, path, ctx)
        return self._decode_scalar(reader, item, path, ctx.settings)

    def _decode_struct(
        self, reader: _Reader, fields: tuple[Field, ...], path: str, ctx: _Ctx
    ) -> dict[str, Any]:
        state = _Struct(self._active(fields), reader.position, path)
        result: dict[str, Any] = {}
        index = 0
        while index < len(state.fields):
            item = state.fields[index]
            if self._in_group(item):
                index = self._decode_group(reader, state, index, result, ctx.settings)
                continue
            start = reader.position
            item_path = f"{path}/{item.name}"
            if item.occurs is None:
                current = self._decode_element(reader, item, item_path, ctx)
            else:
                current = self._decode_occurs(reader, state, item, ctx)
            self._check_const(item, current, item_path)
            if item.checksum is not None:
                expected = self._checksum_value(item, state, reader.data, start, ctx.scopes)
                if expected != current:
                    text = f"received {current!r}, computed {expected!r}"
                    _fail("ChecksumMismatch", item_path, text)
            state.ranges[item.name] = (start, reader.position)
            state.values[item.name] = current
            result[item.name] = current
            index += 1
        return result

    # --- messages -------------------------------------------------------------------------------

    def encode_message(
        self,
        message: str,
        value: dict[str, Any] | None = None,
        interface: str | None = None,
        settings: Settings | None = None,
    ) -> bytes:
        """Encode a message payload (no header, no trailer).

        `settings` replaces the effective settings of the message (e.g. to force range checks).
        """
        out = bytearray()
        ctx = _Ctx(settings or self.message_settings(message, interface))
        self._encode_struct(out, self._message(message).fields, value or {}, "", ctx)
        return bytes(out)

    def decode_message(
        self, message: str, data: bytes, interface: str | None = None
    ) -> dict[str, Any]:
        """Decode a message payload; leftover bytes fail unless the interface allows them."""
        settings = self.message_settings(message, interface)
        reader = _Reader(bytes(data))
        result = self._decode_struct(reader, self._message(message).fields, "", _Ctx(settings))
        if reader.remaining and not settings.allow_trailing:
            _fail("TrailingBytes", "", f"{reader.remaining} byte(s) left")
        return result

    # --- frames ---------------------------------------------------------------------------------

    def message_ids(self, interface: str) -> dict[str, int | None]:
        """Effective message ids of an interface (per-interface override or the message id)."""
        ids: dict[str, int | None] = {}
        for entry in self._interface(interface).messages:
            info = self.project.messages[entry.name].message
            ids[entry.name] = entry.id if entry.id is not None else (info.id if info else None)
        return ids

    def _part_size(self, fields: tuple[Field, ...], settings: Settings) -> int:
        out = bytearray()
        self._encode_struct(out, fields, {}, "", _Ctx(settings))
        return len(out)

    def _length_value(self, iface: Interface, sizes: tuple[int, int, int]) -> int:
        header, payload, trailer = sizes
        counts = iface.framing.length_counts
        if counts == "payload":
            return payload
        if counts == "payload_and_trailer":
            return payload + trailer
        return header + payload + trailer

    def _message_size(self, message: str, interface: str) -> int:
        """Wire size of a message of fixed size (SR-25), from its default value."""
        settings = dataclasses.replace(
            self.message_settings(message, interface), check_encode=False
        )
        return len(self.encode_message(message, None, interface, settings))

    def _check_frame_size(self, iface: Interface, size: int) -> None:
        framing = iface.framing
        if framing.kind == "fixed" and framing.frame_size is not None:
            expected = self._static(framing.frame_size, iface.name)
            if size != expected:
                text = f"frame has {size} bytes, fixed framing needs {expected}"
                _fail("InvalidLength", iface.name, text)
        if framing.max_frame_size is not None:
            limit = self._static(framing.max_frame_size, iface.name)
            if size > limit:
                _fail("InvalidLength", iface.name, f"{size} > max_frame_size {limit}")

    def encode_frame(
        self,
        interface: str,
        message: str,
        value: dict[str, Any] | None = None,
        header: dict[str, Any] | None = None,
    ) -> bytes:
        """Encode header, payload and trailer of one message on an interface."""
        iface = self._interface(interface)
        ids = self.message_ids(interface)
        if message not in ids:
            _fail("InvalidState", message, f"not a message of interface {interface}")
        payload = self.encode_message(message, value, interface)
        if iface.trim_trailing_zeros:
            # The trailing zero bytes are not sent; the first byte always is.
            payload = payload[:1] + payload[1:].rstrip(b"\0")
        settings = self.interface_settings(iface)
        header_fields = iface.header or ()
        values = dict(header or {})
        sizes = (
            self._part_size(header_fields, settings),
            len(payload),
            self._part_size(iface.trailer, settings),
        )
        for item in header_fields:
            if item.role == "message_id":
                values[item.name] = ids[message]
            elif item.role == "payload_length":
                values[item.name] = self._length_value(iface, sizes)
        head = bytearray()
        self._encode_struct(
            head, header_fields, values, "/header", _Ctx(settings, _Scopes(payload))
        )
        scopes = _Scopes(payload, bytes(head) + payload)
        tail = bytearray()
        self._encode_struct(tail, iface.trailer, {}, "/trailer", _Ctx(settings, scopes))
        frame = bytes(head) + payload + bytes(tail)
        self._check_frame_size(iface, len(frame))
        return frame

    def _frame_message(self, iface: Interface, header: dict[str, Any]) -> str:
        id_field = next((item for item in iface.header or () if item.role == "message_id"), None)
        ids = self.message_ids(iface.name)
        if id_field is None:
            if len(ids) != 1:
                _fail("InvalidState", iface.name, "no message_id and several messages")
            return next(iter(ids))
        received = header.get(id_field.name)
        for name, identifier in ids.items():
            if identifier == received:
                return name
        _fail("UnknownMessageId", "/header/" + id_field.name, f"id {received!r}")

    def decode_frame(self, interface: str, data: bytes) -> DecodedFrame:
        """Decode one complete frame (as delimited by the frame decoder)."""
        iface = self._interface(interface)
        settings = self.interface_settings(iface)
        header_fields = iface.header or ()
        header_size = self._part_size(header_fields, settings)
        trailer_size = self._part_size(iface.trailer, settings)
        if len(data) < header_size + trailer_size:
            _fail("Truncated", interface, f"frame of {len(data)} bytes")
        self._check_frame_size(iface, len(data))
        payload = bytes(data[header_size : len(data) - trailer_size])
        scopes = _Scopes(payload, bytes(data[: len(data) - trailer_size]))
        ctx = _Ctx(settings, scopes)
        header = self._decode_struct(
            _Reader(bytes(data[:header_size])), header_fields, "/header", ctx
        )
        sizes = (header_size, len(payload), trailer_size)
        for item in header_fields:
            if item.role == "payload_length" and header[item.name] != self._length_value(
                iface, sizes
            ):
                text = f"length field {header[item.name]} does not match the frame"
                _fail("InvalidLength", "/header/" + item.name, text)
        trailer_bytes = bytes(data[len(data) - trailer_size :])
        trailer = self._decode_struct(_Reader(trailer_bytes), iface.trailer, "/trailer", ctx)
        name = self._frame_message(iface, header)
        if iface.trim_trailing_zeros:
            # The receiver reads the bytes that were not sent as zeros.
            payload = payload.ljust(self._message_size(name, interface), b"\0")
        return DecodedFrame(name, self.decode_message(name, payload, interface), header, trailer)
