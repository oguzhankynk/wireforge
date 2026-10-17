"""Locations of the files WireForge ships besides its Python code.

In a repository checkout (editable install) they live next to `codegen/`; an installed wheel has
copies below `wireforge/_data/`.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DATA = Path(__file__).resolve().parent / "_data"
REPOSITORY = Path(__file__).resolve().parents[2]


def _locate(packaged: str, repository: str) -> Path:
    path = PACKAGE_DATA / packaged
    return path if path.exists() else REPOSITORY / repository


def schema_dir() -> Path:
    """Return the directory of the JSON Schemas of project files."""
    return _locate("schema", "schema")


def runtime_include() -> Path:
    """Return the include directory of the runtime headers (contains `wireforge/`)."""
    return _locate("runtime/include", "runtime/include")


def runtime_requirements() -> Path:
    """Return the file of the runtime requirements."""
    return _locate("runtime/requirements.yaml", "runtime/requirements.yaml")


def cmake_dir() -> Path:
    """Return the directory of the CMake package files (WireForgeConfig.cmake)."""
    return _locate("cmake", "cmake/package")


def ui_dir() -> Path | None:
    """Return the built web UI, or None when it was not built."""
    path = _locate("ui", "ui/dist")
    return path if (path / "index.html").exists() else None
