"""Project loader: root file + fragments -> `wireforge.model.Project`.

Structural problems (YAML, JSON Schema, version stamps) stop the load; semantic rules are checked
afterwards by `wireforge.validate`. Duplicate names across files are reported here, because
merging is where they become visible. The `requirements` catalog is a section like the others and
`reqs` lists are read into the elements that carry them; unknown ids are left to the validator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from wireforge import llr_ids, schema, versioning
from wireforge.issues import Issue, errors_only
from wireforge.model import (
    Checksum,
    ChecksumUse,
    Define,
    Enum,
    Field,
    Framing,
    Interface,
    InterfaceMessage,
    MessageInfo,
    Occurs,
    Profile,
    Project,
    Requirement,
    Source,
    Struct,
    UserConstant,
    UserHeader,
    UserType,
    Vector,
)
from wireforge.plain import to_plain

if TYPE_CHECKING:
    from pathlib import Path

SECTIONS = (
    "requirements",
    "defines",
    "profiles",
    "checksums",
    "enums",
    "types",
    "user_types",
    "messages",
    "interfaces",
    "vectors",
)


@dataclass
class LoadResult:
    """Outcome of `load_project`: the model (None after structural errors) and all issues."""

    project: Project | None
    issues: list[Issue]
    documents: dict[str, CommentedMap] = field(default_factory=dict)


def _mapping(node: object) -> dict[str, Any]:
    """Plain copy of a YAML mapping (the JSON Schema already checked its shape)."""
    return cast("dict[str, Any]", to_plain(node))


def _escape(part: str | int) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


@dataclass(frozen=True)
class _Where:
    """A YAML node plus its file and JSON pointer, used to derive child sources."""

    file: str
    node: Any
    pointer: str

    def child(self, key: str | int) -> _Where:
        return _Where(self.file, self.node[key], f"{self.pointer}/{_escape(key)}")

    def source_of(self, key: str | int) -> Source:
        line: int | None = None
        try:
            if isinstance(self.node, CommentedMap):
                line = self.node.lc.key(key)[0] + 1
            elif isinstance(self.node, CommentedSeq) and isinstance(key, int):
                line = self.node.lc.item(key)[0] + 1
        except (KeyError, IndexError, TypeError):
            line = None
        return Source(self.file, line, f"{self.pointer}/{_escape(key)}")

    def items(self) -> list[tuple[str, _Where, Source]]:
        return [(str(key), self.child(key), self.source_of(key)) for key in self.node]


def _occurs(raw: dict[str, Any] | None) -> Occurs | None:
    if raw is None:
        return None
    return Occurs(
        max=raw["max"], min=raw.get("min"), count=raw.get("count"), fill=bool(raw.get("fill"))
    )


def _checksum_use(raw: dict[str, Any] | None) -> ChecksumUse | None:
    if raw is None:
        return None
    covers = raw["covers"]
    return ChecksumUse(
        use=raw["use"], scope=covers.get("scope"), first=covers.get("from"), last=covers.get("to")
    )


def _reqs(raw: dict[str, Any]) -> tuple[str, ...]:
    return tuple(raw.get("reqs", ()))


def _field(raw: dict[str, Any], source: Source) -> Field:
    return Field(
        name=raw["name"],
        type=raw["type"],
        source=source,
        bits=raw.get("bits"),
        min=raw.get("min"),
        max=raw.get("max"),
        default=raw.get("default"),
        const=raw.get("const"),
        reserved=bool(raw.get("reserved", False)),
        occurs=_occurs(raw.get("occurs")),
        checksum=_checksum_use(raw.get("checksum")),
        role=raw.get("role"),
        condition=raw.get("if"),
        description=raw.get("description"),
        units=raw.get("units"),
        scale=raw.get("scale"),
        offset=raw.get("offset"),
        reqs=_reqs(raw),
    )


def _fields(where: _Where) -> tuple[Field, ...]:
    fields = where.child("fields")
    return tuple(
        _field(_mapping(fields.node[index]), fields.source_of(index))
        for index in range(len(fields.node))
    )


def _struct(name: str, where: _Where, source: Source, *, message: bool) -> Struct:
    raw = _mapping(where.node)
    info = None
    if message:
        info = MessageInfo(
            id=raw.get("id"),
            endian=raw.get("endian"),
            bit_order=raw.get("bit_order"),
            range_check=raw.get("range_check"),
        )
    return Struct(
        name,
        _fields(where),
        source,
        condition=raw.get("if"),
        message=info,
        description=raw.get("description"),
        reqs=_reqs(raw),
    )


def _checksum(name: str, where: _Where, source: Source) -> Checksum:
    raw = _mapping(where.node)
    return Checksum(
        name=name,
        algo=raw["algo"],
        source=source,
        width=raw.get("width"),
        poly=raw.get("poly"),
        init=raw.get("init", 0),
        refin=raw.get("refin", False),
        refout=raw.get("refout", False),
        xorout=raw.get("xorout", 0),
        check=raw.get("check"),
        impl=raw.get("impl"),
        include=raw.get("include"),
        size_bytes=raw.get("size_bytes"),
        reqs=_reqs(raw),
    )


def _user_type(name: str, where: _Where, source: Source) -> UserType:
    raw = _mapping(where.node)
    return UserType(
        name=name,
        cpp=raw["cpp"],
        size_bytes=raw["size_bytes"],
        source=source,
        include=raw.get("include"),
        style=raw.get("style", "quote"),
        description=raw.get("description"),
        reqs=_reqs(raw),
    )


def _interface_messages(where: _Where) -> tuple[InterfaceMessage, ...]:
    entries = where.child("messages")
    result = []
    for index in range(len(entries.node)):
        source = entries.source_of(index)
        if isinstance(entries.node[index], str):
            result.append(InterfaceMessage(str(entries.node[index]), source))
        else:
            raw = _mapping(entries.node[index])
            result.append(
                InterfaceMessage(
                    raw["name"],
                    source,
                    id=raw.get("id"),
                    condition=raw.get("if"),
                    reqs=_reqs(raw),
                )
            )
    return tuple(result)


def _interface(name: str, where: _Where, source: Source) -> Interface:
    raw = _mapping(where.node)
    header_raw = raw.get("header", "none")
    header = None if header_raw == "none" else _fields(where.child("header"))
    trailer = _fields(where.child("trailer")) if "trailer" in raw else ()
    framing_raw = raw.get("framing", {"kind": "none"})
    framing = Framing(
        kind=framing_raw["kind"],
        frame_size=framing_raw.get("frame_size"),
        max_frame_size=framing_raw.get("max_frame_size"),
        length_counts=framing_raw.get("length_counts", "payload"),
        resync=bool(framing_raw.get("resync", False)),
        reqs=_reqs(framing_raw),
    )
    return Interface(
        name=name,
        version=raw["version"],
        direction=raw["direction"],
        source=source,
        messages=_interface_messages(where),
        header=header,
        trailer=trailer,
        framing=framing,
        buffers=raw.get("buffers", {}),
        endian=raw.get("endian"),
        bit_order=raw.get("bit_order"),
        allow_trailing_bytes=bool(raw.get("allow_trailing_bytes", False)),
        description=raw.get("description"),
        reqs=_reqs(raw),
    )


def _requirement(name: str, raw: dict[str, Any], source: Source) -> Requirement:
    return Requirement(
        name,
        raw["text"],
        source,
        reference=raw.get("source"),
        verification=raw.get("verification", "test"),
    )


def _vector(name: str, raw: dict[str, Any], source: Source) -> Vector:
    text = raw["bytes"]
    try:
        data: bytes | None = bytes.fromhex(re.sub(r"[\s_]", "", text))
    except ValueError:
        data = None
    expect = raw["expect"]
    return Vector(
        name=name,
        message=raw["message"],
        text=text,
        data=data,
        source=source,
        interface=raw.get("interface"),
        profile=raw.get("profile"),
        fields=expect.get("fields"),
        status=expect.get("status"),
        direction=raw.get("direction", "decode"),
        reqs=_reqs(raw),
        description=raw.get("description"),
    )


def _add_entry(project: Project, section: str, name: str, where: _Where, source: Source) -> None:
    if section == "requirements":
        project.requirements[name] = _requirement(name, _mapping(where.node), source)
    elif section == "defines":
        project.defines[name] = Define(name, int(where.node), source)
    elif section == "profiles":
        project.profiles[name] = Profile(name, _mapping(where.node)["defines"], source)
    elif section == "checksums":
        project.checksums[name] = _checksum(name, where, source)
    elif section == "enums":
        raw = _mapping(where.node)
        project.enums[name] = Enum(name, raw["type"], raw["values"], source, reqs=_reqs(raw))
    elif section == "types":
        project.types[name] = _struct(name, where, source, message=False)
    elif section == "user_types":
        project.user_types[name] = _user_type(name, where, source)
    elif section == "messages":
        project.messages[name] = _struct(name, where, source, message=True)
    elif section == "vectors":
        project.vectors[name] = _vector(name, _mapping(where.node), source)
    else:
        project.interfaces[name] = _interface(name, where, source)


def _user_includes(project: Project, root: _Where) -> None:
    if "user_includes" not in root.node:
        return
    includes = root.child("user_includes")
    raw = _mapping(includes.node)
    for index, directory in enumerate(raw.get("include_dirs", [])):
        project.include_dirs.append((directory, includes.child("include_dirs").source_of(index)))
    for index, header in enumerate(raw.get("headers", [])):
        source = includes.child("headers").source_of(index)
        style = header.get("style", "quote")
        project.headers.append(UserHeader(header["path"], source, style, header.get("if")))
    if "constants" in raw:
        for name, _, source in includes.child("constants").items():
            constant = raw["constants"][name]
            project.constants[name] = UserConstant(
                name,
                constant["cpp"],
                source,
                value=constant.get("value"),
                profile_values=constant.get("profile_values", {}),
            )


def _merge(project: Project, document: _Where, seen: dict[tuple[str, str], Source]) -> list[Issue]:
    issues = []
    for section in SECTIONS:
        if section not in document.node:
            continue
        for name, where, source in document.child(section).items():
            previous = seen.get((section, name))
            if previous is not None:
                where_first = f"{previous.file}:{previous.line}"
                message = f"{section} entry {name!r} is already defined at {where_first}"
                issues.append(source.issue("SR-1", message))
                continue
            seen[(section, name)] = source
            _add_entry(project, section, name, where, source)
    return issues


def _load_checked(path: Path, schema_file: str) -> tuple[CommentedMap | None, list[Issue]]:
    try:
        document = schema.load_yaml(path)
    except (YAMLError, OSError) as error:
        return None, [Issue(str(path), None, "", f"cannot load YAML: {error}")]
    if not isinstance(document, CommentedMap):
        return None, [Issue(str(path), 1, "", "top level must be a mapping")]
    issues = versioning.check_document(document, file=str(path), line=1)
    if not errors_only(issues):
        issues += schema.validate_document(document, schema_file, file=str(path), source=document)
    return (None if errors_only(issues) else document), issues


def _fragment_paths(root: Path, document: CommentedMap) -> list[Path]:
    found: list[Path] = []
    for pattern in document.get("include", []):
        for path in sorted(root.parent.glob(str(pattern))):
            if path.resolve() != root.resolve() and path not in found:
                found.append(path)
    return found


def _numbers(project: Project, directory: Path, issues: list[Issue]) -> None:
    """Read the optional lock file with the numbers of the low-level requirements."""
    try:
        project.llr_ids = llr_ids.load(directory)
    except llr_ids.LockError as error:
        issues.append(Issue(str(directory / llr_ids.LOCK_FILE), None, "", str(error)))


def load_project(location: Path) -> LoadResult:
    """Load a project directory (or its root file), merging every included fragment."""
    root = location / schema.ROOT_FILE_NAME if location.is_dir() else location
    document, issues = _load_checked(root, "project.schema.json")
    if document is None:
        return LoadResult(None, issues)
    documents = {str(root): document}
    for path in _fragment_paths(root, document):
        fragment, fragment_issues = _load_checked(path, "fragment.schema.json")
        issues += fragment_issues
        if fragment is not None:
            documents[str(path)] = fragment
    if errors_only(issues):
        return LoadResult(None, issues, documents)
    top = _mapping(document)
    project = Project(
        name=top["project"],
        source=Source(str(root), 1, ""),
        version=top.get("version"),
        namespace=top.get("namespace"),
        defaults=top.get("defaults", {}),
        files=list(documents),
    )
    seen: dict[tuple[str, str], Source] = {}
    for file, loaded in documents.items():
        issues += _merge(project, _Where(file, loaded, ""), seen)
    _user_includes(project, _Where(str(root), document, ""))
    _numbers(project, root.parent, issues)
    for loaded in documents.values():
        if "requirements_source" in loaded:
            project.requirements_source = {
                str(key): str(value) for key, value in loaded["requirements_source"].items()
            }
    return LoadResult(project, issues, documents)
