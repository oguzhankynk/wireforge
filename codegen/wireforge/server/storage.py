"""Workspace storage of the server: project directories on disk, ETags and write locks.

The files are the single source of truth: every request reads them from disk, so edits made by
hand (editor, git checkout) are visible immediately. Every file has an ETag (sha256 of its bytes)
and every project an ETag over all its files; writes must name the ETag they were based on and
are refused when the file (or project) changed in between. Writes of one project are serialized
by a lock.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from wireforge import schema
from wireforge.loader import load_project

POLL_SECONDS = 0.2


class StorageError(Exception):
    """A request cannot be served: `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        """Store the HTTP status and the message."""
        super().__init__(message)
        self.status = status


def etag_of(data: bytes) -> str:
    """ETag of file contents."""
    return hashlib.sha256(data).hexdigest()[:32]


@dataclass(frozen=True)
class ProjectState:
    """ETags of a project: one per file and one over all files."""

    etag: str
    files: dict[str, str]


@dataclass
class Workspace:
    """A directory whose subdirectories with a project.yaml are projects."""

    root: Path
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def projects(self) -> list[str]:
        """Return the identifiers (directory names) of the projects."""
        return sorted(
            path.name
            for path in self.root.iterdir()
            if path.is_dir() and (path / schema.ROOT_FILE_NAME).exists()
        )

    def project_dir(self, project: str) -> Path:
        """Directory of a project; 404 for unknown identifiers."""
        if project not in self.projects():
            raise StorageError(404, f"unknown project {project!r}")
        return self.root / project

    def lock(self, project: str) -> threading.Lock:
        """Return the write lock of a project."""
        with self._guard:
            return self._locks.setdefault(project, threading.Lock())

    def files(self, project: str) -> list[Path]:
        """Root file and fragments of a project (the root alone when it does not load)."""
        directory = self.project_dir(project)
        result = load_project(directory)
        if result.documents:
            return [Path(name) for name in result.documents]
        return [directory / schema.ROOT_FILE_NAME]

    def relative(self, project: str, path: Path) -> str:
        """Path of a project file relative to the project directory (POSIX form)."""
        return path.resolve().relative_to(self.project_dir(project).resolve()).as_posix()

    def state(self, project: str) -> ProjectState:
        """Return the current ETags of every file of a project."""
        files = {
            self.relative(project, path): etag_of(path.read_bytes())
            for path in self.files(project)
            if path.exists()
        }
        combined = etag_of(
            "".join(f"{name}={tag};" for name, tag in sorted(files.items())).encode()
        )
        return ProjectState(combined, files)

    def file_path(self, project: str, relative: str) -> Path:
        """Return a YAML file inside a project directory; refuse paths that leave it."""
        directory = self.project_dir(project).resolve()
        path = (directory / relative).resolve()
        if directory not in path.parents or path.suffix not in {".yaml", ".yml"}:
            raise StorageError(400, f"{relative!r} is not a YAML file of the project")
        return path

    def read(self, project: str, relative: str) -> tuple[str, str]:
        """Text and ETag of a project file."""
        path = self.file_path(project, relative)
        if not path.exists():
            raise StorageError(404, f"no file {relative!r}")
        data = path.read_bytes()
        return data.decode("utf-8"), etag_of(data)

    def write(self, project: str, relative: str, text: str, if_match: str | None) -> str:
        """Replace a file when its ETag still equals `if_match` (`*` creates a new file)."""
        path = self.file_path(project, relative)
        with self.lock(project):
            current = etag_of(path.read_bytes()) if path.exists() else None
            check_match(current, if_match)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = text.encode("utf-8")
            path.write_bytes(data)
            return etag_of(data)

    def wait(self, project: str, etag: str, timeout: float) -> ProjectState | None:
        """Block until the project ETag differs from `etag` (None after `timeout` seconds)."""
        deadline = time.monotonic() + timeout
        while True:
            state = self.state(project)
            if state.etag != etag:
                return state
            if time.monotonic() >= deadline:
                return None
            time.sleep(POLL_SECONDS)


def check_match(current: str | None, if_match: str | None) -> None:
    """Optimistic locking: 428 without If-Match, 412 when the resource changed."""
    if if_match is None:
        raise StorageError(428, "If-Match is required for writes")
    expected = if_match.strip('"')
    if expected == "*":
        if current is not None:
            raise StorageError(412, "the file already exists")
        return
    if current != expected:
        raise StorageError(412, "the resource was changed by someone else; reload and retry")
