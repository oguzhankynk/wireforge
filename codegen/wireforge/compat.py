"""Compare two versions of a project interface by interface and classify every difference.

- breaking: the wire format or the accepted values changed; old and new peers cannot interoperate;
- compatible: peers still interoperate (renames, new messages, defaults, trailing fields that old
  receivers ignore);
- doc: only descriptions or units changed.

The required version bump is major / minor / patch for the most severe class found, and the
report checks it against the interface versions of both projects.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from wireforge import expr
from wireforge.expr import ExprError
from wireforge.issues import Issue, errors_only
from wireforge.loader import load_project
from wireforge.model import BUILTIN_BITS, Field, Interface, Project, Struct, Value
from wireforge.validate import Validator, Variant

if TYPE_CHECKING:
    from collections.abc import Iterable

BREAKING = "breaking"
COMPATIBLE = "compatible"
DOC = "doc"
SEVERITIES = (BREAKING, COMPATIBLE, DOC)
BUMP_FOR = {BREAKING: "major", COMPATIBLE: "minor", DOC: "patch"}
BUMP_RANK = {"downgrade": -1, "none": 0, "patch": 1, "minor": 2, "major": 3}
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

Pairs = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Change:
    """One classified difference."""

    severity: str
    where: str
    message: str

    def __str__(self) -> str:
        """Render as `severity: where: message`."""
        return f"{self.severity}: {self.where}: {self.message}"


def _version(text: str | None) -> tuple[int, int, int] | None:
    match = _SEMVER.match(text or "")
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


def version_bump(old: str | None, new: str | None) -> str:
    """Classify the step between two semantic versions: major, minor, patch, none or downgrade."""
    before = _version(old)
    after = _version(new)
    if before is None or after is None or after < before:
        return "downgrade" if before and after else "none"
    for index, name in enumerate(("major", "minor", "patch")):
        if after[index] != before[index]:
            return name
    return "none"


@dataclass
class InterfaceReport:
    """Differences of one interface between the old and the new project."""

    name: str
    old_version: str | None
    new_version: str | None
    changes: list[Change] = field(default_factory=list)

    @property
    def required_bump(self) -> str:
        """Smallest version step that the changes require."""
        for severity in SEVERITIES:
            if any(change.severity == severity for change in self.changes):
                return BUMP_FOR[severity]
        return "none"

    @property
    def actual_bump(self) -> str:
        """Version step between the old and the new interface version."""
        return version_bump(self.old_version, self.new_version)

    @property
    def version_ok(self) -> bool:
        """Whether the new version is at least the required step above the old one."""
        if self.old_version is None or self.new_version is None:
            return True
        return BUMP_RANK[self.actual_bump] >= BUMP_RANK[self.required_bump]

    def summary(self) -> str:
        """One line: versions, required step and verdict."""
        verdict = "OK" if self.version_ok else "VERSION BUMP TOO SMALL"
        return (
            f"interface {self.name}: {self.old_version} -> {self.new_version}, "
            f"{len(self.changes)} change(s), requires {self.required_bump} bump: {verdict}"
        )


@dataclass(frozen=True)
class _Element:
    """One field on the wire, flattened (nested struct fields get dotted paths)."""

    path: str
    layout: Pairs
    semantics: Pairs
    compatible: Pairs
    doc: Pairs


def _pairs(values: dict[str, object]) -> Pairs:
    return tuple((key, repr(value)) for key, value in values.items() if value is not None)


class _Flattener:
    """Builds the wire elements of fields for one project variant."""

    def __init__(self, project: Project, validator: Validator, variant: Variant) -> None:
        self.project = project
        self.validator = validator
        self.variant = variant

    def value(self, raw: Value | None) -> object:
        if raw is None or isinstance(raw, (int, float)):
            return raw
        evaluated = self.validator.static_value(raw, self.variant)
        return raw if evaluated is None else evaluated

    def count_text(self, raw: Value | None) -> str | None:
        if raw is None or isinstance(raw, float):
            return None
        try:
            return repr(expr.as_expression(raw))
        except ExprError:
            return str(raw)

    def checksum(self, item: Field) -> tuple[object, ...] | None:
        use = item.checksum
        if use is None:
            return None
        algo = self.project.checksums.get(use.use)
        params = (
            None
            if algo is None
            else (
                algo.algo,
                algo.width,
                algo.poly,
                algo.init,
                algo.refin,
                algo.refout,
                algo.xorout,
                algo.impl,
                algo.size_bytes,
            )
        )
        return (params, use.scope, use.first, use.last)

    def element(self, item: Field, prefix: str) -> _Element:
        enum = self.project.enums.get(item.type)
        base = enum.type if enum is not None else item.type
        occurs = item.occurs
        user = self.project.user_types.get(item.type)
        if user is not None:
            type_value: object = {
                "kind": "user_type",
                "cpp": user.cpp,
                "size_bytes": user.size_bytes,
            }
        elif base in self.project.types:
            type_value = "struct"
        else:
            type_value = base
        layout: dict[str, object] = {
            "type": type_value,
            "bits": item.bits or BUILTIN_BITS.get(base),
            "occurs": None
            if occurs is None
            else (
                self.value(occurs.min) or 0,
                self.value(occurs.max),
                self.count_text(occurs.count),
                occurs.fill,
            ),
            "const": item.const,
            "reserved": item.reserved or None,
            "checksum": self.checksum(item),
            "role": item.role,
        }
        semantics: dict[str, object] = {
            "min": self.value(item.min),
            "max": self.value(item.max),
            "enum": None if enum is None else tuple(sorted(enum.values.items())),
            "scale": item.scale,
            "offset": item.offset,
        }
        return _Element(
            path=prefix + item.name,
            layout=_pairs(layout),
            semantics=_pairs(semantics),
            compatible=_pairs({"default": item.default}),
            doc=_pairs({"description": item.description, "units": item.units}),
        )

    def fields(self, fields: Iterable[Field], prefix: str = "") -> list[_Element]:
        elements: list[_Element] = []
        for item in fields:
            if not self.validator.present(item, self.variant):
                continue
            elements.append(self.element(item, prefix))
            inner = self.project.types.get(item.type)
            if inner is not None:
                marker = "[]" if item.occurs is not None else ""
                elements += self.fields(inner.fields, f"{prefix}{item.name}{marker}.")
        return elements


@dataclass
class _Side:
    """One project, its validator and the flattener of the variant being compared."""

    project: Project
    validator: Validator
    flattener: _Flattener


class _Comparison:
    """Collects the changes of one interface over all variants, labelling variant-specific ones."""

    def __init__(self, old: Project, new: Project) -> None:
        self.old = old
        self.new = new
        self.old_validator = Validator(old)
        self.new_validator = Validator(new)
        self.found: dict[Change, list[str]] = {}
        self.variant_names: list[str] = []

    def pairs(self) -> list[tuple[_Side, _Side]]:
        old_variants = {variant.name: variant for variant in self.old_validator.variants}
        new_variants = {variant.name: variant for variant in self.new_validator.variants}
        common = [name for name in old_variants if name in new_variants]
        matched = (
            [(old_variants[name], new_variants[name]) for name in common]
            if common
            else [(self.old_validator.variants[0], self.new_validator.variants[0])]
        )
        self.variant_names = [pair[1].name for pair in matched]
        return [
            (
                _Side(self.old, self.old_validator, _Flattener(self.old, self.old_validator, a)),
                _Side(self.new, self.new_validator, _Flattener(self.new, self.new_validator, b)),
            )
            for a, b in matched
        ]

    def add(self, change: Change, variant: str) -> None:
        self.found.setdefault(change, []).append(variant)

    def changes(self) -> list[Change]:
        result = []
        for change, variants in self.found.items():
            partial = len(set(variants)) < len(set(self.variant_names)) and any(variants)
            if partial:
                label = ", ".join(sorted(set(variants)))
                result.append(Change(change.severity, change.where, f"{change.message} [{label}]"))
            else:
                result.append(change)
        order = {severity: index for index, severity in enumerate(SEVERITIES)}
        return sorted(result, key=lambda change: (order[change.severity], change.where))


def _differences(old: Pairs, new: Pairs) -> str:
    before = dict(old)
    after = dict(new)
    keys = sorted(set(before) | set(after))
    return ", ".join(
        f"{key} {before.get(key, '-')} -> {after.get(key, '-')}"
        for key in keys
        if before.get(key) != after.get(key)
    )


@dataclass(frozen=True)
class _Scope:
    """Where a list of elements lives and whether appended elements are tolerated."""

    where: str
    trailing_ok: bool
    variant: str


def _compare_matched(old: _Element, new: _Element, scope: _Scope, report: _Comparison) -> None:
    where = f"{scope.where} / field {new.path}"
    checks = (
        (old.layout, new.layout, BREAKING, "wire layout changed"),
        (old.semantics, new.semantics, BREAKING, "accepted values changed"),
        (old.compatible, new.compatible, COMPATIBLE, "default changed"),
        (old.doc, new.doc, DOC, "documentation changed"),
    )
    if old.path.rsplit(".", 1)[-1] != new.path.rsplit(".", 1)[-1]:
        report.add(Change(COMPATIBLE, where, f"renamed from {old.path}"), scope.variant)
    for before, after, severity, text in checks:
        if before != after:
            report.add(
                Change(severity, where, f"{text}: {_differences(before, after)}"), scope.variant
            )


def _compare_elements(
    old: list[_Element], new: list[_Element], scope: _Scope, report: _Comparison
) -> None:
    matcher = difflib.SequenceMatcher(
        a=[item.layout for item in old], b=[item.layout for item in new], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for before, after in zip(old[i1:i2], new[j1:j2], strict=True):
                _compare_matched(before, after, scope, report)
        elif tag == "insert" and i1 == len(old):
            severity = COMPATIBLE if scope.trailing_ok else BREAKING
            reason = (
                "old receivers ignore trailing bytes"
                if scope.trailing_ok
                else "old receivers reject the extra bytes (allow_trailing_bytes is false)"
            )
            for item in new[j1:j2]:
                where = f"{scope.where} / field {item.path}"
                report.add(Change(severity, where, f"field appended; {reason}"), scope.variant)
        else:
            for item in old[i1:i2]:
                where = f"{scope.where} / field {item.path}"
                report.add(Change(BREAKING, where, "field removed or moved"), scope.variant)
            for item in new[j1:j2]:
                where = f"{scope.where} / field {item.path}"
                report.add(Change(BREAKING, where, "field inserted or moved"), scope.variant)


def _effective(project: Project, interface: Interface, message: Struct, key: str) -> object:
    info = message.message
    own = getattr(info, key) if info is not None else None
    builtin = {"endian": "big", "bit_order": "msb_first"}[key]
    return own or getattr(interface, key) or project.defaults.get(key) or builtin


def _message_ids(project: Project, interface: Interface) -> dict[str, int | None]:
    ids: dict[str, int | None] = {}
    for entry in interface.messages:
        message = project.messages.get(entry.name)
        declared = message.message.id if message is not None and message.message else None
        ids[entry.name] = entry.id if entry.id is not None else declared
    return ids


def _compare_framing(
    old: Interface, new: Interface, sides: tuple[_Side, _Side], report: _Comparison
) -> None:
    where = f"interface {new.name} / framing"
    variant = sides[1].flattener.variant.name
    old_framing, new_framing = old.framing, new.framing
    breaking = {
        "kind": (old_framing.kind, new_framing.kind),
        "frame_size": (
            sides[0].flattener.value(old_framing.frame_size),
            sides[1].flattener.value(new_framing.frame_size),
        ),
        "max_frame_size": (
            sides[0].flattener.value(old_framing.max_frame_size),
            sides[1].flattener.value(new_framing.max_frame_size),
        ),
        "length_counts": (old_framing.length_counts, new_framing.length_counts),
        "direction": (old.direction, new.direction),
        "trim_trailing_zeros": (old.trim_trailing_zeros, new.trim_trailing_zeros),
    }
    for key, (before, after) in breaking.items():
        if before != after:
            report.add(Change(BREAKING, where, f"{key} {before} -> {after}"), variant)
    compatible = {
        "resync": (old_framing.resync, new_framing.resync),
        "allow_trailing_bytes": (old.allow_trailing_bytes, new.allow_trailing_bytes),
    }
    for key, (before, after) in compatible.items():
        if before != after:
            report.add(Change(COMPATIBLE, where, f"{key} {before} -> {after}"), variant)


def _compare_frame_part(
    old: tuple[Field, ...] | None,
    new: tuple[Field, ...] | None,
    where: str,
    sides: tuple[_Side, _Side],
    report: _Comparison,
) -> None:
    variant = sides[1].flattener.variant.name
    if (old is None) != (new is None):
        report.add(Change(BREAKING, where, "added" if old is None else "removed"), variant)
        return
    scope = _Scope(where, trailing_ok=False, variant=variant)
    _compare_elements(
        sides[0].flattener.fields(old or ()), sides[1].flattener.fields(new or ()), scope, report
    )


def _compare_messages(
    old: Interface, new: Interface, sides: tuple[_Side, _Side], report: _Comparison
) -> None:
    variant = sides[1].flattener.variant.name
    old_ids = _message_ids(sides[0].project, old)
    new_ids = _message_ids(sides[1].project, new)
    for name in old_ids:
        if name not in new_ids:
            report.add(
                Change(BREAKING, f"interface {new.name} / message {name}", "removed"), variant
            )
    for name, identifier in new_ids.items():
        where = f"interface {new.name} / message {name}"
        if name not in old_ids:
            report.add(Change(COMPATIBLE, where, "added"), variant)
            continue
        if old_ids[name] != identifier:
            report.add(Change(BREAKING, where, f"id {old_ids[name]} -> {identifier}"), variant)
        _compare_message_body(name, (old, new), sides, report)


def _compare_message_body(
    name: str,
    interfaces: tuple[Interface, Interface],
    sides: tuple[_Side, _Side],
    report: _Comparison,
) -> None:
    old_project, new_project = sides[0].project, sides[1].project
    before, after = old_project.messages.get(name), new_project.messages.get(name)
    if before is None or after is None:
        return
    old_iface, new_iface = interfaces
    variant = sides[1].flattener.variant.name
    where = f"interface {new_iface.name} / message {name}"
    for key in ("endian", "bit_order"):
        old_value = _effective(old_project, old_iface, before, key)
        new_value = _effective(new_project, new_iface, after, key)
        if old_value != new_value:
            report.add(Change(BREAKING, where, f"{key} {old_value} -> {new_value}"), variant)
    if before.description != after.description:
        report.add(Change(DOC, where, "description changed"), variant)
    scope = _Scope(where, trailing_ok=old_iface.allow_trailing_bytes, variant=variant)
    _compare_elements(
        sides[0].flattener.fields(before.fields),
        sides[1].flattener.fields(after.fields),
        scope,
        report,
    )


def compare_interface(old: Project, new: Project, name: str) -> InterfaceReport:
    """Compare interface `name` of two loaded, valid projects."""
    before, after = old.interfaces.get(name), new.interfaces.get(name)
    report = InterfaceReport(
        name, None if before is None else before.version, None if after is None else after.version
    )
    if before is None or after is None:
        severity, text = (
            (COMPATIBLE, "interface added") if before is None else (BREAKING, "removed")
        )
        report.changes.append(Change(severity, f"interface {name}", text))
        return report
    comparison = _Comparison(old, new)
    for sides in comparison.pairs():
        _compare_framing(before, after, sides, comparison)
        _compare_frame_part(
            before.header, after.header, f"interface {name} / header", sides, comparison
        )
        _compare_frame_part(
            before.trailer, after.trailer, f"interface {name} / trailer", sides, comparison
        )
        _compare_messages(before, after, sides, comparison)
    if before.description != after.description:
        comparison.add(Change(DOC, f"interface {name}", "description changed"), "")
    report.changes = comparison.changes()
    return report


def compare_projects(
    old: Project, new: Project, interfaces: list[str] | None = None
) -> list[InterfaceReport]:
    """Compare the named interfaces (default: every interface of either project)."""
    names = interfaces or sorted(set(old.interfaces) | set(new.interfaces))
    return [compare_interface(old, new, name) for name in names]


def _load_valid(location: Path) -> tuple[Project | None, list[Issue]]:
    result = load_project(location)
    issues = list(result.issues)
    if result.project is not None:
        issues += Validator(result.project).run()
    return (None if errors_only(issues) else result.project), issues


def compare_paths(
    old: Path, new: Path, interfaces: list[str] | None = None
) -> tuple[list[InterfaceReport], list[Issue]]:
    """Load, validate and compare two project versions; reports are empty when either is invalid."""
    old_project, old_issues = _load_valid(old)
    new_project, new_issues = _load_valid(new)
    issues = old_issues + new_issues
    if old_project is None or new_project is None:
        return [], issues
    return compare_projects(old_project, new_project, interfaces), issues


def main(argv: list[str] | None = None) -> int:
    """Print the compatibility report; exit 1 on invalid input or an insufficient version bump."""
    parser = argparse.ArgumentParser(prog="python -m wireforge.compat")
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--interface", action="append", dest="interfaces")
    args = parser.parse_args(argv)
    reports, issues = compare_paths(args.old, args.new, args.interfaces)
    for issue in issues:
        sys.stdout.write(f"{issue}\n")
    for report in reports:
        sys.stdout.write(f"{report.summary()}\n")
        for change in report.changes:
            sys.stdout.write(f"  {change}\n")
    failed = errors_only(issues) or any(not report.version_ok for report in reports)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
