"""Requirement test vectors: the user's expectations checked against the reference codec.

A vector is bytes from the ICD with what they must decode to (or what a value must encode to).
`run()` plays every vector through the reference codec in each profile it applies to and `check()`
reports the disagreements as SR-22, so a wrong vector or a wrong model is found before code is
generated. The codec never replaces the user's expectation: the generated C++ test compares with
what the vector says, not with what the codec answered.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from wireforge import expr
from wireforge.codec import CodecError, ReferenceCodec
from wireforge.expr import ExprError
from wireforge.issues import Issue, errors_only
from wireforge.loader import load_project
from wireforge.validate import Validator

if TYPE_CHECKING:
    from pathlib import Path

    from wireforge.codec import ChecksumPlugin
    from wireforge.model import Field, Project, Struct, Vector

OK = "Ok"
NO_PLUGIN = "no plugin for custom checksum"


@dataclass(frozen=True)
class VectorRun:
    """One vector played through the reference codec in one profile.

    `status` is the codec's answer in `phase` (`decode` or `encode`): the first direction that
    failed, or the last one that ran when all agree (`Ok`). `decoded` is the decoded value and
    `encoded` the produced bytes.
    """

    vector: Vector
    profile: str | None
    phase: str
    status: str
    detail: str
    decoded: dict[str, Any] | None
    encoded: bytes | None


def profiles_of(project: Project, vector: Vector) -> list[str | None]:
    """Return the profiles a vector applies to (all of them unless the vector names one)."""
    if vector.profile is not None:
        return [vector.profile]
    return list(project.profiles) or [None]


def f32(value: float) -> float:
    """Round a number to the nearest 32-bit float."""
    return float(struct.unpack(">f", struct.pack(">f", value))[0])


def _holds(codec: ReferenceCodec, message: Struct) -> bool:
    if message.condition is None:
        return True
    try:
        return expr.evaluate(expr.parse_condition(message.condition), codec.variant.scope) != 0
    except ExprError:
        return True


def _decode(codec: ReferenceCodec, vector: Vector, data: bytes) -> tuple[str, str, Any]:
    try:
        if vector.interface is None:
            return OK, "", codec.decode_message(vector.message, data)
        frame = codec.decode_frame(vector.interface, data)
    except CodecError as error:
        return error.status, str(error), None
    if frame.message != vector.message:
        return "UnknownMessageId", f"the frame carries message {frame.message}", None
    return OK, "", frame.value


def _encode(codec: ReferenceCodec, vector: Vector) -> tuple[str, str, bytes | None]:
    value = vector.fields or {}
    try:
        if vector.interface is None:
            return OK, "", codec.encode_message(vector.message, value)
        return OK, "", codec.encode_frame(vector.interface, vector.message, value)
    except CodecError as error:
        return error.status, str(error), None


def run(project: Project, plugins: dict[str, ChecksumPlugin] | None = None) -> list[VectorRun]:
    """Play every well-formed vector through the reference codec, once per profile."""
    runs: list[VectorRun] = []
    for vector in project.vectors.values():
        message = project.messages.get(vector.message)
        known = vector.interface is None or vector.interface in project.interfaces
        if message is None or vector.data is None or not known:
            continue  # the validator reports it (SR-22)
        for profile in profiles_of(project, vector):
            if profile is not None and profile not in project.profiles:
                continue
            codec = ReferenceCodec(project, profile, plugins)
            if not _holds(codec, message):
                continue
            runs.append(_play(codec, vector, vector.data, profile))
    return runs


def _play(codec: ReferenceCodec, vector: Vector, data: bytes, profile: str | None) -> VectorRun:
    phase, status, detail, decoded, encoded = "decode", OK, "", None, None
    if vector.direction in {"decode", "both"}:
        status, detail, decoded = _decode(codec, vector, data)
    if vector.direction in {"encode", "both"} and status == OK:
        phase = "encode"
        status, detail, encoded = _encode(codec, vector)
    return VectorRun(vector, profile, phase, status, detail, decoded, encoded)


# --- comparison --------------------------------------------------------------------------------


def _expected_value(project: Project, item: Field, value: object) -> object:
    """Round the floats the way the codec does (f32 fields hold 32-bit values)."""
    inner = project.types.get(item.type)
    if isinstance(value, list):
        return [_expected_value(project, _single(item), element) for element in value]
    if inner is not None and isinstance(value, dict):
        return _expected_fields(project, inner, value)
    if item.type == "f32" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f32(float(value))
    return value


def _single(item: Field) -> Field:
    """Return the field as one element (arrays are compared element by element)."""
    return type(item)(item.name, item.type, item.source)


def _expected_fields(project: Project, struct_: Struct, values: dict[str, Any]) -> dict[str, Any]:
    by_name = {item.name: item for item in struct_.fields}
    return {
        name: _expected_value(project, by_name[name], value) if name in by_name else value
        for name, value in values.items()
    }


def _differences(expected: object, actual: object, path: str) -> list[str]:
    """List where `actual` differs from `expected` (a mapping is compared by its keys only)."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path or 'value'} is {actual!r}, expected {expected!r}"]
        found: list[str] = []
        for key, value in expected.items():
            if key not in actual:
                found.append(f"{path}{key} is absent, expected {value!r}")
            else:
                found += _differences(value, actual[key], f"{path}{key}.")
        return found
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            size = len(actual) if isinstance(actual, list) else actual
            return [f"{path.rstrip('.')} has {size} elements, expected {len(expected)}"]
        found = []
        for index, value in enumerate(expected):
            found += _differences(value, actual[index], f"{path.rstrip('.')}[{index}].")
        return found
    if expected != actual or isinstance(expected, bool) != isinstance(actual, bool):
        return [f"{path.rstrip('.')} is {actual!r}, expected {expected!r}"]
    return []


def _problems(project: Project, item: VectorRun) -> list[str]:
    vector = item.vector
    if vector.status is not None:
        if item.status == vector.status:
            return []
        return [f"decodes with status {item.status}, expected {vector.status}"]
    if item.status != OK:
        word = "decodes" if item.phase == "decode" else "encodes"
        return [f"{word} with status {item.status}, expected Ok"]
    problems: list[str] = []
    if item.decoded is not None and vector.fields is not None:
        message = project.messages[vector.message]
        expected = _expected_fields(project, message, vector.fields)
        problems += [f"field {text}" for text in _differences(expected, item.decoded, "")]
    if item.encoded is not None and item.encoded != vector.data:
        problems.append(f"encodes to {item.encoded.hex(' ')}, expected {vector.text.strip()}")
    return problems


def check(project: Project, plugins: dict[str, ChecksumPlugin] | None = None) -> list[Issue]:
    """Return SR-22 issues where the codec and a vector disagree.

    Without plugins a vector that needs a custom checksum is skipped with a warning.
    """
    issues: list[Issue] = []
    for item in run(project, plugins):
        source = item.vector.source
        label = f"vector {item.vector.name}"
        label += f" (profile {item.profile})" if item.profile else ""
        if NO_PLUGIN in item.detail:
            text = f"[SR-22] {label}: skipped, a custom checksum needs its plugin"
            issues.append(Issue(source.file, source.line, source.pointer, text, "warning"))
            continue
        issues += [source.issue("SR-22", f"{label}: {text}") for text in _problems(project, item)]
    return issues


def validate_project(
    location: Path, plugins: dict[str, ChecksumPlugin] | None = None
) -> list[Issue]:
    """Load a project and return its structural, semantic and vector issues."""
    result = load_project(location)
    if result.project is None:
        return result.issues
    issues = result.issues + Validator(result.project).run()
    if not errors_only(issues):
        issues += check(result.project, plugins)
    return issues
