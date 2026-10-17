from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from wireforge import cli, service

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"


def _git(directory: Path, *arguments: str) -> None:
    git = shutil.which("git") or "git"
    subprocess.run(
        [git, "-c", "user.email=t@t", "-c", "user.name=t", *arguments],
        cwd=directory,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("needs git")
    target = tmp_path / "basic"
    shutil.copytree(EXAMPLES / "01_basic_telemetry", target)
    _git(target, "init", "-q")
    _git(target, "add", "-A")
    _git(target, "commit", "-qm", "base")
    return target


def _impact(repository: Path, tmp_path: Path) -> tuple[str, str]:
    output = tmp_path / "docs"
    options = service.DocsOptions(since="HEAD")
    result = service.lifecycle_docs(repository, output, options=options)
    assert result.ok, result.text
    assert "impact.md" in result.data["files"]
    return (
        (output / "impact.md").read_text(encoding="utf-8"),
        (output / "impact.csv").read_text(encoding="utf-8"),
    )


def test_no_change_means_no_impact(repository: Path, tmp_path: Path) -> None:
    text, table = _impact(repository, tmp_path)
    assert "## Tests to run and review again\n\nnone" in text
    assert table.splitlines() == ["area,change,id,detail"]


def test_a_changed_limit_reaches_its_requirement_and_tests(
    repository: Path, tmp_path: Path
) -> None:
    project = repository / "project.yaml"
    project.write_text(
        project.read_text(encoding="utf-8").replace("max: 150.0", "max: 180.0"), encoding="utf-8"
    )
    text, table = _impact(repository, tmp_path)
    assert "text changed" in text
    assert "LLR-SensorReading.temperature.RANGE" in table
    rerun = text.split("## Tests to run and review again")[1]
    assert "SensorReading.temperature_max" in rerun
    # The pattern value depends on the limits, so the bytes in the tests of the message change too;
    # a test that uses neither the pattern nor the limit is not listed.
    assert "Interface_sensor_to_us.EncodeFrameNeedsRoom" not in rerun
    assert "model.hpp" in text, "the generated header changed"
    assert "SensorReading" in text.split("## Generated code")[1].split("## Generated tests")[0]


def test_a_new_message_is_added_everywhere(repository: Path, tmp_path: Path) -> None:
    project = repository / "project.yaml"
    text = project.read_text(encoding="utf-8")
    text = text.replace(
        "interfaces:\n",
        "  Ping:\n    id: 0x0102\n    fields:\n      - { name: seq, type: u8 }\n\ninterfaces:\n",
        1,
    ).replace("messages: [SensorReading]", "messages: [SensorReading, Ping]")
    project.write_text(text, encoding="utf-8")
    report, table = _impact(repository, tmp_path)
    assert "llr,added,LLR-Ping.LAYOUT" in table
    assert "test,added" in table
    assert "compatible" in report or "minor" in report.lower()


def test_hlr_changes_are_reported(repository: Path, tmp_path: Path) -> None:
    project = repository / "project.yaml"
    project.write_text(
        project.read_text(encoding="utf-8").replace(
            "calibrated range", "calibrated and safe range"
        ),
        encoding="utf-8",
    )
    _, table = _impact(repository, tmp_path)
    assert "hlr,text changed,HLR-SNS-004" in table


def test_an_unknown_revision_is_an_error(repository: Path, tmp_path: Path) -> None:
    options = service.DocsOptions(since="no-such-revision")
    with pytest.raises(service.ServiceError, match="cannot read"):
        service.lifecycle_docs(repository, tmp_path / "docs", options=options)


def test_the_command_line_option(repository: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    code = cli.main(["docs", str(repository), "-o", str(output), "--since", "HEAD"])
    assert code == cli.EXIT_OK
    assert (output / "impact.md").exists()
