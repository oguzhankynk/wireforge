"""JSON Schema validation of WireForge project files.

This module only checks structure; references, ranges and layout are checked by
`wireforge.validate`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from wireforge import resources, versioning
from wireforge.issues import Issue, errors_only

SCHEMA_DIR = resources.schema_dir()
SCHEMA_FILES = ("project.schema.json", "fragment.schema.json")
ROOT_FILE_NAME = "project.yaml"


def _registry() -> Registry:
    resources = []
    for name in SCHEMA_FILES:
        contents = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
        resources.append(
            (name, Resource.from_contents(contents, default_specification=DRAFT202012))
        )
    return Registry().with_resources(resources)


def _validator(schema_file: str) -> Draft202012Validator:
    contents = json.loads((SCHEMA_DIR / schema_file).read_text(encoding="utf-8"))
    return Draft202012Validator(contents, registry=_registry())


def _line_of(document: object, path: list[str | int]) -> int | None:
    """Best-effort 1-based line of the node addressed by `path` in a round-trip loaded document."""
    line: int | None = None
    current: Any = document
    for part in path:
        try:
            if isinstance(current, CommentedMap) and isinstance(part, str):
                line = current.lc.key(part)[0]
            elif isinstance(current, CommentedSeq) and isinstance(part, int):
                line = current.lc.item(part)[0]
            else:
                break
            current = current[part]
        except (KeyError, IndexError, AttributeError):
            break
    return None if line is None else line + 1


def _pointer(path: list[str | int]) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in path)


def _pick(error: ValidationError) -> ValidationError | None:
    """Most helpful sub-error of an anyOf/oneOf failure, ignoring wrong-branch type mismatches."""
    if not error.context:
        return None
    relevant = [
        sub for sub in error.context if not (sub.validator == "type" and not sub.relative_path)
    ]
    picked: ValidationError | None = best_match(relevant or error.context)
    return picked


def validate_document(
    document: object, schema_file: str, *, file: str = "<memory>", source: object | None = None
) -> list[Issue]:
    """Validate an already loaded document; `source` (round-trip loaded) enables line numbers."""
    validator = _validator(schema_file)
    issues: list[Issue] = []
    errors: list[ValidationError] = sorted(
        validator.iter_errors(document), key=lambda error: [str(p) for p in error.absolute_path]
    )
    for error in errors:
        chosen = _pick(error)
        reported = chosen or error
        path = list(error.absolute_path) + (list(reported.relative_path) if chosen else [])
        line = _line_of(source if source is not None else document, path)
        issues.append(Issue(file, line, _pointer(path), reported.message))
    return issues


def load_yaml(path: Path) -> object:
    """Load a YAML 1.2 file preserving line information."""
    return YAML(typ="rt").load(path.read_text(encoding="utf-8"))


def validate_file(path: Path, schema_file: str) -> list[Issue]:
    """Validate one YAML file against the named schema."""
    try:
        document = load_yaml(path)
    except YAMLError as error:
        return [Issue(str(path), None, "", f"YAML syntax error: {error}")]
    if not isinstance(document, dict):
        return [Issue(str(path), 1, "", "top level must be a mapping")]
    version_issues = versioning.check_document(document, file=str(path), line=1)
    if errors_only(version_issues):
        return version_issues
    return version_issues + validate_document(
        document, schema_file, file=str(path), source=document
    )


def validate_project(location: Path) -> list[Issue]:
    """Validate a project root file and every fragment it includes."""
    root = location / ROOT_FILE_NAME if location.is_dir() else location
    issues = validate_file(root, "project.schema.json")
    document = None
    try:
        document = load_yaml(root)
    except YAMLError:
        return issues
    patterns = document.get("include", []) if isinstance(document, dict) else []
    for pattern in patterns if isinstance(patterns, list) else []:
        for fragment in sorted(root.parent.glob(str(pattern))):
            issues.extend(validate_file(fragment, "fragment.schema.json"))
    return issues


def main(argv: list[str] | None = None) -> int:
    """Validate project directories or root files and print one line per issue."""
    parser = argparse.ArgumentParser(prog="python -m wireforge.schema")
    parser.add_argument("projects", nargs="+", type=Path)
    args = parser.parse_args(argv)
    total = 0
    for project in args.projects:
        found = validate_project(project)
        total += len(found)
        for issue in found:
            sys.stdout.write(f"{issue}\n")
        if not found:
            sys.stdout.write(f"{project}: OK\n")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
