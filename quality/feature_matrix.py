"""The matrix of schema features that the built and measured models have to cover.

A feature of the schema (a field type, an occurrence form, a framing kind, a checksum ...) that no
model uses is a feature whose generated code no test of the repository has ever run. The models
that are built and measured at 100 percent coverage are the examples, the protocols,
`tests/models/*` and the random models of the CI range (`quality/random_models.py`); this script
lists the features each of them uses and fails when a required feature is in none of them, or when
a property of the schema is neither mapped to a feature nor declared as documentation only.

Usage: python3 quality/feature_matrix.py   (needs the Python package)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from random_models import RANDOM_RANGE, write_model
from wireforge.loader import load_project

if TYPE_CHECKING:
    from wireforge.model import Field, Interface, Project

ROOT = Path(__file__).resolve().parents[1]
INTEGER_TYPES = ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64")
BUILTIN_TYPES = (*INTEGER_TYPES, "bool", "f32", "f64", "char")
BYTE = 8
HALF_WORD = 16

# Everything the generated code has to handle, by short name.
REQUIRED = (
    *(f"type:{name}" for name in BUILTIN_TYPES),
    "type:enum",
    "type:struct",
    "type:user_type",
    "bits",
    "bits:signed",
    "bits:bool",
    "bits:across_bytes",
    "reserved",
    "const",
    "range",
    "range:expression",
    "default",
    "default:array",
    "scale_offset",
    "condition:field",
    "condition:message",
    "condition:interface_message",
    "occurs:fixed",
    "occurs:counted",
    "occurs:optional",
    "occurs:fill",
    "occurs:expression",
    "occurs:struct",
    "struct:nested",
    "struct:bit_group",
    "message:id_override",
    "message:multiple",
    "interface:multiple",
    "interface:rx",
    "interface:tx",
    "interface:both",
    "header:none",
    "header:fields",
    "role:sync",
    "role:message_id",
    "role:payload_length",
    "role:protocol_version",
    "role:sequence",
    "trailer",
    "framing:none",
    "framing:fixed",
    "framing:sync_length",
    "framing:length_prefixed",
    "framing:resync",
    "framing:length_counts",
    "interface:allow_trailing_bytes",
    "interface:trim_trailing_zeros",
    "interface:endian",
    "interface:bit_order",
    "interface:buffers",
    "checksum:crc32",
    "checksum:crc16",
    "checksum:parametric",
    "checksum:custom",
    "checksum:custom_c",
    "checksum:scope_payload",
    "checksum:scope_header_and_payload",
    "checksum:range",
    "project:endian_big",
    "project:endian_little",
    "project:bit_order_lsb",
    "project:bit_order_msb",
    "project:range_check_encode",
    "project:range_check_decode",
    "project:defines",
    "project:profiles",
    "project:user_constants",
    "project:vectors",
    "project:requirements",
)

# Every property of the schema is a feature above or documentation only.
SCHEMA_PROPERTIES: dict[str, dict[str, str | None]] = {
    "field": {
        "bits": "bits",
        "checksum": "checksum:crc32",
        "const": "const",
        "default": "default",
        "deprecated": None,
        "description": None,
        "if": "condition:field",
        "max": "range",
        "min": "range",
        "name": None,
        "occurs": "occurs:fixed",
        "reqs": "project:requirements",
        "reserved": "reserved",
        "role": "role:sync",
        "since": None,
        "type": "type:u8",
        "units": None,
        "scale": "scale_offset",
        "offset": "scale_offset",
    },
    "interfaceDef": {
        "allow_trailing_bytes": "interface:allow_trailing_bytes",
        "trim_trailing_zeros": "interface:trim_trailing_zeros",
        "bit_order": "interface:bit_order",
        "buffers": "interface:buffers",
        "description": None,
        "direction": "interface:both",
        "endian": "interface:endian",
        "framing": "framing:none",
        "header": "header:fields",
        "messages": "message:multiple",
        "range_check": "project:range_check_encode",
        "reqs": "project:requirements",
        "trailer": "trailer",
        "version": None,
    },
    "framing": {
        "frame_size": "framing:fixed",
        "kind": "framing:none",
        "length_counts": "framing:length_counts",
        "max_frame_size": "framing:sync_length",
        "reqs": "project:requirements",
        "resync": "framing:resync",
    },
    "messageDef": {
        "bit_order": "project:bit_order_lsb",
        "deprecated": None,
        "description": None,
        "endian": "project:endian_little",
        "fields": "type:u8",
        "id": "message:id_override",
        "if": "condition:message",
        "range_check": "project:range_check_encode",
        "reqs": "project:requirements",
        "since": None,
    },
    "checksumDef": {
        "algo": "checksum:crc32",
        "check": "checksum:parametric",
        "description": None,
        "impl": "checksum:custom",
        "c_impl": "checksum:custom_c",
        "c_include": "checksum:custom_c",
        "include": "checksum:custom",
        "init": "checksum:parametric",
        "poly": "checksum:parametric",
        "refin": "checksum:parametric",
        "refout": "checksum:parametric",
        "reqs": "project:requirements",
        "size_bytes": "checksum:custom",
        "width": "checksum:parametric",
        "xorout": "checksum:parametric",
    },
}


def _type_features(project: Project, item: Field) -> set[str]:
    if item.type in BUILTIN_TYPES:
        return {f"type:{item.type}"}
    for table, name in (
        (project.enums, "type:enum"),
        (project.user_types, "type:user_type"),
        (project.types, "type:struct"),
    ):
        if item.type in table:
            return {name}
    return set()


def _occurs_features(project: Project, item: Field) -> set[str]:
    occurs = item.occurs
    if occurs is None:
        return set()
    if occurs.fill:
        found = {"occurs:fill"}
    elif occurs.count is not None:
        found = {"occurs:optional" if occurs.max == 1 else "occurs:counted"}
    else:
        found = {"occurs:fixed"}
    if isinstance(occurs.max, str):
        found.add("occurs:expression")
    if item.type in project.types:
        found.add("occurs:struct")
    return found


def _field_features(project: Project, item: Field) -> set[str]:
    flags = {
        "reserved": item.reserved,
        "const": item.const is not None,
        "range": item.min is not None or item.max is not None,
        "range:expression": isinstance(item.min, str) or isinstance(item.max, str),
        "default": item.default is not None,
        "scale_offset": item.scale is not None or item.offset is not None,
        "default:array": isinstance(item.default, (list, str)) and item.occurs is not None,
        "condition:field": item.condition is not None,
        "bits": item.bits is not None,
        "bits:signed": item.bits is not None and item.type.startswith("i"),
        "bits:bool": item.bits is not None and item.type == "bool",
        "bits:across_bytes": item.bits is not None and item.bits > BYTE,
    }
    found = {name for name, present in flags.items() if present}
    found |= _type_features(project, item) | _occurs_features(project, item)
    if item.checksum is not None:
        found.add(
            f"checksum:scope_{item.checksum.scope}" if item.checksum.scope else "checksum:range"
        )
    return found


def _interface_features(project: Project, interface: Interface) -> set[str]:
    found = {f"interface:{interface.direction}", f"framing:{interface.framing.kind}"}
    found.add("header:none" if interface.header is None else "header:fields")
    flags = {
        "trailer": bool(interface.trailer),
        "framing:resync": interface.framing.resync,
        "framing:length_counts": interface.framing.length_counts != "payload",
        "interface:allow_trailing_bytes": interface.allow_trailing_bytes,
        "interface:trim_trailing_zeros": interface.trim_trailing_zeros,
        "interface:endian": interface.endian is not None,
        "interface:bit_order": interface.bit_order is not None,
        "interface:buffers": bool(interface.buffers),
        "message:id_override": any(entry.id is not None for entry in interface.messages),
        "condition:interface_message": any(e.condition is not None for e in interface.messages),
    }
    found |= {name for name, present in flags.items() if present}
    for item in (*(interface.header or ()), *interface.trailer):
        found |= _field_features(project, item)
        if item.role:
            found.add(f"role:{item.role}")
    return found


def _checksum_features(project: Project) -> set[str]:
    found: set[str] = set()
    for item in project.checksums.values():
        if item.algo == "crc32":
            found.add("checksum:crc32")
        elif item.algo == "custom":
            found.add("checksum:custom")
            if item.c_impl:
                found.add("checksum:custom_c")
        else:
            found.add("checksum:parametric")
            if item.width == HALF_WORD:
                found.add("checksum:crc16")
    return found


def _struct_features(project: Project) -> set[str]:
    found: set[str] = set()
    for struct in [*project.messages.values(), *project.types.values()]:
        for item in struct.fields:
            found |= _field_features(project, item)
        if struct.condition is not None:
            found.add("condition:message")
    for struct in project.types.values():
        if struct.fields and all(item.bits is not None for item in struct.fields):
            found.add("struct:bit_group")
        if any(item.type in project.types for item in struct.fields):
            found.add("struct:nested")
    return found


def _project_flags(project: Project) -> set[str]:
    defaults = project.defaults
    check = defaults.get("range_check")
    found = {
        "project:endian_little" if defaults.get("endian") == "little" else "project:endian_big",
        "project:bit_order_lsb"
        if defaults.get("bit_order") == "lsb_first"
        else "project:bit_order_msb",
    }
    if isinstance(check, dict):
        found |= {f"project:range_check_{key}" for key, value in check.items() if value}
    tables = {
        "project:defines": project.defines,
        "project:profiles": project.profiles,
        "project:user_constants": project.constants,
        "project:vectors": project.vectors,
        "project:requirements": project.requirements,
        "message:multiple": project.messages if len(project.messages) > 1 else None,
        "interface:multiple": project.interfaces if len(project.interfaces) > 1 else None,
    }
    return found | {name for name, table in tables.items() if table}


def project_features(project: Project) -> set[str]:
    """Return the features a loaded project uses."""
    found = _checksum_features(project) | _struct_features(project) | _project_flags(project)
    for interface in project.interfaces.values():
        found |= _interface_features(project, interface)
    return found


def model_directories() -> list[Path]:
    """Return the example and test model directories."""
    return sorted(
        path.parent
        for pattern in (
            "examples/*/project.yaml",
            "protocols/*/project.yaml",
            "tests/models/*/project.yaml",
            "codegen/wireforge/templates/*/project.yaml",
        )
        for path in ROOT.glob(pattern)
    )


def coverage() -> dict[str, set[str]]:
    """Return, for every model, the features it uses."""
    result: dict[str, set[str]] = {}
    for directory in model_directories():
        loaded = load_project(directory)
        if loaded.project is not None:
            result[str(directory.relative_to(ROOT))] = project_features(loaded.project)
    with tempfile.TemporaryDirectory() as work:
        for seed in range(RANDOM_RANGE[0], RANDOM_RANGE[1] + 1):
            loaded = load_project(write_model(seed, Path(work) / str(seed)).parent)
            if loaded.project is not None:
                result[f"random model {seed}"] = project_features(loaded.project)
    return result


def check_schema(schema: dict[str, Any]) -> list[str]:
    """Return the schema properties that are not mapped to a feature (or documentation)."""
    problems = []
    for name, mapping in SCHEMA_PROPERTIES.items():
        properties = set(schema["$defs"][name]["properties"])
        problems += [
            f"{name}.{key}: not in the feature matrix" for key in sorted(properties - set(mapping))
        ]
        problems += [
            f"{name}.{key}: no longer in the schema" for key in sorted(set(mapping) - properties)
        ]
        problems += [
            f"{name}.{key}: maps to the unknown feature {feature}"
            for key, feature in sorted(mapping.items())
            if feature is not None and feature not in REQUIRED
        ]
    return problems


def main() -> int:
    """Print the uncovered features and the unmapped schema properties."""
    seen = coverage()
    covered = set().union(*seen.values())
    missing = [name for name in REQUIRED if name not in covered]
    schema = json.loads((ROOT / "schema" / "project.schema.json").read_text(encoding="utf-8"))
    problems = check_schema(schema)
    for name in missing:
        sys.stdout.write(f"feature not covered by any model: {name}\n")
    for problem in problems:
        sys.stdout.write(f"{problem}\n")
    sys.stdout.write(
        f"feature matrix: {len(REQUIRED) - len(missing)} of {len(REQUIRED)} features covered "
        f"by {len(seen)} models, {len(problems)} schema problem(s)\n"
    )
    return 1 if missing or problems else 0


if __name__ == "__main__":
    sys.exit(main())
