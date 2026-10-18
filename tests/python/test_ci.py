from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from wireforge import ci_check, cli, test_project
from wireforge.service import BuildOptions

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"


def _git(directory: Path, *args: str) -> None:
    git = shutil.which("git") or "git"
    subprocess.run(
        [git, "-C", str(directory), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(AVIONICS, root / "avionics")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root / "avionics"


def _widen_a_range(project: Path) -> None:
    path = project / "messages" / "status.yaml"
    text = path.read_text(encoding="utf-8")
    assert "{ name: target, type: u8, min: 1, max: 4," in text
    path.write_text(text.replace("min: 1, max: 4,", "min: 0, max: 4,", 1), encoding="utf-8")


def test_an_unchanged_project_passes(repository: Path) -> None:
    result = ci_check.run(repository, ci_check.CiOptions(base="HEAD"))
    assert result.ok, result.text
    summary = str(result.data["summary"])
    assert "| Validation | passed |" in summary
    assert "| Wire compatibility | passed |" in summary
    assert "| Tests | skipped |" in summary


def test_a_range_change_needs_a_major_bump(repository: Path) -> None:
    _widen_a_range(repository)
    result = ci_check.run(repository, ci_check.CiOptions(base="HEAD"))
    assert not result.ok
    summary = str(result.data["summary"])
    assert "| Wire compatibility | failed |" in summary
    assert "| our_to_nodeC | 1.0.0 → 1.0.0 | major | none | failed |" in summary


def test_an_invalid_project_stops_the_check(repository: Path) -> None:
    (repository / "project.yaml").write_text("schema_version: 1\nproject: 9x\n", encoding="utf-8")
    result = ci_check.run(repository, ci_check.CiOptions(base="HEAD", test=True))
    assert not result.ok
    summary = str(result.data["summary"])
    assert "| Validation | failed |" in summary
    assert "| Tests | skipped |" in summary
    assert "Wire compatibility" not in summary


def test_a_missing_base_is_reported_not_fatal(repository: Path) -> None:
    result = ci_check.run(repository, ci_check.CiOptions(base="no-such-revision"))
    assert result.ok
    assert "Not compared with `no-such-revision`" in str(result.data["summary"])


def test_the_command_line_writes_the_summary(
    repository: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "summary.md"
    code = cli.main(["ci", str(repository), "--base", "HEAD", "--summary", str(out)])
    assert code == cli.EXIT_OK
    assert out.read_text(encoding="utf-8").startswith("# WireForge: avionics_demo")
    assert "# WireForge: avionics_demo" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("cmake") is None, reason="needs cmake to build the tests")
def test_the_tests_are_built_and_run(repository: Path, tmp_path: Path) -> None:
    options = ci_check.CiOptions(
        test=True,
        output=tmp_path / "out",
        build=BuildOptions(generator="Ninja" if shutil.which("ninja") else None),
        tests=test_project.TestOptions(plugins=AVIONICS / "plugins" / "vendor_sum.py"),
    )
    result = ci_check.run(repository, options)
    assert result.ok, result.text
    assert "100% tests passed" in str(result.data["summary"])


def test_the_action_and_the_gitlab_example_are_wellformed() -> None:
    root = Path(__file__).resolve().parents[2]
    action = YAML().load(root / "action.yml")
    assert action["runs"]["using"] == "composite"
    assert {"project", "base", "test", "plugins", "python-version", "wireforge-version"} <= set(
        action["inputs"]
    )
    script = "\n".join(str(step.get("run", "")) for step in action["runs"]["steps"])
    assert "wireforge ci" in script
    assert "GITHUB_STEP_SUMMARY" in script
    gitlab = YAML().load(root / "docs" / "integration" / "gitlab_ci.yml")
    assert "wireforge ci" in " ".join(str(line) for line in gitlab["wireforge"]["script"])
