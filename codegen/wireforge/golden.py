"""Golden vectors: reference encodings of every message of a project, per profile and interface.

Each vector holds a value, its payload bytes and its complete frame. Two samples are produced per
message: `default` (the `init()` value) and `pattern` (every field set to a non-trivial value
inside its range, arrays filled to capacity, optional fields present). Vectors are stored as JSON
and are the shared truth for the reference codec and the generated C++ tests.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wireforge import expr
from wireforge.codec import CodecError, ReferenceCodec
from wireforge.expr import ExprError
from wireforge.loader import load_project
from wireforge.model import FLOAT_TYPES, Field, Project

if TYPE_CHECKING:
    from wireforge.codec import ChecksumPlugin
    from wireforge.validate import Variant

FLOAT_SPAN = 1000.0
CHAR_PATTERN = "WireForge"


def _f32(value: float) -> float:
    return float(struct.unpack(">f", struct.pack(">f", value))[0])


class Sampler:
    """Builds the `pattern` sample of a message."""

    def __init__(self, codec: ReferenceCodec) -> None:
        """Sample values in the profile of `codec`."""
        self.codec = codec
        self.project = codec.project

    def struct(self, fields: tuple[Field, ...]) -> dict[str, Any]:
        """Return the pattern value of a struct with these fields."""
        value: dict[str, Any] = {}
        for item in fields:
            if not self.codec.validator.present(item, self.codec.variant):
                continue
            if item.checksum is not None or item.const is not None or item.reserved:
                continue
            value[item.name] = self.field(item, value)
        return value

    def length(self, item: Field, value: dict[str, Any]) -> int:
        """Return the element count of an array (capacity, or its count expression)."""
        occurs = item.occurs
        capacity = (
            self.codec.validator.static_value(occurs.max, self.codec.variant) if occurs else 1
        )
        count = occurs.count if occurs is not None else None
        if not isinstance(count, str) or count.strip().isidentifier():
            return capacity or 0
        scope_values = dict(self.codec.variant.scope.values)
        scope_values.update({k: int(v) for k, v in value.items() if isinstance(v, (bool, int))})
        try:
            node = expr.parse(count)
            scope = self.codec.variant.scope
            return expr.evaluate(node, type(scope)(scope_values, scope.members, scope.sizes))
        except ExprError:
            return 0

    def field(self, item: Field, value: dict[str, Any]) -> object:
        """Return the pattern value of one field (`value` holds the fields before it)."""
        if item.occurs is None:
            return self._scalar_field(item)
        return self._array_field(item, value)

    def _scalar_field(self, item: Field) -> object:
        inner = self.project.types.get(item.type)
        if inner is not None:
            return self.struct(inner.fields)
        user = self.project.user_types.get(item.type)
        if user is not None:
            return self._user_bytes(user.size_bytes, 0)
        return self.scalar(item, 0)

    def _array_field(self, item: Field, value: dict[str, Any]) -> object:
        count = self.length(item, value)
        if item.type == "char":
            return (CHAR_PATTERN * (count // len(CHAR_PATTERN) + 1))[:count]
        inner = self.project.types.get(item.type)
        if inner is not None:
            return [self.struct(inner.fields) for _ in range(count)]
        user = self.project.user_types.get(item.type)
        if user is not None:
            return [self._user_bytes(user.size_bytes, index) for index in range(count)]
        return [self.scalar(item, index) for index in range(count)]

    def _user_bytes(self, size_bytes: int, index: int) -> bytes:
        """Deterministic, position-distinct byte pattern for an opaque user type."""
        return bytes((index + offset) % 256 for offset in range(size_bytes))

    def scalar(self, item: Field, index: int) -> object:
        """Return the pattern value of element `index` of a scalar field."""
        enum = self.project.enums.get(item.type)
        if enum is not None:
            members = sorted(enum.values, key=lambda name: enum.values[name])
            return members[(len(members) - 1 - index) % len(members)]
        if item.type == "bool":
            return index % 2 == 0
        if item.type == "char":
            return chr(ord("A") + index % 26)
        low, high = self.bounds(item)
        if item.type in FLOAT_TYPES:
            number = low + (high - low) * 0.75
            return _f32(number) if item.type == "f32" else number
        return max(int(low), int(low) + (int(high) - int(low)) * 2 // 3 - index)

    def bounds(self, item: Field) -> tuple[float, float]:
        """Return the valid range of a numeric field in this profile."""
        if item.type in FLOAT_TYPES:
            low, high = -FLOAT_SPAN, FLOAT_SPAN
        else:
            low, high = self.codec.int_range(item)
        lower = self.codec.bound(item.min)
        upper = self.codec.bound(item.max)
        return (
            low if lower is None else max(low, lower),
            high if upper is None else min(high, upper),
        )


def holds(condition: str | None, variant: Variant) -> bool:
    """Return whether an `if:` condition is true in a profile (a missing one always is)."""
    if condition is None:
        return True
    try:
        return expr.evaluate(expr.parse_condition(condition), variant.scope) != 0
    except ExprError:
        return True


def _profiles(project: Project) -> list[str | None]:
    return list(project.profiles) or [None]


def _vector(
    codec: ReferenceCodec, interface: str, message: str, sample: tuple[str, dict[str, Any]]
) -> dict[str, Any]:
    name, value = sample
    payload = codec.encode_message(message, value, interface)
    frame = codec.encode_frame(interface, message, value)
    return {
        "interface": interface,
        "message": message,
        "sample": name,
        "value": codec.decode_message(message, payload, interface),
        "payload": payload.hex(),
        "frame": frame.hex(),
    }


def vectors(
    project: Project, plugins: dict[str, ChecksumPlugin] | None = None
) -> list[dict[str, Any]]:
    """Every golden vector of a valid project, in a stable order."""
    result: list[dict[str, Any]] = []
    for profile in _profiles(project):
        codec = ReferenceCodec(project, profile, plugins)
        sampler = Sampler(codec)
        for interface_name, interface in sorted(project.interfaces.items()):
            for entry in interface.messages:
                message = project.messages[entry.name]
                if not holds(entry.condition, codec.variant) or not holds(
                    message.condition, codec.variant
                ):
                    continue  # the message does not exist in this profile
                fields = message.fields
                samples = (
                    ("default", codec.default_value(entry.name)),
                    ("pattern", sampler.struct(fields)),
                )
                for sample in samples:
                    vector = _vector(codec, interface_name, entry.name, sample)
                    result.append({"profile": profile, **vector})
    return result


def _jsonable(value: object) -> object:
    """Replace raw bytes (an opaque user type's value) with a JSON-safe, unambiguous wrapper.

    `__bytes_hex__` can never collide with a field name: SR-2 rejects any identifier containing
    `__`.
    """
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def render(project: Project, plugins: dict[str, ChecksumPlugin] | None = None) -> str:
    """Golden vectors as canonical JSON text."""
    return json.dumps(_jsonable(vectors(project, plugins)), indent=2, sort_keys=True) + "\n"


def load_plugins(path: Path) -> dict[str, ChecksumPlugin]:
    """Load `PLUGINS` (custom checksum impl name -> function) from a Python file."""
    spec = importlib.util.spec_from_file_location(f"wireforge_plugins_{path.stem}", path)
    if spec is None or spec.loader is None:
        message = f"cannot load plugins from {path}"
        raise ValueError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugins: dict[str, ChecksumPlugin] = dict(module.PLUGINS)
    return plugins


def main(argv: list[str] | None = None) -> int:
    """Write (or with --check compare) the golden vector file of a project."""
    parser = argparse.ArgumentParser(prog="python -m wireforge.golden")
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--check", action="store_true", help="fail when the file is outdated")
    parser.add_argument("--plugins", type=Path, help="Python file defining PLUGINS")
    args = parser.parse_args(argv)
    result = load_project(args.project)
    if result.project is None:
        for issue in result.issues:
            sys.stdout.write(f"{issue}\n")
        return 1
    try:
        text = render(result.project, load_plugins(args.plugins) if args.plugins else None)
    except CodecError as error:
        sys.stdout.write(f"{error}\n")
        return 1
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != text:
            sys.stdout.write(f"{args.output} is outdated; regenerate it\n")
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
