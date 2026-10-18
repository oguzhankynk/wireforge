"""Physical values of fields with `scale` and `offset`, shared by the generator and the tests.

The wire carries the raw value; the physical value is `raw * scale + offset`. The generated code
has a getter and a setter per scaled field; this module decides what they look like (the limits of
the raw value, the literals) so that the code and the tests written for it agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wireforge.model import BUILTIN_BITS, FLOAT_TYPES, INTEGER_TYPES, SIGNED_TYPES

if TYPE_CHECKING:
    from typing import Any

    from wireforge.model import Field, Project, Struct

EXACT_HALF = 1 << 52  # below this a double holds value +- 0.5 exactly
SAFE_RAW = 1 << 50  # raw values the tests use at the ends of a wide field
FLOAT_MAX = {"f32": 3.4028234663852886e38, "f64": 1.7976931348623157e308}


def is_scaled(item: Field) -> bool:
    """Whether a field has a physical value of its own."""
    return item.scale is not None or item.offset is not None


def scaled_fields(item: Struct) -> list[Field]:
    """Return the fields of a struct that have `scale` or `offset`."""
    return [field for field in item.fields if is_scaled(field)]


def scale_of(item: Field) -> float:
    """Return the scale of a field (1 without `scale`)."""
    return 1.0 if item.scale is None else float(item.scale)


def offset_of(item: Field) -> float:
    """Return the offset of a field (0 without `offset`)."""
    return 0.0 if item.offset is None else float(item.offset)


def number(value: float) -> str:
    """Return a C++ double literal; a negative one is parenthesized."""
    text = repr(float(value))
    return f"({text})" if value < 0 else text


def widened(item: Field, expression: str) -> str:
    """Return a C++ expression of a raw value as a double (no cast when it already is one)."""
    return expression if item.type == "f64" else f"static_cast<double>({expression})"


def physical(item: Field, raw: float) -> float:
    """Return the physical value of a raw value, as the generated getter computes it."""
    return float(raw) * scale_of(item) + offset_of(item)


def is_float(item: Field) -> bool:
    """Whether the raw value of a field is a floating-point number."""
    return item.type in FLOAT_TYPES


def integer_limits(type_name: str, bits: int) -> tuple[int, int]:
    """Return the smallest and the largest value of an integer type of `bits` bits."""
    if type_name in SIGNED_TYPES:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def raw_limits(item: Field) -> tuple[int, int]:
    """Return the smallest and the largest raw value of an integer field."""
    if item.type not in INTEGER_TYPES:
        message = f"{item.name}: {item.type} has no integer limits"
        raise ValueError(message)
    return integer_limits(item.type, item.bits or BUILTIN_BITS[item.type])


@dataclass(frozen=True)
class Probe:
    """Raw values a test sets through the physical setter: both ends and one value outside each."""

    low: int
    high: int
    below: int
    above: int


def probe(item: Field) -> Probe:
    """Return the raw values the generated test of an integer field uses."""
    low, high = raw_limits(item)
    return Probe(
        low=max(low, -SAFE_RAW),
        high=min(high, SAFE_RAW),
        below=low * 2 - 2,
        above=high * 2 + 2,
    )


def lower_check(item: Field) -> str:
    """Return the C++ condition that keeps `raw` from rounding below the lowest raw value."""
    low, _ = raw_limits(item)
    if abs(low) < EXACT_HALF:
        return f"raw > {float(low) - 0.5!r}"
    return f"raw >= {float(low)!r}"


def upper_check(item: Field) -> str:
    """Return the C++ condition `raw` has to meet to round to a value that is not too high."""
    _, high = raw_limits(item)
    if high < EXACT_HALF:
        return f"raw < {float(high) + 0.5!r}"
    return f"raw < {float(high + 1)!r}"


def physical_values(project: Project, struct: Struct, value: dict[str, Any]) -> dict[str, Any]:
    """Return the physical values of a decoded struct value, by field name (nested like the value).

    Only fields with `scale` or `offset` and structs that contain some appear in the result.
    """
    result: dict[str, Any] = {}
    for item in struct.fields:
        raw = value.get(item.name)
        inner = project.types.get(item.type)
        if raw is None:
            continue
        if is_scaled(item) and isinstance(raw, (int, float)):
            result[item.name] = physical(item, raw)
        elif inner is not None:
            parts = raw if isinstance(raw, list) else [raw]
            found = [physical_values(project, inner, part) for part in parts]
            if any(found):
                result[item.name] = found if isinstance(raw, list) else found[0]
    return result


def function_names(owner: str, item: Field) -> tuple[str, str]:
    """Return the names of the getter and the setter of a scaled field."""
    return f"physical_{owner}_{item.name}", f"set_physical_{owner}_{item.name}"
