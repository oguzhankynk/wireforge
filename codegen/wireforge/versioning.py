"""File-format identity, compatibility policy and migrations.

Policy (rule SR-19):
- major newer than this tool supports -> error (upgrade WireForge)
- major older -> error unless imported through `migrate_document`
- same major, minor newer than supported -> error
- generator newer than this tool -> warning only
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING

from wireforge import __version__
from wireforge.issues import Issue

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, MutableMapping

FORMAT_MAJOR = 1
FORMAT_MINOR = 2
GENERATOR_NAME = "wireforge"
TOOL_VERSION = __version__

_MIGRATIONS: dict[int, Callable[[MutableMapping[str, object]], None]] = {}
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def register_migration(
    from_major: int, step: Callable[[MutableMapping[str, object]], None]
) -> None:
    """Register the step upgrading a document from `from_major` to `from_major + 1` in place."""
    _MIGRATIONS[from_major] = step


@contextmanager
def temporary_migration(
    from_major: int, step: Callable[[MutableMapping[str, object]], None]
) -> Iterator[None]:
    """Register a migration for the duration of a `with` block (tests and tooling)."""
    previous = _MIGRATIONS.get(from_major)
    _MIGRATIONS[from_major] = step
    try:
        yield
    finally:
        if previous is None:
            _MIGRATIONS.pop(from_major, None)
        else:
            _MIGRATIONS[from_major] = previous


def migration_path(from_major: int) -> list[int] | None:
    """Majors whose steps must run to reach FORMAT_MAJOR, or None when a step is missing."""
    path = list(range(from_major, FORMAT_MAJOR))
    return path if all(major in _MIGRATIONS for major in path) else None


def _semver_tuple(text: object) -> tuple[int, int, int] | None:
    match = _SEMVER.match(text) if isinstance(text, str) else None
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


def check_document(
    document: MutableMapping[str, object], *, file: str, line: int | None = None
) -> list[Issue]:
    """Apply the compatibility policy to the version stamp of one document."""
    version = document.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        message = "[SR-19] missing or invalid schema_version: the file does not say its format"
        return [Issue(file, line, "/schema_version", message)]
    if version > FORMAT_MAJOR:
        message = (
            f"[SR-19] file uses format version {version} but this WireForge {TOOL_VERSION} "
            f"supports up to {FORMAT_MAJOR}; upgrade WireForge"
        )
        return [Issue(file, line, "/schema_version", message)]
    if version < FORMAT_MAJOR:
        hint = (
            "run `wireforge import` to migrate it"
            if migration_path(version) is not None
            else "no migration path is available"
        )
        message = f"[SR-19] file uses older format version {version}; {hint}"
        return [Issue(file, line, "/schema_version", message)]
    issues: list[Issue] = []
    minor = document.get("schema_minor", 0)
    if isinstance(minor, int) and minor > FORMAT_MINOR:
        message = (
            f"[SR-19] file needs format {version}.{minor} but this WireForge supports "
            f"{FORMAT_MAJOR}.{FORMAT_MINOR}"
        )
        issues.append(Issue(file, line, "/schema_minor", message))
    generator = document.get("generator")
    generator_version = generator.get("version") if isinstance(generator, dict) else None
    written = _semver_tuple(generator_version)
    current = _semver_tuple(TOOL_VERSION)
    if written is not None and current is not None and written[:2] > current[:2]:
        message = (
            f"file was written by WireForge {generator_version}, newer than {TOOL_VERSION}; "
            "unknown features may be rejected"
        )
        issues.append(Issue(file, line, "/generator", message, severity="warning"))
    return issues


def migrate_document(document: MutableMapping[str, object]) -> list[str]:
    """Upgrade `document` in place to the current format; returns the applied steps.

    Raises TypeError without an integer schema_version, ValueError when the file is newer than
    supported or a migration step is missing.
    """
    version = document.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        message = "document has no integer schema_version"
        raise TypeError(message)
    if version > FORMAT_MAJOR:
        message = f"format {version} is newer than supported {FORMAT_MAJOR}"
        raise ValueError(message)
    path = migration_path(version)
    if path is None:
        message = f"no migration path from format {version} to {FORMAT_MAJOR}"
        raise ValueError(message)
    applied = []
    for major in path:
        _MIGRATIONS[major](document)
        applied.append(f"format {major} -> {major + 1}")
    stamp(document)
    return applied


def stamp(document: MutableMapping[str, object]) -> None:
    """Write the current format and generator stamp into `document`."""
    document["schema_version"] = FORMAT_MAJOR
    document["schema_minor"] = FORMAT_MINOR
    document["generator"] = {"name": GENERATOR_NAME, "version": TOOL_VERSION}
