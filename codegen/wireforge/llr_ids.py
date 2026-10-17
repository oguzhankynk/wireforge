"""Stable numbers for the low-level requirements (`llr_ids.lock`).

A low-level requirement is identified by a key made of names (`SensorReading.temperature.RANGE`).
Customers' tools want ids that never change, so a project can pin a number to each key in an
optional lock file. The file is append-only: a number is never changed, moved or given again; a key
that disappears is moved to `retired`, and a renamed element keeps its number (`rename`).
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any, cast

from ruamel.yaml import YAML

from wireforge import schema
from wireforge.model import IdMap, Source
from wireforge.plain import to_plain

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

LOCK_FILE = "llr_ids.lock"
DEFAULT_FORMAT = "SWLLR-{n:05d}"
FIRST_NUMBER = 1


class LockError(ValueError):
    """The lock file is invalid or an operation on it is not allowed."""


def load(project_dir: Path) -> IdMap | None:
    """Read the lock file of a project directory (None without one)."""
    path = project_dir / LOCK_FILE
    if not path.exists():
        return None
    document = schema.load_yaml(path)
    issues = schema.validate_document(
        document, "llr_ids.schema.json", file=str(path), source=document
    )
    if issues:
        raise LockError("\n".join(str(issue) for issue in issues))
    plain = cast("dict[str, Any]", to_plain(document))
    return IdMap(
        plain["format"],
        plain["next"],
        dict(plain["ids"]),
        dict(plain.get("retired", {})),
        Source(str(path), 1, ""),
    )


def number(ids: IdMap, n: int) -> str:
    """Return the displayed id of the number `n`."""
    return ids.format.format(n=n)


def assign(keys: Iterable[str], current: IdMap | None, *, text: str = DEFAULT_FORMAT) -> IdMap:
    """Return the lock for `keys`: existing numbers kept, new keys numbered in order.

    Keys that are no longer derived move to `retired` with their number.
    """
    wanted = list(dict.fromkeys(keys))
    base = current or IdMap(text, FIRST_NUMBER, {}, {}, Source("", None, ""))
    ids = {key: value for key, value in base.ids.items() if key in set(wanted)}
    retired = dict(base.retired)
    retired.update({key: value for key, value in base.ids.items() if key not in set(wanted)})
    following = base.next
    for key in wanted:
        if key not in ids:
            ids[key] = number(base, following)
            following += 1
    return IdMap(base.format, following, ids, retired, base.source)


def rename(current: IdMap, old: str, new: str) -> IdMap:
    """Move a number to a new key (an element was renamed); the new key must not be numbered."""
    if old not in current.ids:
        message = f"{old!r} has no number"
        raise LockError(message)
    if new in current.ids:
        message = f"{new!r} already has the number {current.ids[new]}"
        raise LockError(message)
    ids = {(new if key == old else key): value for key, value in current.ids.items()}
    return IdMap(current.format, current.next, ids, dict(current.retired), current.source)


def dump(ids: IdMap) -> str:
    """Return the canonical text of the lock file."""
    document: dict[str, Any] = {
        "schema_version": 1,
        "format": ids.format,
        "next": ids.next,
        "ids": dict(ids.ids),
    }
    if ids.retired:
        document["retired"] = dict(ids.retired)
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 4096
    out = io.StringIO()
    yaml.dump(document, out)
    return out.getvalue()
