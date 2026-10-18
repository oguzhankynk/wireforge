"""Canonical formatter for project files: stable key order, comments and styles kept.

`format_text()` reorders the keys of every known mapping (project root, fields, messages,
interfaces, framing, ...) into the order of the JSON Schema, normalizes indentation (mappings
2 spaces, sequences 4 with the dash at 2) and writes flow mappings as `{ key: value }`. Everything
else is kept as written: comments, blank lines, quoting, flow or block style of each node, the
order of user-named entries (types, messages, enum values, defines). The result is idempotent,
so hand edits and tool edits (CLI, server, GUI) produce minimal diffs.

Comments travel with the key they precede when keys are reordered; a comment above the first key
of a mapping stays at the top of that mapping.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.emitter import Emitter
from ruamel.yaml.error import CommentMark
from ruamel.yaml.events import MappingEndEvent
from ruamel.yaml.tokens import CommentToken
from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# Index of the comment that follows a mapping key in ruamel's comment slots.
TRAILING_SLOT = 2
ROOT = (
    "schema_version",
    "schema_minor",
    "generator",
    "project",
    "version",
    "description",
    "namespace",
    "defaults",
    "include",
    "requirements_source",
    "requirements",
    "defines",
    "user_includes",
    "profiles",
    "checksums",
    "enums",
    "types",
    "user_types",
    "messages",
    "interfaces",
    "vectors",
    "bundle",
)
FIELD = (
    "name",
    "type",
    "bits",
    "min",
    "max",
    "default",
    "const",
    "reserved",
    "units",
    "scale",
    "offset",
    "occurs",
    "checksum",
    "role",
    "if",
    "since",
    "deprecated",
    "description",
    "reqs",
)
STRUCT = ("description", "reqs", "if", "since", "deprecated", "fields")
MESSAGE = (
    "id",
    "description",
    "reqs",
    "endian",
    "bit_order",
    "range_check",
    "if",
    "since",
    "deprecated",
    "fields",
)
INTERFACE = (
    "version",
    "description",
    "reqs",
    "direction",
    "endian",
    "bit_order",
    "range_check",
    "header",
    "trailer",
    "framing",
    "buffers",
    "allow_trailing_bytes",
    "messages",
)
ORDERS: dict[str, tuple[str, ...]] = {
    "root": ROOT,
    "defaults": ("endian", "bit_order", "range_check"),
    "range_check": ("encode", "decode"),
    "profile": ("description", "defines"),
    "checksum": (
        "algo",
        "description",
        "reqs",
        "width",
        "poly",
        "init",
        "refin",
        "refout",
        "xorout",
        "check",
        "impl",
        "include",
        "c_impl",
        "c_include",
        "size_bytes",
    ),
    "enum": ("type", "description", "reqs", "values"),
    "struct": STRUCT,
    "message": MESSAGE,
    "field": FIELD,
    "occurs": ("min", "max", "count", "fill"),
    "checksum_use": ("use", "covers"),
    "covers": ("scope", "from", "to"),
    "interface": INTERFACE,
    "part": ("fields",),
    "framing": ("kind", "frame_size", "max_frame_size", "length_counts", "resync", "reqs"),
    "buffers": ("rx_bytes", "tx_bytes"),
    "interface_message": ("name", "id", "if", "reqs"),
    "generator": ("name", "version"),
    "bundle": ("content_hash", "source_files"),
    "user_includes": ("include_dirs", "headers", "constants", "prelude"),
    "header": ("path", "style", "if"),
    "constant": ("cpp", "c", "description", "value", "profile_values"),
    "user_type": ("cpp", "description", "reqs", "size_bytes", "include", "style"),
    "requirement": ("text", "source", "verification"),
    "vector": (
        "description",
        "interface",
        "message",
        "profile",
        "direction",
        "bytes",
        "expect",
        "reqs",
    ),
    "expect": ("fields", "status"),
}
# Mapping kind of the value under a key, for kinds reached by key from another kind.
CHILDREN: dict[tuple[str, str], str] = {
    ("root", "defaults"): "defaults",
    ("defaults", "range_check"): "range_check",
    ("message", "range_check"): "range_check",
    ("interface", "range_check"): "range_check",
    ("root", "generator"): "generator",
    ("root", "bundle"): "bundle",
    ("root", "user_includes"): "user_includes",
    ("field", "occurs"): "occurs",
    ("field", "checksum"): "checksum_use",
    ("checksum_use", "covers"): "covers",
    ("interface", "header"): "part",
    ("interface", "trailer"): "part",
    ("interface", "framing"): "framing",
    ("interface", "buffers"): "buffers",
    ("vector", "expect"): "expect",
}
# Kind of the entries of a section map (user-named keys).
SECTIONS = {
    "requirements": "requirement",
    "profiles": "profile",
    "checksums": "checksum",
    "enums": "enum",
    "types": "struct",
    "user_types": "user_type",
    "messages": "message",
    "interfaces": "interface",
    "vectors": "vector",
}
# Kind of the elements of a sequence under a key.
ELEMENTS = {
    ("struct", "fields"): "field",
    ("message", "fields"): "field",
    ("part", "fields"): "field",
    ("interface", "messages"): "interface_message",
    ("user_includes", "headers"): "header",
}


class SpacedEmitter(Emitter):
    """Emitter that writes non-empty flow mappings as `{ key: value }`."""

    @override
    def expect_flow_mapping(
        self, single: bool | None = False, force_flow_indent: bool | None = False
    ) -> None:
        """Open a flow mapping; a space follows the brace unless the mapping is empty."""
        empty = bool(self.events) and isinstance(self.events[0], MappingEndEvent)
        self.flow_map_start = "{" if empty else "{ "
        self.flow_map_end = "}" if empty else " }"
        super().expect_flow_mapping(single, force_flow_indent)
        if self.flow_context and self.flow_context[-1] == "{ ":
            self.flow_context[-1] = "{"

    @override
    def expect_flow_mapping_key(self) -> None:
        """Close with ` }` (non-empty mappings) or write the next key."""
        self.flow_map_end = " }"
        super().expect_flow_mapping_key()


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    yaml.Emitter = SpacedEmitter
    return yaml


def load(text: str) -> object:
    """Load YAML text keeping comments, quotes and styles."""
    return _yaml().load(text)


def dump(document: object) -> str:
    """Write a document loaded with `load()` in the canonical layout."""
    out = io.StringIO()
    _yaml().dump(document, out)
    return out.getvalue()


# --- key order ----------------------------------------------------------------------------------


def _token(text: str) -> CommentToken:
    return CommentToken(text, CommentMark(0), None)


def _split(token: CommentToken | None) -> tuple[str, str]:
    """Split a key's trailing comment into its end-of-line part and the lines after it."""
    if token is None:
        return "", ""
    text = str(token.value)
    head, newline, rest = text.partition("\n")
    return head + newline, rest


def _trailing(mapping: CommentedMap, key: object) -> CommentToken | None:
    entry = mapping.ca.items.get(key)
    if not entry or len(entry) <= TRAILING_SLOT:
        return None
    token = entry[TRAILING_SLOT]
    return token if isinstance(token, CommentToken) else None


def _set_trailing(mapping: CommentedMap, key: object, text: str) -> None:
    entry = mapping.ca.items.setdefault(key, [None, None, None, None])
    entry[TRAILING_SLOT] = _token(text) if text else None


def reorder(mapping: CommentedMap, order: Sequence[str]) -> None:
    """Sort the keys of `mapping` by `order` (unknown keys after, in their order)."""
    keys = list(mapping.keys())
    rank = {name: index for index, name in enumerate(order)}
    wanted = sorted(keys, key=lambda key: (rank.get(str(key), len(order)), keys.index(key)))
    if wanted == keys:
        return
    eol: dict[object, str] = {}
    before: dict[object, str] = {}
    previous: object = None
    after_previous = ""
    for key in keys:
        line, rest = _split(_trailing(mapping, key))
        eol[key] = line
        if previous is not None:
            before[key] = after_previous
        after_previous = rest
        previous = key
    tail = after_previous
    values = [(key, mapping[key]) for key in wanted]
    for key in keys:
        del mapping[key]
    for index, (key, value) in enumerate(values):
        mapping[key] = value
        following = before.get(wanted[index + 1], "") if index + 1 < len(wanted) else tail
        if index == 0 and key in before:
            following = before[key] + following  # a comment above a key that moved to the top
        line = eol[key] or ("\n" if following else "")  # following lines need a line end
        _set_trailing(mapping, key, line + following)


def _visit(node: object, kind: str | None) -> None:
    if isinstance(node, CommentedSeq):
        return
    if not isinstance(node, CommentedMap):
        return
    if kind in ORDERS:
        reorder(node, ORDERS[kind])
    for key, value in node.items():
        name = str(key)
        if kind == "root" and name in SECTIONS:
            _visit_section(value, SECTIONS[name])
            continue
        child = CHILDREN.get((kind or "", name))
        element = ELEMENTS.get((kind or "", name))
        if element is not None and isinstance(value, CommentedSeq):
            for item in value:
                _visit_element(item, element)
        elif child is not None:
            _visit(value, child)
        elif kind == "user_includes" and name == "constants" and isinstance(value, CommentedMap):
            _visit_section(value, "constant")


def _visit_section(node: object, kind: str) -> None:
    if isinstance(node, CommentedMap):
        for value in node.values():
            _visit(value, kind)


def _visit_element(node: object, kind: str) -> None:
    _visit(node, kind)


def canonicalize(document: object) -> None:
    """Reorder the keys of a loaded project or fragment document in place."""
    _visit(document, "root")


def format_text(text: str) -> str:
    """Return the canonical form of one project or fragment file."""
    document = load(text)
    canonicalize(document)
    return dump(document)


def format_file(path: Path, *, check: bool = False) -> bool:
    """Format `path` in place (or with `check` only compare); return whether it changes."""
    text = path.read_text(encoding="utf-8")
    formatted = format_text(text)
    if formatted == text:
        return False
    if not check:
        path.write_text(formatted, encoding="utf-8")
    return True
