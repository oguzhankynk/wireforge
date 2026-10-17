"""Change impact between two baselines of a project.

`compare()` says what a change did to the high-level requirements, the low-level requirements
(by key, shown with the ids of the new baseline), the generated code, the generated tests and the
wire compatibility, and lists the tests to run and review again: every test that verifies a
requirement that changed, and every test that changed itself.
"""

from __future__ import annotations

import csv
import difflib
import io
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wireforge import docs_markdown as md
from wireforge import generate, llr, trace

if TYPE_CHECKING:
    from wireforge.codec import ChecksumPlugin
    from wireforge.model import Project

TEST_HEAD = re.compile(r"^TEST\((\w+), (\w+)\) \{$", re.MULTILINE)
PROFILE = re.compile(r"^#if defined\(WIREFORGE_PROFILE_(\w+)\)$", re.MULTILINE)
SYMBOL = re.compile(r"^(?:struct|enum class|class|namespace) (\w+)")
FUNCTION = re.compile(r"(\w+)\((?:const\s+)?(?:[\w:]+<[^>]*>\s+)?([A-Za-z_][\w:]*)?")
CSV_COLUMNS = ("area", "change", "id", "detail")


@dataclass(frozen=True)
class Side:
    """One baseline, reduced to what the comparison needs."""

    project: Project
    llrs: dict[str, llr.Llr]
    files: dict[str, str]
    tests: dict[str, tuple[str, tuple[str, ...]]]


@dataclass(frozen=True)
class Change:
    """One difference: area (hlr, llr, code, test, wire), kind of change, id and detail."""

    area: str
    change: str
    id: str
    detail: str = ""


@dataclass(frozen=True)
class ImpactReport:
    """All differences of a comparison and the tests to run again."""

    old_label: str
    new_label: str
    baseline_note: str
    changes: tuple[Change, ...]
    rerun: tuple[str, ...]
    compatibility: str


def side(
    project: Project, content_hash: str, plugins: dict[str, ChecksumPlugin] | None = None
) -> Side:
    """Reduce a valid project to the data of the comparison."""
    items = {item.key: item for item in llr.derive(project)}
    files = generate.render(project, content_hash)
    return Side(project, items, files, _tests(trace.render_test_sources(project, plugins)))


def _tests(sources: dict[str, str]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Map `<profile>:Suite.Name` to the text of the test and the requirement ids it records."""
    ids = {f"{item.profile or '-'}:{item.id}": item.ids for item in trace.parse_tests(sources)}
    found: dict[str, tuple[str, tuple[str, ...]]] = {}
    for text in sources.values():
        profile = "-"
        heads = list(TEST_HEAD.finditer(text))
        for index, head in enumerate(heads):
            for marker in PROFILE.finditer(text, 0, head.start()):
                profile = marker[1]
            end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
            key = f"{profile}:{head[1]}.{head[2]}"
            found[key] = (text[head.start() : end], ids.get(key, ()))
    return found


def _symbols(old: str, new: str) -> list[str]:
    """Name the declarations of `new` that contain a line that differs from `old`.

    The banner at the top of a generated file carries the model hash and changes with every
    model change, so it does not count.
    """
    after = new.splitlines()
    skip = next((n + 1 for n, line in enumerate(after) if line.strip() == "*/"), 0)
    names: list[str] = []
    matcher = difflib.SequenceMatcher(None, old.splitlines(), after, autojunk=False)
    for tag, _, _, start, end in matcher.get_opcodes():
        if tag == "equal":
            continue
        for number in range(max(start, skip), max(end, start + 1)):
            if number < skip or number >= len(after):
                continue
            name = _enclosing(after, number)
            if name not in names:
                names.append(name)
    return names or ["(banner only)"]


def _enclosing(lines: list[str], index: int) -> str:
    for position in range(index, -1, -1):
        found = SYMBOL.match(lines[position])
        if found:
            return found[1]
        if lines[position].startswith(("inline ", "[[nodiscard]]", "constexpr ")):
            call = FUNCTION.search(lines[position])
            if call:
                return f"{call[1]}({call[2] or ''})"
    return "(file scope)"


def compare(
    old: Side, new: Side, *, labels: tuple[str, str], compatibility: str = ""
) -> ImpactReport:
    """Compare two baselines."""
    changes: list[Change] = []
    changes += _requirements(old, new)
    changed_keys = _llr_changes(old, new, changes)
    for name in sorted(set(old.files) | set(new.files)):
        if name not in old.files:
            changes.append(Change("code", "added", name))
        elif name not in new.files:
            changes.append(Change("code", "removed", name))
        elif old.files[name] != new.files[name]:
            symbols = ", ".join(_symbols(old.files[name], new.files[name]))
            changes.append(Change("code", "changed", name, symbols))
    changed_tests = _test_changes(old, new, changes)
    shown = {key: llr.display_id(key, new.project.llr_ids) for key in changed_keys}
    changed_ids = set(shown.values())
    rerun = sorted(
        {
            key.split(":", 1)[1]
            for key, (_, ids) in new.tests.items()
            if changed_ids & set(ids) or key in changed_tests
        }
    )
    note = _baseline_note(old.project, new.project)
    return ImpactReport(labels[0], labels[1], note, tuple(changes), tuple(rerun), compatibility)


def _requirements(old: Side, new: Side) -> list[Change]:
    found: list[Change] = []
    before, after = old.project.requirements, new.project.requirements
    for rid in sorted(set(before) | set(after)):
        if rid not in before:
            found.append(Change("hlr", "added", rid, after[rid].text))
        elif rid not in after:
            found.append(Change("hlr", "removed", rid))
        elif before[rid].text != after[rid].text:
            found.append(Change("hlr", "text changed", rid, after[rid].text))
    return found


def _llr_changes(old: Side, new: Side, changes: list[Change]) -> set[str]:
    """Add the low-level differences; return the keys whose requirement changed."""
    touched: set[str] = set()
    for key in sorted(set(old.llrs) | set(new.llrs)):
        before, after = old.llrs.get(key), new.llrs.get(key)
        if before is None and after is not None:
            changes.append(
                Change("llr", "added", llr.display_id(key, new.project.llr_ids), after.text)
            )
        elif after is None and before is not None:
            changes.append(Change("llr", "removed", llr.display_id(key, old.project.llr_ids)))
        elif before is not None and after is not None:
            ident = llr.display_id(key, new.project.llr_ids)
            if before.text != after.text:
                changes.append(Change("llr", "text changed", ident, after.text))
                touched.add(key)
            if before.parents != after.parents:
                was = ", ".join(before.parents) or "derived"
                now = ", ".join(after.parents) or "derived"
                detail = f"{was} -> {now}"
                changes.append(Change("llr", "parents changed", ident, detail))
                touched.add(key)
        if after is not None and before is None:
            touched.add(key)
    return touched


def _test_changes(old: Side, new: Side, changes: list[Change]) -> set[str]:
    changed: set[str] = set()
    for key in sorted(set(old.tests) | set(new.tests)):
        name = key.split(":", 1)[1] + ("" if key.startswith("-:") else f" ({key.split(':', 1)[0]})")
        if key not in old.tests:
            changes.append(Change("test", "added", name))
            changed.add(key)
        elif key not in new.tests:
            changes.append(Change("test", "removed", name))
        elif old.tests[key][0] != new.tests[key][0]:
            changes.append(Change("test", "changed", name))
            changed.add(key)
    return changed


def _baseline_note(old: Project, new: Project) -> str:
    before = (old.requirements_source or {}).get("baseline")
    after = (new.requirements_source or {}).get("baseline")
    if before != after:
        return f"The high-level baseline changed from {before or 'none'} to {after or 'none'}."
    return ""


def to_markdown(report: ImpactReport) -> str:
    """Render impact.md."""
    lines = [f"# Change impact — {report.old_label} to {report.new_label}", ""]
    if report.baseline_note:
        lines += [report.baseline_note, ""]
    counts: dict[tuple[str, str], int] = {}
    for item in report.changes:
        counts[item.area, item.change] = counts.get((item.area, item.change), 0) + 1
    summary = [(area, change, number) for (area, change), number in sorted(counts.items())]
    lines += ["## Summary", "", *md.table(("Area", "Change", "Count"), summary), ""]
    titles = {
        "hlr": "High-level requirements",
        "llr": "Low-level requirements",
        "code": "Generated code",
        "test": "Generated tests",
    }
    for area, title in titles.items():
        rows = [(i.change, i.id, i.detail) for i in report.changes if i.area == area]
        lines += [f"## {title}", ""]
        lines += md.table(("Change", "Id", "Detail"), rows) if rows else ["none"]
        lines += [""]
    lines += ["## Wire compatibility", "", report.compatibility.strip() or "not compared", ""]
    lines += ["## Tests to run and review again", ""]
    lines += [f"- {name}" for name in report.rerun] or ["none"]
    return "\n".join([*lines, ""])


def to_csv(report: ImpactReport) -> str:
    """Render impact.csv."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows((i.area, i.change, i.id, i.detail) for i in report.changes)
    writer.writerows(("rerun", "run and review", name, "") for name in report.rerun)
    return out.getvalue()
