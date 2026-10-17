from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wireforge import cli, service, test_project

if TYPE_CHECKING:
    from collections.abc import Callable

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"
BASIC = EXAMPLES / "01_basic_telemetry"
PLUGINS = AVIONICS / "plugins" / "vendor_sum.py"


@pytest.fixture
def avionics(tmp_path: Path) -> Path:
    copy = tmp_path / "avionics"
    shutil.copytree(AVIONICS, copy)
    return copy


def _git(directory: Path, *args: str) -> str:
    git = shutil.which("git") or "git"
    result = subprocess.run(
        [git, "-C", str(directory), *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def _changed_lines(diff: str) -> list[str]:
    return [
        line
        for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]


def test_hand_gui_hand_edits_give_minimal_diffs(avionics: Path) -> None:
    _git(avionics, "init", "-q")
    _git(avionics, "add", "-A")
    _git(avionics, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    nav = avionics / "messages" / "nav.yaml"
    # 1. hand edit: a comment and a changed limit
    text = nav.read_text(encoding="utf-8")
    text = text.replace("max: 20000", "max: 25000  # raised for the new radar")
    nav.write_text(text, encoding="utf-8")
    # 2. tool edit (what the GUI does through the service layer)
    edited = service.set_value(avionics, "/messages/NavSolution/fields/3/max", 85.0)
    assert edited.ok, edited.text
    added = service.set_value(
        avionics, "/types/TrackRef/fields/-", {"name": "age", "type": "u8", "units": "s"}
    )
    assert added.ok, added.text
    # 3. hand edit again
    common = avionics / "types" / "common.yaml"
    common.write_text(common.read_text(encoding="utf-8") + "# end of types\n", encoding="utf-8")
    changed = _changed_lines(_git(avionics, "diff", "-U0"))
    assert changed == [
        "-        max: 20000",
        "+        max: 25000  # raised for the new radar",
        "-      - { name: lat, type: f64, min: -90.0, max: 90.0, default: 0.0, units: deg }",
        "+      - { name: lat, type: f64, min: -90.0, max: 85.0, default: 0.0, units: deg }",
        "+      - { name: age, type: u8, units: s }",
        "+# end of types",
    ]
    assert service.format_project(avionics, check=True).ok, "tool output is canonical"


def test_service_edits(avionics: Path) -> None:
    got = service.get(avionics, "/messages/Command/fields/0")
    assert got.data["value"]["max"] == 4
    assert got.data["file"].endswith("status.yaml")
    dry = service.set_value(
        avionics, "/messages/Command/fields/0/max", 6, service.EditOptions(dry_run=True)
    )
    assert "+      - { name: target, type: u8, min: 1, max: 6, default: 1 }" in dry.data["diff"]
    assert service.get(avionics, "/messages/Command/fields/0/max").data["value"] == 4
    created = service.upsert(
        avionics,
        "types",
        "Pair",
        {"fields": [{"name": "a", "type": "u8"}, {"name": "b", "type": "u8"}]},
    )
    assert created.ok
    assert created.data["file"].endswith("common.yaml"), "goes to the file holding types"
    updated = service.upsert(
        avionics,
        "types",
        "Pair",
        {"description": "Two bytes.", "fields": [{"name": "b", "type": "u16"}]},
    )
    assert updated.ok
    assert service.get(avionics, "/types/Pair").data["value"] == {
        "description": "Two bytes.",
        "fields": [{"name": "b", "type": "u16"}],
    }
    removed = service.remove(avionics, "/types/Pair")
    assert removed.ok
    with pytest.raises(service.ServiceError, match="not found"):
        service.get(avionics, "/types/Pair")
    with pytest.raises(service.ServiceError, match="not found"):
        service.remove(avionics, "/types/Pair")
    with pytest.raises(service.ServiceError, match="JSON pointer"):
        service.get(avionics, "types")
    with pytest.raises(service.ServiceError, match="unknown section"):
        service.upsert(avionics, "nope", "X", {})


def test_strict_edits_roll_back(avionics: Path) -> None:
    status = avionics / "messages" / "status.yaml"
    before = status.read_text(encoding="utf-8")
    bad = service.set_value(
        avionics,
        "/messages/Command/fields/0/default",
        99,
        service.EditOptions(strict=True),
    )
    assert not bad.ok
    assert any("SR-6" in issue.message for issue in bad.issues)
    assert status.read_text(encoding="utf-8") == before
    loose = service.set_value(avionics, "/messages/Command/fields/0/default", 99)
    assert loose.ok, "written; the issues are reported"
    assert loose.issues


def _run(argv: list[str]) -> tuple[int, dict[str, object]]:
    outcome = cli.run(argv)
    result = outcome["result"]
    assert isinstance(result, dict)
    return int(outcome["exit_code"]), result


def test_cli_commands(avionics: Path, tmp_path: Path) -> None:
    project = str(avionics)
    assert _run(["validate", project])[0] == cli.EXIT_OK
    assert _run(["fmt", project, "--check"])[0] == cli.EXIT_OK
    assert _run(["migrate", project, "--check"])[0] in {cli.EXIT_OK, cli.EXIT_FAILED}
    assert _run(["migrate", project])[0] == cli.EXIT_OK
    assert _run(["migrate", project, "--check"])[0] == cli.EXIT_OK
    code, result = _run(["analyze", project, "--profile", "platformA", "--format", "csv"])
    assert code == cli.EXIT_OK
    assert str(result["text"]).startswith("profile,owner,path")
    code, result = _run(
        ["generate", project, "-o", str(tmp_path / "out"), "--tests", "--plugins", str(PLUGINS)]
    )
    assert code == cli.EXIT_OK
    assert (tmp_path / "out" / "avionics_demo" / "generated_tests" / "CMakeLists.txt").exists()
    code, result = _run(["compat", str(AVIONICS), project])
    assert code == cli.EXIT_OK
    assert isinstance(result["data"], dict)
    code, result = _run(["get", project, "/messages/Command/id"])
    assert result["data"] == {"file": str(avionics / "messages" / "status.yaml"), "value": 769}
    assert _run(["set", project, "/messages/Command/description", "Command to nodeC."])[0] == 0
    assert _run(["upsert", project, "types", "Byte", "{ fields: [{ name: v, type: u8 }] }"])[0] == 0
    assert _run(["remove", project, "/types/Byte", "--dry-run"])[0] == cli.EXIT_OK
    assert _run(["remove", project, "/types/Byte"])[0] == cli.EXIT_OK


def test_cli_codec(avionics: Path) -> None:
    common = ["--interface", "our_to_nodeC", "--profile", "platformA", "--plugins", str(PLUGINS)]
    code, result = _run(["encode", str(avionics), "Command", "{ target: 2, opcode: 7 }", *common])
    assert code == cli.EXIT_OK
    data = result["data"]
    assert isinstance(data, dict)
    frame = str(data["frame"])
    code, result = _run(["decode", str(avionics), frame, *common])
    assert code == cli.EXIT_OK
    decoded = result["data"]
    assert isinstance(decoded, dict)
    assert decoded["value"] == {"target": 2, "opcode": 7, "sum": 9}
    code, result = _run(
        ["decode", str(avionics), str(data["payload"]), "--message", "Command", *common]
    )
    assert code == cli.EXIT_OK
    code, _ = _run(["decode", str(avionics), frame[:-2] + "00", *common])
    assert code == cli.EXIT_FAILED
    code, _ = _run(["decode", str(avionics), "zz", *common])
    assert code == cli.EXIT_ERROR
    code, _ = _run(["encode", str(avionics), "Command", "{ target: 9 }", *common])
    assert code == cli.EXIT_FAILED


def test_cli_bundles_and_errors(avionics: Path, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.yaml"
    assert _run(["export", str(avionics), "-o", str(bundle)])[0] == cli.EXIT_OK
    assert _run(["import", str(bundle), str(tmp_path / "unpacked")])[0] == cli.EXIT_OK
    assert _run(["import", str(bundle), str(tmp_path / "unpacked")])[0] == cli.EXIT_FAILED
    assert _run(["validate", str(tmp_path / "missing")])[0] == cli.EXIT_ERROR
    assert cli.main(["validate"]) == cli.EXIT_USAGE
    assert cli.main(["--help"]) == cli.EXIT_OK


def test_cli_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["validate", str(BASIC)]) == cli.EXIT_OK
    assert capsys.readouterr().out.endswith(": OK\n")


def test_run_tests_drives_cmake(
    avionics: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        del cwd
        commands.append(command)
        failed = command[-1] == "coverage"
        return subprocess.CompletedProcess(command, 1 if failed else 0, "out\n", "")

    monkeypatch.setattr(service, "_run", fake)
    tests = test_project.TestOptions(plugins=PLUGINS)
    ok = service.run_tests(avionics, tmp_path / "out", tests)
    assert ok.ok
    assert [command[1] for command in commands] == ["-S", "--build", "--test-dir"]
    coverage = service.run_tests(
        avionics, tmp_path / "out", tests, service.BuildOptions(coverage=True, generator="Ninja")
    )
    assert not coverage.ok
    assert "-DWIREFORGE_COVERAGE=ON" in commands[3]
    assert "Ninja" in commands[3]

    def no_cmake(name: str) -> None:
        del name

    monkeypatch.setattr(shutil, "which", no_cmake)
    with pytest.raises(service.ServiceError, match="cmake"):
        service.run_tests(avionics, tmp_path / "out")
    assert json.loads(json.dumps(ok.to_dict()))["ok"]


def test_cli_docs(avionics: Path, tmp_path: Path) -> None:
    output = tmp_path / "docs"
    assert _run(["docs", str(avionics), "-o", str(output)])[0] == cli.EXIT_OK
    assert (output / "index.md").exists()
    assert _run(["docs", str(avionics), "-o", str(output), "--strict"])[0] == cli.EXIT_OK
    edge = EXAMPLES / "03_edge_cases"
    code, result = _run(["docs", str(edge), "-o", str(tmp_path / "edge"), "--strict"])
    assert code == cli.EXIT_FAILED
    assert "[DS-1]" in str(result["text"])
    assert _run(["docs", str(edge), "-o", str(tmp_path / "edge")])[0] == cli.EXIT_OK


def test_cli_docs_with_results(
    avionics: Path, tmp_path: Path, make_results: Callable[..., Path]
) -> None:
    build = make_results(tmp_path / "run")
    output = tmp_path / "docs"
    code, result = _run(["docs", str(avionics), "-o", str(output), "--results", str(build)])
    assert code == cli.EXIT_OK
    assert "Runtime headers" in (output / "test_report.md").read_text(encoding="utf-8")
    code, result = _run(
        ["docs", str(avionics), "-o", str(output), "--results", str(build), "--strict"]
    )
    assert code == cli.EXIT_FAILED
    assert "[DS-7]" in str(result["text"])
    missing = _run(["docs", str(avionics), "-o", str(output), "--results", str(tmp_path / "none")])
    assert missing[0] == cli.EXIT_ERROR


def test_cli_test_accepts_the_runtime_coverage_flag() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["test", "x", "-o", "y", "--coverage-runtime"])
    assert args.coverage_runtime
    assert not parser.parse_args(["test", "x", "-o", "y", "--coverage"]).coverage_runtime
