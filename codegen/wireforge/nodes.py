"""Units of a system: the view of one unit, the direction of its messages and its hash.

A project with `nodes` is one model. `node_view` cuts it down to what one unit needs: the
interfaces it takes part in, the messages it sends or receives there, and the types, enums and
checksums those messages use. The view is an ordinary `Project`, so the generators run on it
unchanged; its name and namespace carry the unit's name, which keeps the code of several units
apart. `node_hash` identifies the view, so a change that does not reach a unit leaves its hash.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import TYPE_CHECKING, Literal, TypeVar

from wireforge.model import Interface, InterfaceMessage, Project, Source, Vector

if TYPE_CHECKING:
    from collections.abc import Iterable

Direction = Literal["send", "receive", "both"]
T = TypeVar("T")

# Fields of the model that say where something was written, not what it is.
_LOCATION_FIELDS = frozenset({"source", "files", "requirements_source", "llr_ids"})


class NodeError(ValueError):
    """The project has no such unit."""

    def __init__(self, node: str, known: Iterable[str]) -> None:
        """Name the unit and the units the project does define."""
        super().__init__(
            f"unknown node {node!r} (the project defines: {', '.join(known) or 'none'})"
        )


def _check(project: Project, node: str) -> None:
    if node not in project.nodes:
        raise NodeError(node, sorted(project.nodes))


def _direction(entry: InterfaceMessage, node: str) -> Direction | None:
    sends = node in entry.senders
    receives = bool(set(entry.senders) - {node})
    if sends and receives:
        return "both"
    if sends:
        return "send"
    return "receive" if receives else None


def directions(project: Project, node: str, interface: str) -> dict[str, Direction]:
    """Return message name -> `send`, `receive` or `both` for `node` on one interface.

    A message is `both` when the unit sends it and another sender may put it on the same line.
    Messages the unit neither sends nor receives are left out.
    """
    _check(project, node)
    result: dict[str, Direction] = {}
    for entry in project.interfaces[interface].messages:
        direction = _direction(entry, node)
        if direction is not None:
            result[entry.name] = direction
    return result


def entry_directions(project: Project, interface: Interface) -> dict[str, Direction]:
    """Return message name -> direction of an interface as the generators see it.

    A unit's view gives the directions of that unit; a project without a unit (no `nodes`) sends
    and receives every message.
    """
    if project.node is None:
        return {entry.name: "both" for entry in interface.messages}
    return directions(project, project.node, interface.name)


def message_way(project: Project, interface: str, message: str) -> Direction:
    """Return the direction of one message on an interface as the generators see it."""
    return entry_directions(project, project.interfaces[interface])[message]


def _interface_view(interface: Interface, node: str) -> Interface:
    entries = tuple(entry for entry in interface.messages if _direction(entry, node) is not None)
    kinds = {_direction(entry, node) for entry in entries}
    if kinds == {"send"}:
        direction = "tx"
    elif kinds == {"receive"}:
        direction = "rx"
    else:
        direction = "both"
    return dataclasses.replace(interface, messages=entries, direction=direction)


def _field_uses(project: Project, fields: Iterable[object], used: dict[str, set[str]]) -> None:
    for item in fields:
        type_name = getattr(item, "type", "")
        checksum = getattr(item, "checksum", None)
        if checksum is not None:
            used["checksums"].add(checksum.use)
        if type_name in project.enums:
            used["enums"].add(type_name)
        elif type_name in project.user_types:
            used["user_types"].add(type_name)
        elif type_name in project.types and type_name not in used["types"]:
            used["types"].add(type_name)
            _field_uses(project, project.types[type_name].fields, used)


def _used(project: Project, interfaces: list[Interface]) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {
        "messages": {entry.name for item in interfaces for entry in item.messages},
        "types": set(),
        "enums": set(),
        "user_types": set(),
        "checksums": set(),
    }
    for name in sorted(used["messages"]):
        _field_uses(project, project.messages[name].fields, used)
    for item in interfaces:
        _field_uses(project, [*(item.header or ()), *item.trailer], used)
    return used


def _keep(entries: dict[str, T], names: set[str]) -> dict[str, T]:
    return {name: value for name, value in entries.items() if name in names}


def _vector_view(project: Project, vector: Vector, node: str, present: set[str]) -> Vector | None:
    """Return the vector as far as it applies to the unit, or None when it does not apply."""
    if vector.interface is None:
        return vector
    if vector.interface not in present:
        return None
    way = directions(project, node, vector.interface).get(vector.message)
    decodes = way in {"receive", "both"} and vector.direction in {"decode", "both"}
    encodes = way in {"send", "both"} and vector.direction in {"encode", "both"}
    if decodes and encodes:
        return vector
    if not decodes and not encodes:
        return None
    return dataclasses.replace(vector, direction="decode" if decodes else "encode")


def node_view(project: Project, node: str) -> Project:
    """Return the project as one unit sees it, named and namespaced after the unit."""
    _check(project, node)
    interfaces = [
        _interface_view(item, node) for item in project.interfaces.values() if node in item.nodes
    ]
    used = _used(project, interfaces)
    present = {item.name for item in interfaces}
    taking_part = {name for item in interfaces for name in item.nodes}
    messages = _keep(project.messages, used["messages"])
    vectors = {}
    for name, vector in project.vectors.items():
        kept_vector = (
            _vector_view(project, vector, node, present) if vector.message in messages else None
        )
        if kept_vector is not None:
            vectors[name] = kept_vector
    return dataclasses.replace(
        project,
        name=f"{project.name}_{node}",
        node=node,
        namespace=f"{project.namespace or project.name}::{node}",
        messages=messages,
        types=_keep(project.types, used["types"]),
        enums=_keep(project.enums, used["enums"]),
        user_types=_keep(project.user_types, used["user_types"]),
        checksums=_keep(project.checksums, used["checksums"]),
        nodes=_keep(project.nodes, taking_part),
        interfaces={item.name: item for item in interfaces},
        vectors=vectors,
    )


def _plain(value: object) -> object:
    if isinstance(value, Source):
        return None
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _plain(getattr(value, item.name))
            for item in dataclasses.fields(value)
            if item.name not in _LOCATION_FIELDS
        }
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def node_hash(project: Project, node: str) -> str:
    """Return the sha256 of the canonical JSON of the unit's view (locations are left out).

    The view keeps the whole requirements catalog, defines, profiles and user constants, so a
    change to one of those changes the hash of every unit.
    """
    view = node_view(project, node)
    canonical = json.dumps(_plain(view), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
