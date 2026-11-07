"""Interface documents of a system: the ICD of one unit and the ICD of the whole system.

Both are Markdown. The unit ICD lists the interfaces of one unit with the other participants, the
messages it sends and the messages it receives, and the wire layouts of its view. The system ICD
lists the units, which unit takes part in which interface and, for every message, on which
interface it goes from whom to whom.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from wireforge import layout, nodes

if TYPE_CHECKING:
    from collections.abc import Sequence

    from wireforge.model import Interface, InterfaceMessage, Project

WAYS = {"send": "sends", "receive": "receives", "both": "sends, receives"}


def _table(header: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> list[str]:
    lines = [f"| {' | '.join(header)} |", f"|{'|'.join('---' for _ in header)}|"]
    lines += [f"| {' | '.join(row)} |" for row in rows]
    return [*lines, ""]


def _identifier(project: Project, entry: InterfaceMessage) -> str:
    info = project.messages[entry.name].message
    number = entry.id if entry.id is not None else (info.id if info else None)
    return "-" if number is None else f"{number:#x}"


def _receivers(interface: Interface, entry: InterfaceMessage) -> list[str]:
    return [node for node in interface.nodes if set(entry.senders) - {node}]


def _list(names: list[str] | tuple[str, ...]) -> str:
    return ", ".join(names) or "-"


def system_icd(project: Project, content_hash: str) -> str:
    """Return the ICD of the whole system of a project with `nodes`."""
    version = f" {project.version}" if project.version else ""
    lines = [
        f"# System interface description of {project.name}{version}",
        "",
        f"Model hash: {content_hash}",
        "",
        "## Units",
        "",
    ]
    rows: list[tuple[str, ...]] = [
        (name, node.description or "-", node.language or "per command")
        for name, node in project.nodes.items()
    ]
    lines += _table(("Unit", "Description", "Language"), rows)
    lines += ["## Interfaces and units", ""]
    names = list(project.nodes)
    rows = []
    for interface in project.interfaces.values():
        cells = [interface.name, interface.version]
        for node in names:
            ways = set(nodes.directions(project, node, interface.name).values())
            if node not in interface.nodes or not ways:
                cells.append("-")
                continue
            sends = bool(ways & {"send", "both"})
            receives = bool(ways & {"receive", "both"})
            cells.append(WAYS["both" if sends and receives else "send" if sends else "receive"])
        rows.append(tuple(cells))
    lines += _table(("Interface", "Version", *names), rows)
    lines += ["## Messages", ""]
    rows = []
    for interface in project.interfaces.values():
        for entry in interface.messages:
            rows.append(
                (
                    entry.name,
                    interface.name,
                    _identifier(project, entry),
                    _list(entry.senders),
                    _list(_receivers(interface, entry)),
                )
            )
    rows.sort()
    lines += _table(("Message", "Interface", "Id", "From", "To"), rows)
    return "\n".join(lines).rstrip("\n") + "\n"


def unit_icd(project: Project, node: str, content_hash: str) -> str:
    """Return the ICD of one unit of a project with `nodes` (`project` is the whole project)."""
    unit = project.nodes[node]
    view = nodes.node_view(project, node)
    version = f" {project.version}" if project.version else ""
    lines = [
        f"# Interface description of unit {node} of {project.name}{version}",
        "",
        f"Unit hash: {content_hash}",
        "",
    ]
    if unit.description:
        lines += [unit.description, ""]
    lines += [f"Language: {unit.language or 'per command'}", "", "## Interfaces", ""]
    rows = []
    sent: list[tuple[str, ...]] = []
    received: list[tuple[str, ...]] = []
    for interface in view.interfaces.values():
        ways = nodes.directions(project, node, interface.name)
        rows.append(
            (
                interface.name,
                interface.version,
                _list([other for other in interface.nodes if other != node]),
                _list([name for name, way in ways.items() if way in {"send", "both"}]),
                _list([name for name, way in ways.items() if way in {"receive", "both"}]),
            )
        )
        for entry in interface.messages:
            ident = _identifier(project, entry)
            if ways[entry.name] in {"send", "both"}:
                sent.append(
                    (entry.name, interface.name, ident, _list(_receivers(interface, entry)))
                )
            if ways[entry.name] in {"receive", "both"}:
                senders = [name for name in entry.senders if name != node]
                received.append((entry.name, interface.name, ident, _list(senders)))
    lines += _table(("Interface", "Version", "Other participants", "Sends", "Receives"), rows)
    lines += ["## Messages sent", ""]
    lines += _table(("Message", "Interface", "Id", "To"), sorted(sent))
    lines += ["## Messages received", ""]
    lines += _table(("Message", "Interface", "Id", "From"), sorted(received))
    lines += ["## Wire layouts", ""]
    try:
        report = layout.to_markdown(layout.analyze(view))
    except layout.LayoutError as error:
        report = f"The layout analysis failed: {error}\n"
    lines += [re.sub(r"^(#+) ", r"#\1 ", line) for line in report.splitlines()]
    return "\n".join(lines).rstrip("\n") + "\n"
