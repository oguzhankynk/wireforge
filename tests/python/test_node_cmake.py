import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from wireforge import resources

HERE = Path(__file__).parent
CONSUMER = HERE / "system_fixture_consumer"
pytestmark = pytest.mark.skipif(shutil.which("cmake") is None, reason="cmake is not installed")


def _env() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{environment['PATH']}"
    return environment


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, env=_env())


def _configure(source: Path, build: Path) -> subprocess.CompletedProcess[str]:
    package = f"-DWireForge_DIR={resources.cmake_dir()}"
    return _run(["cmake", "-S", str(source), "-B", str(build), package])


def test_units_of_the_fixture_build_and_run(tmp_path: Path) -> None:
    build = tmp_path / "build"
    configured = _configure(CONSUMER, build)
    assert configured.returncode == 0, configured.stdout + configured.stderr
    built = _run(["cmake", "--build", str(build), "--config", "Release"])
    assert built.returncode == 0, built.stdout + built.stderr
    tested = _run(["ctest", "--test-dir", str(build), "-C", "Release", "--output-on-failure"])
    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert (build / "wireforge_unit_a" / "nodes" / "A" / "fixture_A").is_dir()
    assert (build / "wireforge_unit_b" / "nodes" / "B" / "fixture_B").is_dir()


@pytest.mark.parametrize(
    ("project", "node", "message"),
    [
        ("system_fixture", "", "has nodes; give NODE"),
        ("../../examples/01_basic_telemetry", "NODE A", "NODE needs a project that defines nodes"),
    ],
)
def test_node_is_required_exactly_for_projects_with_nodes(
    tmp_path: Path, project: str, node: str, message: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.21)\n"
        "project(check LANGUAGES CXX)\n"
        "find_package(WireForge REQUIRED)\n"
        f'wireforge_generate(model PROJECT "{(HERE / project).resolve().as_posix()}" {node})\n',
        encoding="utf-8",
    )
    configured = _configure(source, tmp_path / "build")
    assert configured.returncode != 0
    assert message in configured.stdout + configured.stderr
