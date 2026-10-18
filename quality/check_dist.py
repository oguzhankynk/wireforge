"""The distribution of WireForge: reproducible, complete and installable.

Builds the wheel and the source distribution twice with `SOURCE_DATE_EPOCH` set to the time of the
last commit and fails when

- the two wheels differ (the hash must be the same) or the two source distributions differ in
  their files,
- a file the tool needs at run time is not in the wheel (schemas, runtime headers, runtime
  requirements, templates, CMake package, licenses, `py.typed`),
- a clean virtual environment cannot install the wheel and, separately, the source distribution
  and run `wireforge version`, `init`, `validate` and `generate` from them.

Usage: python3 quality/check_dist.py   (needs `build` and network access for the build backend)
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from wireforge import __version__

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "wireforge/py.typed",
    "wireforge/wireshark_runtime.lua",
    "wireforge/templates/telemetry/project.yaml",
    "wireforge/templates/command/project.yaml",
    "wireforge/templates/raw/project.yaml",
    "wireforge/_data/schema/project.schema.json",
    "wireforge/_data/protocols/modbus_rtu/project.yaml",
    "wireforge/_data/protocols/modbus_tcp/project.yaml",
    "wireforge/_data/protocols/ubx/project.yaml",
    "wireforge/_data/runtime/include/wireforge/span.hpp",
    "wireforge/_data/runtime/requirements.yaml",
    "wireforge/_data/runtime/LICENSE",
    "wireforge/_data/cmake/WireForgeConfig.cmake",
)


def _run(command: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True, env=env, cwd=cwd)
    if result.returncode != 0:
        sys.stdout.write(f"$ {' '.join(command)}\n{result.stdout}{result.stderr}\n")
        message = f"failed: {' '.join(command)}"
        raise SystemExit(message)
    return result.stdout


def _epoch() -> str:
    return _run(["git", "log", "-1", "--format=%ct"], cwd=ROOT).strip()


def _build(target: Path, epoch: str) -> tuple[Path, Path]:
    env = {**os.environ, "SOURCE_DATE_EPOCH": epoch}
    _run([sys.executable, "-m", "build", "-o", str(target), str(ROOT)], env)
    return next(target.glob("*.whl")), next(target.glob("*.tar.gz"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sdist_files(path: Path) -> dict[str, str]:
    """Return name -> hash of the files of a source distribution (the archive bytes may differ)."""
    files: dict[str, str] = {}
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            stream = tar.extractfile(member)
            if stream is not None:
                files[member.name] = hashlib.sha256(stream.read()).hexdigest()
    return files


def _problems(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    problems = [f"missing in the wheel: {name}" for name in REQUIRED if name not in names]
    problems += [
        f"missing in the wheel: the license file {license_file}"
        for license_file in ("LICENSE", "runtime/LICENSE")
        if not any(name.endswith(f"/licenses/{license_file}") for name in names)
    ]
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        if "License-Expression: BUSL-1.1 AND MIT" not in archive.read(metadata).decode():
            problems.append("the metadata does not declare both licenses (BUSL-1.1 AND MIT)")
    if (
        ROOT / "ui" / "dist" / "index.html"
    ).exists() and "wireforge/_data/ui/index.html" not in names:
        problems.append("missing in the wheel: the built UI")
    return problems


def _install_and_run(artifact: Path, work: Path, version: str) -> None:
    venv = work / f"venv-{artifact.suffix.lstrip('.')}"
    _run([sys.executable, "-m", "venv", str(venv)])
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    _run([str(python), "-m", "pip", "install", "--quiet", str(artifact)])
    cli = [str(python), "-m", "wireforge.cli"]
    shown = _run([*cli, "version"]).strip()
    if version not in shown:
        message = f"{artifact.name}: `wireforge version` says '{shown}', expected {version}"
        raise SystemExit(message)
    project = work / f"project-{artifact.suffix.lstrip('.')}"
    _run([*cli, "init", str(project)])
    _run([*cli, "validate", str(project)])
    _run([*cli, "generate", str(project), "-o", str(work / f"gen-{project.name}"), "--tests"])


def main() -> int:
    """Build twice, compare, check the contents and install both artifacts."""
    epoch = _epoch()
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        wheel, sdist = _build(work / "first", epoch)
        second_wheel, second_sdist = _build(work / "second", epoch)
        problems = _problems(wheel)
        if _digest(wheel) != _digest(second_wheel):
            problems.append("two builds give different wheels")
        if _sdist_files(sdist) != _sdist_files(second_sdist):
            problems.append("two builds give source distributions with different files")
        for problem in problems:
            sys.stdout.write(f"{problem}\n")
        if problems:
            return 1
        for artifact in (wheel, sdist):
            _install_and_run(artifact, work, __version__)
        sys.stdout.write(
            f"distribution: {wheel.name} {_digest(wheel)[:12]} and {sdist.name} are reproducible, "
            "complete and installable\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
