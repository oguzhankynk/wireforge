"""Requirements, design and test documents of a project, generated from its model.

`context()` gathers everything a document needs (the model, the derived low-level requirements,
the trace, the layout analysis and the generated tests); `render()` turns it into Markdown and CSV
files and `strict_findings()` lists what a reviewer would reject. Nothing here reads the clock, the
user name or an absolute path, so the same model always produces the same bytes.

The documents are evidence for the applicant's own process, not a certification: the generator is
not qualified and the high-level requirements stay the user's.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from wireforge import (
    docs_markdown as md,
)
from wireforge import (
    generate,
    golden,
    layout,
    lifecycle_results,
    llr,
    test_cases,
    test_project,
    trace,
    vectors,
    versioning,
)
from wireforge import results as test_results
from wireforge.test_cases import OK

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from wireforge.codec import ChecksumPlugin
    from wireforge.model import Project, Struct

DEPENDENCIES = ("ruamel.yaml", "jsonschema")
ROBUST_INTERFACE_TESTS = ("RejectsCorruptedFrame", "SkipsBytesBeforeSync", "EncodeFrameNeedsRoom")
INTERFACE_TESTS = {
    "DispatchesEveryMessage": (
        "Send the pattern frame of every message through Link and Endpoint.",
        "every message reaches its handler once, no errors",
    ),
    "DeliversByteByByte": (
        "Deliver the first pattern frame one byte at a time.",
        "one frame, none early, no errors",
    ),
    "SkipsBytesBeforeSync": (
        "Send bytes that are not the sync value, then a pattern frame.",
        "one frame, no errors",
    ),
    "RejectsCorruptedFrame": (
        "Invert the last byte of a pattern frame and send it.",
        "the frame is rejected or delivered as the reference codec decides",
    ),
    "EncodeFrameNeedsRoom": (
        "Call encode_frame with buffers that cannot hold the frame.",
        "BufferTooSmall and size 0",
    ),
    "DeliversRawBytesWithoutDecoding": (
        "Send the pattern frames to a handler that only takes raw bytes.",
        "raw frame and payload spans match, nothing decoded",
    ),
    "RawCanForwardToDecoder": (
        "Send a pattern frame to a handler whose raw hook forwards to the decoder.",
        "one raw and one decoded delivery",
    ),
    "WireSize": (
        "Compile-time assertion of header, trailer, payload, frame and buffer sizes.",
        "sizes equal the layout analysis",
    ),
}
STATUS_KINDS = (
    ("OutOfRange", ("RANGE", "ENUM", "BITS")),
    ("CountExceedsMax", ("COUNT",)),
    ("ConstMismatch", ("CONST",)),
    ("ChecksumMismatch", ("CHECKSUM",)),
    ("Truncated", ("TRUNCATED",)),
    ("TrailingBytes", ("TRAILING",)),
    ("BufferTooSmall", ("FRAME_ENCODE",)),
    ("InvalidLength", ("FRAME_LENGTH", "FRAME_MAX")),
    ("UnknownMessageId", ("DISPATCH_UNKNOWN",)),
)
LIMITS = (
    "These documents are evidence for the applicant's own process, not a certification. The code "
    "generator is not qualified; the generated code is verified like hand-written code and the "
    "files here show how. The correctness of the high-level requirements and all tests on the "
    "target hardware belong to the applicant."
)


@dataclass(frozen=True)
class TestEntry:
    """One generated test as the test specification lists it."""

    __test__ = False  # not a pytest class

    id: str
    executable: str
    profile: str | None
    kind: str
    oracle: str
    verifies: tuple[str, ...]
    procedure: str
    expected: str


@dataclass(frozen=True)
class StrictFinding:
    """Something a strict review rejects."""

    rule: str
    message: str
    pointer: str = ""


@dataclass(frozen=True)
class Objective:
    """One row of the DO-178C table: our own summary and where the evidence is."""

    table: str
    objectives: str
    summary: str
    evidence: tuple[str, ...]
    applicant: bool
    applies_at_c: str


@dataclass(frozen=True)
class DocsContext:
    """Everything the documents are made of."""

    project: Project
    content_hash: str
    llrs: tuple[llr.Llr, ...]
    llr_rows: tuple[trace.LlrRow, ...]
    rows: tuple[trace.TraceRow, ...]
    tests: tuple[TestEntry, ...]
    layout: layout.LayoutReport
    generated_files: tuple[str, ...]
    model_files: tuple[tuple[str, str], ...]
    results: test_results.Results | None = None
    deviations: tuple[test_results.Deviation, ...] = ()


NoteKey = tuple[str | None, str, str]  # profile, suite, test name


@dataclass(frozen=True)
class _Note:
    procedure: str
    expected: str
    robust: bool = False
    oracle: str = "reference-codec"


# --- DO-178C table (our own words; the standard is not quoted) ------------------------------------


# Table, objectives, our own summary, evidence (`;` separated), applies at DAL C. A line that starts
# with a space continues the previous one.
DO178C_ROWS = """\
A-1|1-7|Planning|Applicant|yes
A-2|1|High-level requirements developed|requirements.md#high-level-requirements|yes
A-2|2|Derived high-level requirements to the system process|Applicant|yes
A-2|3|Software architecture developed|design.md#overview;design.md#interfaces|yes
A-2|4|Low-level requirements developed|requirements.md#low-level-requirements|yes
A-2|5|Derived low-level requirements to the system process|
 requirements.md#derived-requirements|yes
A-2|6|Source code developed|design.md#generated-files;index.md#baseline|yes
A-2|7|Executable object code produced and loaded|Applicant|yes
A-3|1-7|High-level requirements verified|
 Applicant;traceability.md#requirements-without-model-elements|check
A-4|1,2,4,6|Low-level requirements comply, are accurate, verifiable and traceable|
 traceability.md#low-level-requirements-to-tests;review_checklists.md#low-level-requirements|yes
A-4|7|Algorithms accurate|requirements.md#low-level-requirements;test_spec.md#tests|check
A-4|8-12|Architecture verified|
 design.md#overview;interfaces_coupling.md#data-coupling;review_checklists.md#design|check
A-4|3,10,13|Target compatibility and partitioning|Applicant|check
A-5|1,2,5|Code complies with requirements and architecture and is traceable|
 traceability.md#requirements-to-tests;review_checklists.md#code|yes
A-5|3,4,6|Code is verifiable, conforms to standards and is accurate|
 code_standard.md#rules;code_standard.md#evidence|yes
A-5|7|Integration output complete|Applicant|yes
A-5|8,9|Parameter data items|N/A (no parameter data items)|check
A-6|1,2|Object code complies and is robust with the high-level requirements|
 Applicant (tests on the target)|yes
A-6|3,4|Object code complies and is robust with the low-level requirements|
 test_spec.md#tests;test_report.md#results|yes
A-6|5|Object code compatible with the target|Applicant|yes
A-7|1,2|Test procedures and results correct|
 test_spec.md#tests;test_report.md#results;review_checklists.md#tests|yes
A-7|3,4|Test coverage of high-level and low-level requirements|
 traceability.md#low-level-requirements-to-tests|yes
A-7|7|Statement coverage|test_report.md#coverage|yes
A-7|8|Data and control coupling|
 interfaces_coupling.md#data-coupling;interfaces_coupling.md#control-coupling|yes
A-7|5,6,9|MC/DC, decision coverage and object code coverage|Not at DAL C|check
A-8|all|Configuration management|Applicant;index.md#baseline;environment.md#model-files|yes
A-9|all|Quality assurance|Applicant|yes
A-10|all|Certification liaison|Applicant|yes
"""


def _objectives() -> tuple[Objective, ...]:
    joined = re.sub(r"\n ", "", DO178C_ROWS)
    result = []
    for line in joined.splitlines():
        table, numbers, summary, evidence, at_c = line.split("|")
        items = tuple(part.strip() for part in evidence.split(";"))
        applicant = items[0].startswith(("Applicant", "N/A", "Not at"))
        result.append(Objective(table, numbers, summary, items, applicant, at_c))
    return tuple(result)


DO178C_DAL_C = _objectives()

OVERVIEW = """\
The generated code has three parts. `model.hpp` holds one struct per type and message with
`validate()`, `min_wire_size()` and `max_wire_size()`, the `encode`/`decode` pair and
`serialize`/`deserialize` for messages. `interfaces/<interface>.hpp` holds, per interface, the frame
writer and encoder, the `Handler` the application implements, the `Receiver` that decodes and
dispatches, and the `Endpoint` and `Link` types that join them to a transport. `user_prelude.hpp`
includes the application headers named in the model.

The runtime library supplies the `Encoder` and `Decoder` (a sticky error instead of exceptions),
the `FrameDecoder`, the `Dispatcher`, the `Link` and the lock policies. Nothing allocates memory,
throws or needs RTTI. The generated code has no branches of its own: a condition the generator
needs is written as a call to a runtime helper that is unit tested for both outcomes."""
OVERVIEW = re.sub(r"(?<!\n)\n(?!\n)", " ", OVERVIEW)

CODE_RULES = (
    (
        "Compiled with `-Wall -Wextra -Wconversion -Wsign-conversion -Wuseless-cast` and Clang "
        "`-Weverything`; the build fails on any warning."
    ),
    (
        "No dynamic memory, exceptions, RTTI or threads in the generated and runtime code "
        "(checked on object files)."
    ),
    (
        "Fixed-capacity containers only; all sizes are known at compile time and checked against "
        "the layout analysis."
    ),
    "No branches in generated code; the runtime helpers they call are tested for both outcomes.",
    (
        "Every public entity carries Doxygen comments and the documentation build treats "
        "warnings as errors."
    ),
    (
        "Static analysis with clang-tidy (`cppcoreguidelines-*`, `bugprone-*`, `cert-*`), "
        "cppcheck, the GCC analyzer and the Clang static analyzer; no suppression without an "
        "entry in the suppression register."
    ),
    (
        "Address, undefined behaviour and thread sanitizers on the runtime tests; 32-bit and "
        "big-endian runs."
    ),
)
CODE_EVIDENCE = (
    (
        "`no_heap.md`",
        "no allocation, exception, RTTI or thread symbol in any runtime or generated object",
    ),
    (
        "Coverage of the generated tests",
        "line, branch and function coverage of the generated headers",
    ),
    (
        "Analyzer gates",
        "clang-tidy, cppcheck and compiler analyzers on the runtime and generated code",
    ),
    (
        "Differential tests",
        "generated C++ equals the reference codec on random, boundary and stored inputs",
    ),
)

CHECKLIST_HUMAN = {
    "high-level-requirements": (
        "Each requirement agrees with the system requirements it comes from.",
        "Each requirement is unambiguous and verifiable.",
    ),
    "low-level-requirements": (
        "Each low-level requirement text refines its high-level requirement correctly.",
        "Derived requirements were sent to the system safety assessment.",
    ),
    "design": (
        "The architecture suits the target hardware and its partitioning.",
        "The assumptions in `interfaces_coupling.md` hold on the target.",
    ),
    "code": (
        "The generated code was reviewed against the low-level requirements (sample or full).",
        "The user code behind user types and custom checksums was reviewed.",
    ),
    "tests": (
        "Each test procedure and expected result is correct.",
        "Each test environment matches the one named in `environment.md`.",
    ),
}


# --- small helpers --------------------------------------------------------------------------------


def _ids(keys: Iterable[str]) -> tuple[str, ...]:
    return tuple(llr.display_id(key) for key in keys)


def _preamble(ctx: DocsContext, title: str) -> list[str]:
    project = ctx.project
    files = len(ctx.model_files)
    return md.preamble(title, project.name, project.version, ctx.content_hash, files)


_table = md.table
_csv = md.csv_text
_anchor = md.anchor
_link = md.link
_links = md.links
_format = md.file_format
slug = md.slug


def _hlr_users(ctx: DocsContext) -> dict[str, list[str]]:
    """Model element pointers per requirement id."""
    users: dict[str, list[str]] = {}
    for row in ctx.rows:
        for rid in row.requirements:
            users.setdefault(rid, []).append(row.element)
    return users


# --- context --------------------------------------------------------------------------------------


def _notes(project: Project, plugins: dict[str, ChecksumPlugin] | None) -> dict[NoteKey, _Note]:
    """Return procedure and expected result per (profile, suite, name) from the reference cases."""
    notes: dict[NoteKey, _Note] = {}
    for profile in test_cases.cases(project, plugins):
        for cases in profile.messages:
            name = cases.message.name
            default = cases.default_encoded
            size = f"encodes to {len(default)} bytes" if isinstance(default, bytes) else default
            notes[profile.profile, name, "Defaults"] = _Note(
                "Validate and serialize a default-constructed value.",
                f"validate {cases.default_validated}; {size}",
                cases.default_validated != OK,
            )
            for case in cases.encodes:
                key = (profile.profile, name, _identifier(case.name))
                notes.setdefault(key, _encode_note(case))
            for decode in cases.decodes:
                key = (profile.profile, name, f"Decode_{decode.name}")
                notes.setdefault(
                    key,
                    _Note(
                        _decode_procedure(decode.name),
                        f"deserialize returns {decode.status}",
                        robust=True,
                    ),
                )
    notes.update(_vector_notes(project, plugins))
    for vector in golden.vectors(project, plugins):
        name = _identifier(f"{vector['interface']}_{vector['message']}_{vector['sample']}")
        frame = len(bytes.fromhex(vector["frame"]))
        notes[vector["profile"], "Golden", name] = _Note(
            "Serialize the golden value, compare payload and frame with the golden bytes, "
            "loop the frame back through Link and Endpoint.",
            f"encodes to the golden frame ({frame} bytes) and is delivered unchanged",
        )
    return notes


def _vector_notes(
    project: Project, plugins: dict[str, ChecksumPlugin] | None
) -> dict[NoteKey, _Note]:
    """Return the notes of the requirement vectors; their oracle is the user's expectation."""
    notes: dict[NoteKey, _Note] = {}
    for run in vectors.run(project, plugins):
        vector = run.vector
        level = f"frame of interface {vector.interface}" if vector.interface else "payload"
        verbs = {"decode": "Decode", "encode": "Encode", "both": "Decode and encode"}
        procedure = f"{verbs[vector.direction]} the {level} bytes of vector {vector.name}."
        expected = f"status {vector.status}" if vector.status else "the field values of the vector"
        notes[run.profile, "HlrVector", vector.name] = _Note(
            procedure, expected, robust=vector.status is not None, oracle="hlr-vector"
        )
    return notes


def _identifier(text: str) -> str:
    """Return a case name as the generated test spells it."""
    return re.sub(r"[^A-Za-z0-9]", "_", text)


def _encode_note(case: test_cases.EncodeCase) -> _Note:
    edit = case.edit
    value = "empty" if edit.value is test_cases.CLEAR else repr(edit.value)
    procedure = f"Set `{edit.target}` of the pattern value to {value}, call validate and serialize."
    if isinstance(case.encoded, bytes):
        result = f"encodes to {len(case.encoded)} bytes"
    else:
        result = f"serialize fails with {case.encoded}"
    return _Note(procedure, f"validate {case.validated}; {result}", case.validated != OK)


def _decode_procedure(name: str) -> str:
    if name == "truncated":
        return "Deserialize the pattern payload without its last byte."
    if name == "extended":
        return "Deserialize the pattern payload followed by one extra byte."
    index, _, change = name.removeprefix("byte").partition("_")
    return f"Deserialize the pattern payload with byte {index} changed ({change})."


def _tests(
    project: Project, parsed: list[trace.ParsedTest], notes: dict[NoteKey, _Note]
) -> tuple[TestEntry, ...]:
    names: list[str | None] = [*project.profiles] or [None]
    by_profile = dict(zip(names, test_project.executables(project, "tests"), strict=True))
    entries = []
    for item in parsed:
        note = _test_note(item, notes)
        entries.append(
            TestEntry(
                item.id,
                by_profile[item.profile],
                item.profile,
                "robustness" if note.robust else "normal",
                note.oracle,
                item.ids,
                note.procedure,
                note.expected,
            )
        )
    return tuple(sorted(entries, key=lambda entry: (entry.profile or "", entry.id)))


def _test_note(item: trace.ParsedTest, notes: dict[NoteKey, _Note]) -> _Note:
    found = notes.get((item.profile, item.suite, item.name))
    if found is not None:
        return found
    if item.name == "WireSize":
        return _Note(
            "Compile-time assertion of the wire size of the message.",
            "size equals the layout analysis",
        )
    if item.suite.startswith("Interface_"):
        base = "RawDecodeErrorOf" if item.name.startswith("RawDecodeErrorOf") else item.name
        if base == "RawDecodeErrorOf":
            return _Note(
                "Offer a payload that fails to decode to the default raw hook.",
                "the decode status is returned and nothing is delivered",
                robust=True,
            )
        procedure, expected = INTERFACE_TESTS[item.name]
        return _Note(procedure, expected, robust=item.name in ROBUST_INTERFACE_TESTS)
    return _Note("See the test source.", "as asserted in the test source")


def context(
    project: Project, content_hash: str, plugins: dict[str, ChecksumPlugin] | None = None
) -> DocsContext:
    """Gather what the documents of a valid project are made of (without test results)."""
    traced = trace.report(project, content_hash, plugins)
    parsed = trace.parse_tests(trace.render_test_sources(project, plugins))
    root = Path(project.source.file).parent
    files = []
    for name in project.files:
        relative = Path(os.path.relpath(name, root)).as_posix()
        files.append((relative, hashlib.sha256(Path(name).read_bytes()).hexdigest()))
    return DocsContext(
        project,
        content_hash,
        tuple(llr.derive(project)),
        tuple(traced.llrs),
        tuple(traced.rows),
        _tests(project, parsed, _notes(project, plugins)),
        layout.analyze(project),
        tuple(sorted(generate.render(project, content_hash))),
        tuple(sorted(files)),
    )


# --- strict findings ------------------------------------------------------------------------------


def strict_findings(ctx: DocsContext) -> list[StrictFinding]:
    """List what a strict review rejects (rules DS-1 to DS-4)."""
    project = ctx.project
    if not project.requirements:
        return [StrictFinding("DS-1", "the project defines no requirements")]
    findings: list[StrictFinding] = []
    owners = {item.key.split(".", 1)[0]: False for item in ctx.llrs}
    for item in ctx.llrs:
        owners[item.key.split(".", 1)[0]] |= not item.derived
    for kind, names in (("message", project.messages), ("interface", project.interfaces)):
        for name in names:
            if not owners.get(name, False):
                text = f"{kind} {name} has no high-level requirement, so all its LLRs are derived"
                findings.append(StrictFinding("DS-2", text, f"/{kind}s/{name}"))
    findings += [
        StrictFinding("DS-3", f"{row.id} has no test", row.element)
        for row in ctx.llr_rows
        if not row.tests and not row.runtime_verified
    ]
    used = {rid for row in ctx.rows for rid in row.requirements}
    used |= {parent for item in ctx.llrs for parent in item.parents}
    vectored = {rid for vector in project.vectors.values() for rid in vector.reqs}
    for rid, requirement in project.requirements.items():
        if requirement.verification != "test":
            continue
        pointer = f"/requirements/{rid}"
        if rid not in used and rid not in vectored:
            text = f"requirement {rid} is not linked to any model element"
            findings.append(StrictFinding("DS-4", text, pointer))
        elif rid not in vectored:
            text = f"requirement {rid} is verified by test but has no requirement vector"
            findings.append(StrictFinding("DS-4", text, pointer))
    findings += [
        StrictFinding(rule, text, pointer)
        for rule, text, pointer in lifecycle_results.findings(ctx)
    ]
    return findings


# --- documents ------------------------------------------------------------------------------------

DOCUMENTS = (
    ("index.md", "Baseline, summary, strict result and limits."),
    (
        "requirements.md",
        "High-level, low-level and derived requirements (also as `requirements.csv`).",
    ),
    ("design.md", "Architecture, generated files, interfaces, messages, errors and resources."),
    (
        "interfaces_coupling.md",
        "Data and control coupling between the transport, the generated code and the handler.",
    ),
    (
        "test_spec.md",
        "Every generated test: procedure, expected result, requirements (also `test_spec.csv`).",
    ),
    ("test_report.md", "Test results and coverage (when a test run is given)."),
    ("resources.md", "Memory from the layout analysis and stack use from the test run."),
    ("traceability.md", "Requirements to code to tests and back (also `traceability.csv`)."),
    ("environment.md", "Model files with hashes, tool and library versions."),
    ("code_standard.md", "Rules the generated code follows and the evidence for them."),
    ("review_checklists.md", "Review checklists with the automatic checks already answered."),
    ("do178c_dal_c.md", "Mapping of these documents to the DO-178C objectives at DAL C."),
)


def summary(ctx: DocsContext) -> dict[str, int]:
    """Count requirements, tests and gaps for the index and the service result."""
    return {
        "high-level requirements": len(ctx.project.requirements),
        "low-level requirements": len(ctx.llrs),
        "derived low-level requirements": sum(item.derived for item in ctx.llrs),
        "tests": len(ctx.tests),
        "low-level requirements without a test": sum(
            1 for row in ctx.llr_rows if not row.tests and not row.runtime_verified
        ),
    }


def _index(ctx: DocsContext, findings: list[StrictFinding]) -> str:
    lines = _preamble(ctx, "Lifecycle documents")
    lines += [
        "## Baseline",
        "",
        "The documents below describe exactly the model with the hash above.",
        "",
    ]
    lines += ["## Documents", "", *_table(("File", "Content"), DOCUMENTS), ""]
    lines += ["## Summary", "", *_table(("Item", "Count"), summary(ctx).items()), ""]
    if ctx.results is None:
        lines += ["Results: not provided — strict covered model checks only.", ""]
    else:
        counts = len(ctx.results.tests) + len(ctx.results.selftests)
        lines += [f"Results: {counts} test results and the coverage of the run are included.", ""]
    lines += ["## Strict result", ""]
    rules = {
        "DS-1": "no requirements defined",
        "DS-2": "message or interface without a requirement",
    }
    rules |= {
        "DS-3": "low-level requirement without a test",
        "DS-4": "requirement without a model element or vector",
    }
    if ctx.results is not None:
        rules |= {
            "DS-5": "failed or skipped test",
            "DS-6": "statement coverage below 100 % or runtime not measured",
            "DS-7": "uncovered line without a justification",
        }
    rows = []
    for rule, text in rules.items():
        count = sum(1 for item in findings if item.rule == rule)
        rows.append((rule, text, "FAIL" if count else "OK", count))
    lines += [*_table(("Rule", "Checks", "Result", "Findings"), rows), ""]
    lines += [f"- {item.rule}: {item.message}" for item in findings]
    lines += ["", "## Limits", "", LIMITS, ""]
    return "\n".join(lines)


def _requirements(ctx: DocsContext) -> tuple[str, str]:
    project = ctx.project
    users = _hlr_users(ctx)
    counts: dict[str, int] = {}
    for item in ctx.llrs:
        for parent in item.parents:
            counts[parent] = counts.get(parent, 0) + 1
    lines = _preamble(ctx, "Requirements")
    lines += ["## High-level requirements", ""]
    high = [
        (
            f"{_anchor(rid)}{rid}",
            item.text,
            item.reference or "-",
            item.verification,
            ", ".join(f"`{pointer}`" for pointer in users.get(rid, [])),
            counts.get(rid, 0),
        )
        for rid, item in project.requirements.items()
    ]
    if high:
        headers = ("ID", "Text", "Source", "Verification", "Model elements", "LLRs")
        lines += _table(headers, high)
    else:
        lines += ["The project defines no high-level requirements."]
    lines += ["", "## Low-level requirements", ""]
    low = [
        (
            f"{_anchor(row.id)}{row.id}",
            row.kind,
            row.text,
            f"`{row.element}`",
            ", ".join(row.parents) or "derived",
            "yes" if row.derived else "no",
        )
        for row in ctx.llr_rows
    ]
    lines += _table(("ID", "Kind", "Text", "Element", "Parents", "Derived"), low)
    derived = [row for row in ctx.llr_rows if row.derived]
    lines += ["", "## Derived requirements", ""]
    if project.requirements:
        lines += [
            f"{len(derived)} low-level requirements have no high-level requirement behind them."
        ]
        lines += ["Provide them to the system safety assessment.", ""]
        lines += [f"- {_link(row.id)}" for row in derived]
    else:
        lines += ["Without a requirements catalog every low-level requirement is derived."]
    rows: list[Sequence[object]] = [
        (rid, "HLR", item.verification, item.text, " ".join(users.get(rid, [])), "")
        for rid, item in project.requirements.items()
    ]
    rows += [
        (row.id, "LLR", row.kind, row.text, row.element, " ".join(row.parents))
        for row in ctx.llr_rows
    ]
    csv_text = _csv(("id", "level", "kind", "text", "elements", "parents"), rows)
    return "\n".join(lines) + "\n", csv_text


def _field_rows(fields: Iterable[layout.FieldLayout]) -> list[Sequence[object]]:
    return [
        (
            row.path,
            row.type,
            f"{row.offset_min}..{row.offset_max}"
            if row.offset_min != row.offset_max
            else row.offset_min,
            f"{row.size_min}..{row.size_max}" if row.size_min != row.size_max else row.size_min,
            row.range,
            row.default,
        )
        for row in fields
    ]


FIELD_HEADERS = ("Field", "Type", "Offset", "Size", "Range", "Default")


def _design(ctx: DocsContext) -> str:
    project = ctx.project
    lines = _preamble(ctx, "Design")
    lines += ["## Overview", "", OVERVIEW, "", "## Generated files", ""]
    lines += _table(
        ("File", "Responsibility"), [(name, _file_role(name)) for name in ctx.generated_files]
    )
    lines += ["", "## Interfaces", ""]
    for interface in sorted(project.interfaces.values(), key=lambda item: item.name):
        lines += [f"### Interface {interface.name}", "", _anchor(f"interface-{interface.name}")]
        lines += [
            (
                f"Version {interface.version}, direction {interface.direction}, framing "
                f"{interface.framing.kind}. Requirements: {', '.join(interface.reqs) or '-'}."
            ),
            "",
        ]
        for profile in ctx.layout.profiles:
            found = next((item for item in profile.interfaces if item.name == interface.name), None)
            if found is None:
                continue
            label = f"Profile {profile.profile}" if profile.profile else "Layout"
            lines += [f"#### {label}", ""]
            sizes = (
                ("Header bytes", found.header_size),
                ("Trailer bytes", found.trailer_size),
                ("Largest payload", found.max_payload),
                ("Largest frame", found.max_frame),
                ("Receive buffer", found.rx_buffer),
                ("Transmit buffer", found.tx_buffer),
                ("Link RAM", found.link_ram),
            )
            lines += [*_table(("Item", "Bytes"), sizes), ""]
            if found.header:
                lines += ["Header", "", *_table(FIELD_HEADERS, _field_rows(found.header)), ""]
            if found.trailer:
                lines += ["Trailer", "", *_table(FIELD_HEADERS, _field_rows(found.trailer)), ""]
            rows = [
                (
                    m.name,
                    m.id if m.id is not None else "-",
                    f"{m.payload_min}..{m.payload_max}",
                    f"{m.frame_min}..{m.frame_max}",
                )
                for m in found.messages
            ]
            lines += [*_table(("Message", "Id", "Payload bytes", "Frame bytes"), rows), ""]
    lines += ["## Messages and types", ""]
    for item in (
        *sorted(project.types.values(), key=_name),
        *sorted(project.messages.values(), key=_name),
    ):
        lines += _struct_section(ctx, item)
    lines += ["## Error handling", ""]
    statuses = [
        (f"`{status}`", ", ".join(kinds), sum(row.kind in kinds for row in ctx.llr_rows))
        for status, kinds in STATUS_KINDS
    ]
    lines += [*_table(("Status", "Requirement kinds", "Requirements"), statuses), ""]
    lines += ["## Resources", ""]
    resources = [
        (
            profile.profile or "-",
            profile.link_ram,
            max((item.max_frame for item in profile.interfaces), default=0),
        )
        for profile in ctx.layout.profiles
    ]
    lines += _table(("Profile", "Link RAM bytes", "Largest frame bytes"), resources)
    return "\n".join(lines) + "\n"


def _name(item: Struct) -> str:
    return item.name


def _file_role(name: str) -> str:
    if name.endswith("model.hpp"):
        return "Types, messages, validation, wire sizes, encode and decode."
    if name.endswith("user_prelude.hpp"):
        return "Application headers and checks of user constants."
    return "Frame encoder, handler, receiver and endpoint of one interface."


def _struct_section(ctx: DocsContext, item: Struct) -> list[str]:
    kind = "Message" if item.message is not None else "Type"
    lines = [f"### {kind} {item.name}", "", _anchor(f"{kind.lower()}-{item.name}")]
    if item.description:
        lines += [item.description, ""]
    if item.message is not None:
        for profile in ctx.layout.profiles:
            found = next((m for m in profile.messages if m.name == item.name), None)
            if found is None:
                continue
            label = f"Profile {profile.profile}" if profile.profile else "Layout"
            lines += [f"{label}: {found.size_min}..{found.size_max} bytes.", ""]
            lines += [*_table(FIELD_HEADERS, _field_rows(found.fields)), ""]
    else:
        rows = [
            (f.name, f.type, f.bits or "-", f.default if f.default is not None else "-")
            for f in item.fields
        ]
        lines += [*_table(("Field", "Type", "Bits", "Default"), rows), ""]
    owned = [row.id for row in ctx.llr_rows if row.id.startswith(f"LLR-{item.name}.")]
    lines += [f"Low-level requirements: {_links(owned)}", ""]
    return lines


class _Verified:
    """Names the tests of one interface that cover a coupling."""

    def __init__(self, interface: str, tests: set[str]) -> None:
        self.interface = interface
        self.tests = tests

    def __call__(self, *names: str, extra: Sequence[str] = ()) -> str:
        found = [f"Interface_{self.interface}.{name}" for name in names]
        found = [item for item in found if item in self.tests]
        return ", ".join(f"`{item}`" for item in [*found, *extra]) or "-"


def _coupling(ctx: DocsContext) -> str:
    lines = _preamble(ctx, "Interfaces and coupling")
    lines += ["## Data coupling", ""]
    for interface in sorted(ctx.project.interfaces.values(), key=lambda item: item.name):
        lines += [f"### Interface {interface.name}", ""]
        tests = {
            entry.id for entry in ctx.tests if entry.id.startswith(f"Interface_{interface.name}.")
        }
        golden_tests = sorted(
            {entry.id for entry in ctx.tests if entry.id.startswith(f"Golden.{interface.name}_")}
        )

        verified = _Verified(interface.name, tests)

        rows: list[Sequence[object]] = []
        if interface.direction in {"rx", "both"}:
            rows += [
                (
                    "Transport (user)",
                    "`Link`",
                    "received bytes",
                    "`receive(Span)` into the receive ring",
                    verified("DeliversByteByByte", "DispatchesEveryMessage"),
                ),
                (
                    "`FrameDecoder`",
                    "`Dispatcher`",
                    "frame span",
                    "call on the same thread",
                    verified("DispatchesEveryMessage"),
                ),
                (
                    "`Receiver`",
                    "`Handler` (user)",
                    "decoded message (on the stack)",
                    "virtual `on<Message>Received(const Message&)`",
                    verified("DispatchesEveryMessage"),
                ),
                (
                    "`Receiver`",
                    "`Handler` (user)",
                    "raw payload and frame (valid during the call)",
                    "`on<Message>Raw(const RawMessage&)`",
                    verified("DeliversRawBytesWithoutDecoding", "RawCanForwardToDecoder"),
                ),
            ]
        if interface.direction in {"tx", "both"}:
            rows += [
                (
                    "User",
                    "`encode_frame` / `Link::send`",
                    "message value",
                    "call",
                    verified("EncodeFrameNeedsRoom", extra=golden_tests[:3]),
                ),
                (
                    "`Link`",
                    "Transport (user)",
                    "frame bytes",
                    "`send(Span)`",
                    verified(extra=golden_tests[:3]),
                ),
            ]
        lines += [*_table(("Producer", "Consumer", "Data", "Mechanism", "Verified by"), rows), ""]
    lines += ["## Control coupling", ""]
    lines += [
        (
            "Receiving: `Link::poll()` calls `Transport::receive`, which feeds "
            "`FrameDecoder::push`; the decoder calls `Dispatcher::dispatch`, which calls the "
            "`Receiver`, which calls the `Handler`."
        ),
        "",
        "Sending: `encode_frame` writes the frame; `Link::send_frame` hands it to the transport.",
        "",
        "## Assumptions",
        "",
        "- The default lock policy is a standard mutex; a `NullLock` is for single-threaded use.",
        "- The raw spans given to `on<Message>Raw` are valid only during the call.",
        "- The generated codec is reentrant: it keeps no state outside the objects it is given.",
        "",
    ]
    return "\n".join(lines)


def _test_spec(ctx: DocsContext) -> tuple[str, str]:
    lines = _preamble(ctx, "Test specification")
    lines += [
        "## Tests",
        "",
        f"{len(ctx.tests)} tests. Oracle `reference-codec` is the reference codec of WireForge.",
        "",
    ]
    rows = [
        (
            f"{_anchor('test-' + (entry.profile or 'default') + '-' + entry.id)}{entry.id}",
            entry.executable,
            entry.profile or "-",
            entry.kind,
            entry.oracle,
            _links(entry.verifies),
            entry.procedure,
            entry.expected,
        )
        for entry in ctx.tests
    ]
    headers = ("ID", "Executable", "Profile", "Kind", "Oracle", "Verifies", "Procedure", "Expected")
    lines += _table(headers, rows)
    csv_rows = [
        (
            e.id,
            e.executable,
            e.profile or "",
            e.kind,
            e.oracle,
            " ".join(e.verifies),
            e.procedure,
            e.expected,
        )
        for e in ctx.tests
    ]
    return "\n".join(lines) + "\n", _csv(
        ("id", "executable", "profile", "kind", "oracle", "verifies", "procedure", "expected"),
        csv_rows,
    )


def _traceability(ctx: DocsContext) -> tuple[str, str]:
    lines = _preamble(ctx, "Traceability")
    lines += ["## Requirements to tests", ""]
    users = _hlr_users(ctx)
    rows: list[Sequence[object]] = []
    for rid in ctx.project.requirements:
        low = [row for row in ctx.llr_rows if rid in row.parents]
        tests = {test for row in low for test in row.tests}
        tests |= {entry.id for entry in ctx.tests if rid in entry.verifies}
        elements = ", ".join(f"`{item}`" for item in users.get(rid, [])) or "-"
        generated = sorted({row.generated for row in low if row.generated})
        rows.append(
            (
                _link(rid),
                elements,
                _links([row.id for row in low]),
                ", ".join(generated) or "-",
                len(tests),
            )
        )
    if rows:
        lines += _table(("Requirement", "Model elements", "LLRs", "Code", "Tests"), rows)
    else:
        lines += ["The project defines no high-level requirements."]
    lines += ["", "## Low-level requirements to tests", ""]
    code_rows = [
        (
            _link(row.id),
            row.generated or "-",
            ", ".join(row.tests) or ("runtime tests" if row.runtime_verified else "-"),
        )
        for row in ctx.llr_rows
    ]
    lines += _table(("LLR", "Code", "Tests"), code_rows)
    lines += ["", "## Tests to requirements", ""]
    test_rows = [(entry.id, entry.profile or "-", _links(entry.verifies)) for entry in ctx.tests]
    lines += _table(("Test", "Profile", "Verifies"), test_rows)
    findings = strict_findings(ctx)
    lines += ["", "## Requirements without model elements", ""]
    lines += [f"- {item.message}" for item in findings if item.rule == "DS-4"] or ["none"]
    lines += ["", "## Gaps", ""]
    lines += [f"- {item.rule}: {item.message}" for item in findings] or ["none"]
    csv_rows = [
        (
            row.id,
            row.kind,
            " ".join(row.parents),
            row.generated,
            " ".join(row.tests),
            "yes" if row.derived else "no",
            lifecycle_results.outcome_of(ctx, row.tests),
        )
        for row in ctx.llr_rows
    ]
    return "\n".join(lines) + "\n", _csv(
        ("llr", "kind", "parents", "code", "tests", "derived", "result"), csv_rows
    )


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _environment(ctx: DocsContext) -> str:
    lines = _preamble(ctx, "Environment")
    lines += [
        "## Model files",
        "",
        *_table(("File", "sha256"), [(name, f"`{digest}`") for name, digest in ctx.model_files]),
        "",
    ]
    versions = [("WireForge", versioning.TOOL_VERSION), ("Python", platform.python_version())]
    versions += [(name, _version(name)) for name in DEPENDENCIES]
    versions.append(("File format", _format()))
    lines += ["## Tool versions", "", *_table(("Item", "Version"), versions), ""]
    lines += lifecycle_results.toolchain_lines(ctx)
    return "\n".join(lines)


def _code_standard(ctx: DocsContext) -> str:
    lines = _preamble(ctx, "Code standard")
    lines += ["## Rules", "", *[f"- {rule}" for rule in CODE_RULES], "", "## Evidence", ""]
    lines += [*_table(("Evidence", "Shows"), CODE_EVIDENCE), ""]
    return "\n".join(lines)


def _names_sources(model: str) -> bool:
    """Whether every struct of the generated model header says where it was defined."""
    return model.count("\nstruct ") == model.count("/// Defined in ")


def _checklists(ctx: DocsContext) -> str:
    findings = strict_findings(ctx)
    project = ctx.project

    def auto(text: str, *, ok: bool) -> str:
        return f"- [auto: {'PASS' if ok else 'FAIL'}] {text}"

    def human(section: str) -> list[str]:
        return [f"- [ ] reviewer: {text}" for text in CHECKLIST_HUMAN[section]]

    def clean(rule: str) -> bool:
        return not any(item.rule == rule for item in findings)

    sources = generate.render(project, ctx.content_hash)
    laid_out = {m.name for p in ctx.layout.profiles for m in p.messages}
    robust_by = {entry.id.split(".")[0] for entry in ctx.tests if entry.kind == "robustness"}
    lines = _preamble(ctx, "Review checklists")
    lines += ["## High-level requirements", ""]
    lines += [
        auto(
            "every requirement has a text",
            ok=all(r.text.strip() for r in project.requirements.values()),
        ),
        auto("every requirement is linked to a model element", ok=clean("DS-4")),
        *human("high-level-requirements"),
        "",
        "## Low-level requirements",
        "",
        auto("every low-level requirement has a test", ok=clean("DS-3")),
        auto(
            f"derived low-level requirements listed ({sum(i.derived for i in ctx.llrs)})", ok=True
        ),
        auto(
            "no unresolved expression in a requirement text",
            ok=all("{" not in i.text for i in ctx.llrs),
        ),
        *human("low-level-requirements"),
        "",
        "## Design",
        "",
        auto(
            "every message is in the layout analysis",
            ok=set(project.messages) <= laid_out or bool(project.profiles),
        ),
        auto("link RAM is computed for every profile", ok=bool(ctx.layout.profiles)),
        *human("design"),
        "",
        "## Code",
        "",
        auto(
            "every generated file carries the model hash",
            ok=all(ctx.content_hash in s for s in sources.values()),
        ),
        auto(
            "every generated struct names its model source",
            ok=_names_sources(sources[f"{project.name}/model.hpp"]),
        ),
        *human("code"),
        "",
        "## Tests",
        "",
        auto(
            "every generated test records the requirements it verifies",
            ok=all(e.verifies for e in ctx.tests),
        ),
        auto("every message has a robustness test", ok=set(project.messages) <= robust_by),
        *human("tests"),
        "",
    ]
    return "\n".join(lines)


def _do178c(ctx: DocsContext) -> str:
    lines = _preamble(ctx, "DO-178C DAL C mapping")
    lines += [
        "## Scope",
        "",
        LIMITS,
        "",
        "The table uses our own wording and the objective numbers only.",
        "",
    ]
    lines += ["## Objectives", ""]
    rows = [
        (
            item.table,
            item.objectives,
            item.summary,
            "Applicant"
            if item.applicant
            else ", ".join(f"[{ref}]({ref})" for ref in item.evidence),
            item.applies_at_c,
        )
        for item in DO178C_DAL_C
    ]
    lines += _table(("Table", "Objectives", "Summary", "Evidence", "Applies at C"), rows)
    lines += ["", "## Applicant activities", ""]
    lines += [
        (
            "Planning, configuration management, quality assurance, certification liaison, "
            "system safety assessment, tests on the target and the review of the high-level "
            "requirements are the applicant's."
        ),
        "Rows marked `check` must be confirmed against your licensed copy of the standard.",
        "",
    ]
    return "\n".join(lines)


def render(ctx: DocsContext) -> dict[str, str]:
    """Render every document: file name -> content."""
    findings = strict_findings(ctx)
    requirements, requirements_csv = _requirements(ctx)
    spec, spec_csv = _test_spec(ctx)
    traceability, traceability_csv = _traceability(ctx)
    return {
        "index.md": _index(ctx, findings),
        "requirements.md": requirements,
        "requirements.csv": requirements_csv,
        "design.md": _design(ctx),
        "interfaces_coupling.md": _coupling(ctx),
        "test_spec.md": spec,
        "test_spec.csv": spec_csv,
        "test_report.md": lifecycle_results.test_report(ctx),
        "resources.md": lifecycle_results.resources(ctx),
        "traceability.md": traceability,
        "traceability.csv": traceability_csv,
        "environment.md": _environment(ctx),
        "code_standard.md": _code_standard(ctx),
        "review_checklists.md": _checklists(ctx),
        "do178c_dal_c.md": _do178c(ctx),
    }
