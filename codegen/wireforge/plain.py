"""Plain Python values from the objects ruamel.yaml loads."""

from __future__ import annotations


def to_plain(value: object) -> object:
    """Convert ruamel scalar subclasses (HexInt, ScalarFloat, ...) to plain Python values."""
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    for kind in (bool, int, float, str):
        if isinstance(value, kind):
            return kind(value)
    return value
