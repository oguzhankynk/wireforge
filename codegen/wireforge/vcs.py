"""What git says about a project directory: commit, repository root and local changes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 20


def _git(directory: Path, *arguments: str) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        done = subprocess.run(
            [git, *arguments],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def commit(directory: Path) -> str | None:
    """Return the commit the working copy is at (None outside a repository)."""
    return _git(directory, "rev-parse", "HEAD")


def root(directory: Path) -> Path | None:
    """Return the top directory of the repository."""
    top = _git(directory, "rev-parse", "--show-toplevel")
    return Path(top).resolve() if top else None


def dirty(directory: Path) -> bool:
    """Return whether files below `directory` differ from the commit (untracked ones count)."""
    changes = _git(directory, "status", "--porcelain", "--", ".")
    return bool(changes)
