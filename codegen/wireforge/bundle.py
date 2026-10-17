"""Bundle export and import.

A bundle is one ordinary single-file project (no `include`) stamped with the current format plus
`bundle: {content_hash, source_files}`. The sha256 covers the definition content only: stamps, the
bundle block and key order do not change it.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from wireforge import schema, vectors, versioning
from wireforge.issues import Issue, errors_only
from wireforge.loader import SECTIONS, load_project
from wireforge.plain import to_plain
from wireforge.validate import Validator, validate_project

STAMP_KEYS = frozenset({"schema_version", "schema_minor", "generator", "bundle"})
ROOT_ONLY_KEYS = ("project", "version", "description", "namespace", "defaults", "user_includes")
# Standard layout written by import: section -> directory/file (interfaces get one file each).
LAYOUT = {
    "requirements": "requirements/requirements.yaml",
    "profiles": "profiles/profiles.yaml",
    "messages": "messages/messages.yaml",
    "vectors": "vectors/vectors.yaml",
}
TYPE_SECTIONS = ("enums", "types")
ROOT_SECTIONS = ("defines", "checksums")


class BundleError(Exception):
    """Export or import refused; `issues` explains why."""

    def __init__(self, message: str, issues: list[Issue] | None = None) -> None:
        """Keep the summary and the detailed issues."""
        super().__init__(message)
        self.issues = issues or []


def content_hash(document: dict[str, Any]) -> str:
    """sha256 over the definition content (stamps and key order excluded)."""
    content = {key: value for key, value in document.items() if key not in STAMP_KEYS}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 120
    return yaml


def _dump(document: dict[str, Any]) -> str:
    stream = io.StringIO()
    _yaml().dump(document, stream)
    return stream.getvalue()


def _stamped(content: dict[str, Any]) -> dict[str, Any]:
    stamp: dict[str, object] = {}
    versioning.stamp(stamp)
    return {**stamp, **content}


def build_bundle(location: Path) -> dict[str, Any]:
    """Merge a valid project into one bundle document; raises BundleError for invalid projects."""
    result = load_project(location)
    issues = list(result.issues)
    if result.project is not None:
        issues += Validator(result.project).run()
        if not errors_only(issues):
            issues += vectors.check(result.project)
    if result.project is None or errors_only(issues):
        message = f"{location}: export refused, the project has errors"
        raise BundleError(message, errors_only(issues))
    root_file = Path(result.project.source.file)
    root = cast("dict[str, Any]", to_plain(result.documents[str(root_file)]))
    content: dict[str, Any] = {key: root[key] for key in ROOT_ONLY_KEYS if key in root}
    for section in SECTIONS:
        merged: dict[str, Any] = {}
        for document in result.documents.values():
            merged.update(cast("dict[str, Any]", to_plain(document.get(section, {}))))
        if merged:
            content[section] = merged
    sources = [Path(file).relative_to(root_file.parent).as_posix() for file in result.documents]
    bundle = _stamped(content)
    bundle["bundle"] = {"content_hash": content_hash(content), "source_files": sources}
    return bundle


def export_bundle(location: Path, output: Path) -> None:
    """Write the bundle of `location` to `output`."""
    output.write_text(_dump(build_bundle(location)), encoding="utf-8")


def _load_bundle(path: Path) -> dict[str, Any]:
    try:
        loaded = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError) as error:
        message = f"{path}: cannot read bundle: {error}"
        raise BundleError(message) from error
    if not isinstance(loaded, dict):
        message = f"{path}: a bundle must be a mapping"
        raise BundleError(message)
    return loaded


def _verify(document: dict[str, Any], path: Path) -> None:
    info = document.get("bundle")
    expected = info.get("content_hash") if isinstance(info, dict) else None
    if expected is None:
        message = f"{path}: not a bundle (no bundle.content_hash); use --no-verify to import it"
        raise BundleError(message)
    actual = content_hash(document)
    if actual != expected:
        message = f"{path}: content hash mismatch (bundle says {expected}, content is {actual})"
        raise BundleError(message)


def _migrate(document: dict[str, Any], path: Path) -> None:
    try:
        versioning.migrate_document(document)
    except (TypeError, ValueError) as error:
        message = f"{path}: {error}"
        raise BundleError(message) from error


def _split(content: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Distribute a single-file project over the standard layout (relative path -> document)."""
    root = {key: content[key] for key in (*ROOT_ONLY_KEYS, *ROOT_SECTIONS) if key in content}
    files: dict[str, dict[str, Any]] = {}
    types = {section: content[section] for section in TYPE_SECTIONS if section in content}
    if types:
        files["types/types.yaml"] = types
    for section, relative in LAYOUT.items():
        if section in content:
            files[relative] = {section: content[section]}
    for name, interface in content.get("interfaces", {}).items():
        files[f"interfaces/{name}.yaml"] = {"interfaces": {name: interface}}
    directories = sorted({relative.split("/")[0] for relative in files})
    if directories:
        root["include"] = [f"{directory}/*.yaml" for directory in directories]
    result = {schema.ROOT_FILE_NAME: _stamped(root)}
    result.update({relative: _stamped(body) for relative, body in files.items()})
    return result


def import_bundle(
    bundle: Path, destination: Path, *, verify: bool = True, single_file: bool = False
) -> list[Issue]:
    """Unpack a bundle into `destination` and return the validation issues of the result."""
    if destination.exists() and any(destination.iterdir()):
        message = f"{destination}: destination is not empty"
        raise BundleError(message)
    document = _load_bundle(bundle)
    if verify:
        _verify(document, bundle)
    _migrate(document, bundle)
    content = {key: value for key, value in document.items() if key not in STAMP_KEYS}
    issues = schema.validate_document(_stamped(content), "project.schema.json", file=str(bundle))
    if errors_only(issues):
        message = f"{bundle}: the bundle is not a valid project"
        raise BundleError(message, issues)
    files = {schema.ROOT_FILE_NAME: _stamped(content)} if single_file else _split(content)
    for relative, body in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_dump(body), encoding="utf-8")
    return validate_project(destination)
