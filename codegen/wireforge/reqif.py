"""ReqIF 1.2: read high-level requirements from the customer's tool, write the low-level ones.

ReqIF is the exchange format of requirements tools (DOORS, DOORS Next, Polarion, codebeamer, Jama
and others). `read()` returns the id and the text of every requirement object of a `.reqif` or
`.reqifz` file; `write_llrs()` writes the low-level requirements of a project as a specification
whose requirements refine placeholders for the high-level requirement ids, so that the customer's
tool can link them to its own requirements by id. Ids and time stamps are deterministic.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

from wireforge.results import Node, ResultsError, parse_xml

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from wireforge.lifecycle import DocsContext

NAMESPACE = "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd"
FOREIGN_ID = "ReqIF.ForeignID"
TEXT = "ReqIF.Text"
EPOCH = "1970-01-01T00:00:00Z"
ARCHIVE_MEMBER = "llr.reqif"
GIT_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Requirement:
    """A requirement object of a ReqIF file."""

    id: str
    text: str


@dataclass(frozen=True)
class Document:
    """What was read from a ReqIF file."""

    tool: str
    requirements: tuple[Requirement, ...]
    attributes: tuple[str, ...]


def _text(raw: bytes) -> str:
    return raw.decode("utf-8-sig")


def _content(path: Path) -> str:
    if path.suffix.lower() == ".reqifz":
        try:
            with zipfile.ZipFile(path) as archive:
                names = [name for name in archive.namelist() if name.lower().endswith(".reqif")]
                if not names:
                    message = f"{path}: the archive holds no .reqif file"
                    raise ResultsError(message)
                return _text(archive.read(names[0]))
        except (zipfile.BadZipFile, OSError) as error:
            message = f"{path}: cannot read the archive: {error}"
            raise ResultsError(message) from error
    try:
        return _text(path.read_bytes())
    except OSError as error:
        message = f"{path}: cannot read: {error}"
        raise ResultsError(message) from error


def _values(element: Node, names: dict[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    values = element.find("VALUES")
    for value in values.children if values is not None else []:
        definition = value.find("DEFINITION")
        reference = (
            definition.children[0].all_text().strip() if definition and definition.children else ""
        )
        name = names.get(reference, reference)
        if value.tag == "ATTRIBUTE-VALUE-XHTML":
            content = value.find("THE-VALUE")
            found[name] = " ".join((content.all_text() if content is not None else "").split())
        else:
            found[name] = value.get("THE-VALUE")
    return found


def read(path: Path, id_attr: str | None = None, text_attr: str | None = None) -> Document:
    """Read the requirement objects of a ReqIF file.

    The id comes from `ReqIF.ForeignID` and the text from `ReqIF.Text` unless other attribute names
    are given; when an attribute is missing the error lists the names the file has.
    """
    try:
        root = parse_xml(_content(path))
    except Exception as error:  # expat errors and an empty document
        message = f"{path}: not a ReqIF document: {error}"
        raise ResultsError(message) from error
    names = {
        item.get("IDENTIFIER"): item.get("LONG-NAME")
        for item in root.iter("ATTRIBUTE-DEFINITION-STRING")
        + root.iter("ATTRIBUTE-DEFINITION-XHTML")
        + root.iter("ATTRIBUTE-DEFINITION-INTEGER")
        + root.iter("ATTRIBUTE-DEFINITION-ENUMERATION")
    }
    tool_node = next(iter(root.iter("REQ-IF-TOOL-ID")), None)
    tool = tool_node.all_text().strip() if tool_node is not None else ""
    wanted_id, wanted_text = id_attr or FOREIGN_ID, text_attr or TEXT
    objects = [_values(item, names) for item in root.iter("SPEC-OBJECT")]
    available = sorted({name for values in objects for name in values})
    for wanted in (wanted_id, wanted_text):
        if objects and wanted not in available:
            message = (
                f"{path}: no attribute {wanted!r}; the file has {', '.join(available) or 'none'}. "
                "Name the attributes with --id-attr and --text-attr."
            )
            raise ResultsError(message)
    requirements = tuple(
        Requirement(values[wanted_id].strip(), values.get(wanted_text, "").strip())
        for values in objects
        if values.get(wanted_id, "").strip()
    )
    return Document(tool, requirements, tuple(available))


# --- writing -----------------------------------------------------------------------------------


def _identifier(text: str) -> str:
    return "_wf_" + hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def commit_time(directory: Path) -> str:
    """Return the author-independent time of the last commit, or the epoch without git."""
    git = shutil.which("git")
    if git is None:
        return EPOCH
    try:
        done = subprocess.run(
            [git, "log", "-1", "--format=%cI"],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return EPOCH
    stamp = done.stdout.strip()
    return stamp if done.returncode == 0 and stamp else EPOCH


def _attribute_type(ident: str, name: str, datatype: str, stamp: str) -> str:
    return (
        f'<ATTRIBUTE-DEFINITION-{datatype} IDENTIFIER="{ident}" LAST-CHANGE="{stamp}" '
        f"LONG-NAME={quoteattr(name)}><TYPE><DATATYPE-DEFINITION-{datatype}-REF>"
        f"{_identifier('dt-' + datatype)}</DATATYPE-DEFINITION-{datatype}-REF></TYPE>"
        f"</ATTRIBUTE-DEFINITION-{datatype}>"
    )


def _object(ident: str, type_ident: str, values: Iterable[tuple[str, str, str]], stamp: str) -> str:
    rendered = "".join(
        f"<ATTRIBUTE-VALUE-{kind} THE-VALUE={quoteattr(value)}><DEFINITION>"
        f"<ATTRIBUTE-DEFINITION-{kind}-REF>{definition}</ATTRIBUTE-DEFINITION-{kind}-REF>"
        f"</DEFINITION></ATTRIBUTE-VALUE-{kind}>"
        for kind, definition, value in values
    )
    return (
        f'<SPEC-OBJECT IDENTIFIER="{ident}" LAST-CHANGE="{stamp}"><TYPE><SPEC-OBJECT-TYPE-REF>'
        f"{type_ident}</SPEC-OBJECT-TYPE-REF></TYPE><VALUES>{rendered}</VALUES></SPEC-OBJECT>"
    )


def llr_document(ctx: DocsContext, stamp: str) -> str:
    """Return the ReqIF text of the low-level requirements of a project."""
    project = ctx.project
    t_llr, t_head, t_hlr = (_identifier(name) for name in ("type-llr", "type-head", "type-hlr"))
    t_rel, t_spec = _identifier("type-refines"), _identifier("type-spec")
    a = {
        name: _identifier("attr-" + name)
        for name in ("id", "text", "kind", "element", "source", "derived", "hid", "htext")
    }
    parents = sorted({parent for row in ctx.llr_rows for parent in row.parents})
    objects: list[str] = []
    relations: list[str] = []
    hierarchy: dict[str, list[str]] = {}
    for row in ctx.llr_rows:
        ident = _identifier(row.id)
        values = [
            ("STRING", a["id"], row.id),
            ("STRING", a["text"], row.text),
            ("STRING", a["kind"], row.kind),
            ("STRING", a["element"], row.element),
            ("STRING", a["source"], row.generated or ""),
            ("BOOLEAN", a["derived"], "true" if row.derived else "false"),
        ]
        objects.append(_object(ident, t_llr, values, stamp))
        hierarchy.setdefault(
            row.element.split("/")[2] if row.element.count("/") > 1 else row.element, []
        ).append(ident)
        relations += [
            f'<SPEC-RELATION IDENTIFIER="{_identifier(row.id + parent)}" LAST-CHANGE="{stamp}">'
            f"<TYPE><SPEC-RELATION-TYPE-REF>{t_rel}</SPEC-RELATION-TYPE-REF></TYPE>"
            f"<SOURCE><SPEC-OBJECT-REF>{ident}</SPEC-OBJECT-REF></SOURCE>"
            f"<TARGET><SPEC-OBJECT-REF>{_identifier('hlr' + parent)}</SPEC-OBJECT-REF></TARGET>"
            "</SPEC-RELATION>"
            for parent in row.parents
        ]
    objects += [
        _object(_identifier("hlr" + p), t_hlr, [("STRING", a["hid"], p)], stamp) for p in parents
    ]
    children = ""
    for owner, idents in sorted(hierarchy.items()):
        head = _identifier("head" + owner)
        objects.append(_object(head, t_head, [("STRING", a["htext"], owner)], stamp))
        inner = "".join(
            f'<SPEC-HIERARCHY IDENTIFIER="{_identifier("h" + i)}" LAST-CHANGE="{stamp}">'
            f"<OBJECT><SPEC-OBJECT-REF>{i}</SPEC-OBJECT-REF></OBJECT></SPEC-HIERARCHY>"
            for i in idents
        )
        children += (
            f'<SPEC-HIERARCHY IDENTIFIER="{_identifier("h" + head)}" LAST-CHANGE="{stamp}">'
            f"<OBJECT><SPEC-OBJECT-REF>{head}</SPEC-OBJECT-REF></OBJECT><CHILDREN>{inner}</CHILDREN>"
            "</SPEC-HIERARCHY>"
        )
    llr_attrs = "".join(
        _attribute_type(a[name], label, kind, stamp)
        for name, label, kind in (
            ("id", FOREIGN_ID, "STRING"),
            ("text", TEXT, "STRING"),
            ("kind", "WF.Kind", "STRING"),
            ("element", "WF.Element", "STRING"),
            ("source", "WF.Source", "STRING"),
            ("derived", "WF.Derived", "BOOLEAN"),
        )
    )
    title = f"WireForge LLR {project.name} {project.version or ''}".strip()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<REQ-IF xmlns="{NAMESPACE}"><THE-HEADER>'
        f'<REQ-IF-HEADER IDENTIFIER="{_identifier("header")}">'
        f"<COMMENT>{escape(title)}</COMMENT><CREATION-TIME>{stamp}</CREATION-TIME>"
        f"<REPOSITORY-ID>{escape(project.name)}</REPOSITORY-ID>"
        f"<REQ-IF-TOOL-ID>WireForge</REQ-IF-TOOL-ID><REQ-IF-VERSION>1.0</REQ-IF-VERSION>"
        f"<SOURCE-TOOL-ID>WireForge</SOURCE-TOOL-ID><TITLE>{escape(title)}</TITLE>"
        "</REQ-IF-HEADER></THE-HEADER><CORE-CONTENT><REQ-IF-CONTENT><DATATYPES>"
        f'<DATATYPE-DEFINITION-STRING IDENTIFIER="{_identifier("dt-STRING")}" '
        f'LAST-CHANGE="{stamp}" '
        'LONG-NAME="String" MAX-LENGTH="32000"/>'
        f'<DATATYPE-DEFINITION-BOOLEAN IDENTIFIER="{_identifier("dt-BOOLEAN")}" '
        f'LAST-CHANGE="{stamp}" LONG-NAME="Boolean"/></DATATYPES><SPEC-TYPES>'
        f'<SPEC-OBJECT-TYPE IDENTIFIER="{t_llr}" LAST-CHANGE="{stamp}" LONG-NAME="WF_LLR">'
        f"<SPEC-ATTRIBUTES>{llr_attrs}</SPEC-ATTRIBUTES></SPEC-OBJECT-TYPE>"
        f'<SPEC-OBJECT-TYPE IDENTIFIER="{t_head}" LAST-CHANGE="{stamp}" LONG-NAME="WF_HEADING">'
        f"<SPEC-ATTRIBUTES>{_attribute_type(a['htext'], TEXT, 'STRING', stamp)}</SPEC-ATTRIBUTES>"
        "</SPEC-OBJECT-TYPE>"
        f'<SPEC-OBJECT-TYPE IDENTIFIER="{t_hlr}" LAST-CHANGE="{stamp}" LONG-NAME="WF_HLR_REF">'
        f"<SPEC-ATTRIBUTES>{_attribute_type(a['hid'], FOREIGN_ID, 'STRING', stamp)}"
        "</SPEC-ATTRIBUTES>"
        "</SPEC-OBJECT-TYPE>"
        f'<SPEC-RELATION-TYPE IDENTIFIER="{t_rel}" LAST-CHANGE="{stamp}" LONG-NAME="WF_REFINES"/>'
        f'<SPECIFICATION-TYPE IDENTIFIER="{t_spec}" LAST-CHANGE="{stamp}" LONG-NAME="WF_SPEC"/>'
        f"</SPEC-TYPES><SPEC-OBJECTS>{''.join(objects)}</SPEC-OBJECTS>"
        f"<SPEC-RELATIONS>{''.join(relations)}</SPEC-RELATIONS><SPECIFICATIONS>"
        f'<SPECIFICATION IDENTIFIER="{_identifier("specification")}" LAST-CHANGE="{stamp}" '
        f"LONG-NAME={quoteattr(title)}><TYPE><SPECIFICATION-TYPE-REF>{t_spec}"
        f"</SPECIFICATION-TYPE-REF></TYPE><CHILDREN>{children}</CHILDREN></SPECIFICATION>"
        "</SPECIFICATIONS></REQ-IF-CONTENT></CORE-CONTENT></REQ-IF>\n"
    )


def write_llrs(path: Path, ctx: DocsContext, directory: Path) -> None:
    """Write the low-level requirements as ReqIF (`.reqifz` zips the `.reqif` file)."""
    text = llr_document(ctx, commit_time(directory))
    if path.suffix.lower() == ".reqifz":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo(ARCHIVE_MEMBER, (1980, 1, 1, 0, 0, 0))
            archive.writestr(info, text)
        path.write_bytes(buffer.getvalue())
    else:
        path.write_text(text, encoding="utf-8", newline="\n")
