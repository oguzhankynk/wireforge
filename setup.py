"""Packaging hook: the wheel carries the JSON Schemas, the runtime headers and the built UI.

Everything else is configured in pyproject.toml. Editable installs read these files from the
repository; built wheels get copies below `wireforge/_data/`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
DATA = {
    "schema": ROOT / "schema",
    "runtime/include": ROOT / "runtime" / "include",
    "cmake": ROOT / "cmake" / "package",
    "ui": ROOT / "ui" / "dist",
}


FILES = {
    "runtime/requirements.yaml": ROOT / "runtime" / "requirements.yaml",
    "runtime/LICENSE": ROOT / "runtime" / "LICENSE",
}


class BuildWithData(build_py):
    """Copy the data directories into the built package."""

    def run(self) -> None:
        """Build the Python modules, then add the data files."""
        super().run()
        target = Path(self.build_lib) / "wireforge" / "_data"
        for name, source in DATA.items():
            if source.is_dir():
                shutil.copytree(source, target / name, dirs_exist_ok=True)
        for name, source in FILES.items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / name)


setup(cmdclass={"build_py": BuildWithData})
