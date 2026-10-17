import copy
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from wireforge import service, validate, vectors

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
EXAMPLES = sorted(path.parent for path in (ROOT / "examples").glob("*/project.yaml"))
BASE = YAML(typ="safe").load((HERE / "semantic_base.yaml").read_text(encoding="utf-8"))
CASES = YAML(typ="safe").load((HERE / "semantic_cases.yaml").read_text(encoding="utf-8"))
RULES = [f"SR-{number}" for number in range(1, 23)]


def _apply(document: dict[str, Any], operation: dict[str, Any]) -> None:
    key = next(name for name in ("set", "del", "append") if name in operation)
    path: list[str | int] = operation[key]
    parent: Any = document
    for part in path[:-1]:
        parent = parent[part]
    last = path[-1]
    if key == "set":
        parent[last] = operation["value"]
    elif key == "del":
        del parent[last]
    else:
        parent[last].append(operation["value"])


def _write_case(tmp_path: Path, case: dict[str, Any]) -> Path:
    document: dict[str, Any] = copy.deepcopy(BASE)
    for operation in case.get("patch", []):
        _apply(document, operation)
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    if "fragment" in case:
        document["include"] = ["extra.yaml"]
        with (tmp_path / "extra.yaml").open("w", encoding="utf-8") as stream:
            yaml.dump(case["fragment"], stream)
    with (tmp_path / "project.yaml").open("w", encoding="utf-8") as stream:
        yaml.dump(document, stream)
    return tmp_path


def test_base_project_is_valid() -> None:
    issues = vectors.validate_project(HERE / "semantic_base.yaml")
    assert [str(issue) for issue in issues] == []


@pytest.mark.parametrize("project", EXAMPLES, ids=lambda path: path.name)
def test_examples_are_semantically_valid(project: Path) -> None:
    issues = validate.validate_project(project)
    assert [str(issue) for issue in issues] == []


@pytest.mark.parametrize("project", EXAMPLES, ids=lambda path: path.name)
def test_example_vectors_agree_with_the_codec(project: Path) -> None:
    plugin_file = service.find_plugins(project)
    plugins = service.load_plugins(plugin_file)
    issues = vectors.validate_project(project, plugins)
    assert [str(issue) for issue in issues] == []


def test_corpus_size_and_rule_coverage() -> None:
    assert len(CASES) >= 50
    assert len({case["name"] for case in CASES}) == len(CASES)
    assert {case["rule"] for case in CASES} == set(RULES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_negative_case(tmp_path: Path, case: dict[str, Any]) -> None:
    issues = vectors.validate_project(_write_case(tmp_path, case))
    text = "\n".join(str(issue) for issue in issues)
    matching = [
        issue
        for issue in issues
        if f"[{case['rule']}]" in issue.message and str(case["expect"]) in str(issue)
    ]
    assert matching, text
    assert all(issue.line is not None for issue in matching), text


def test_issue_location_points_at_the_field(tmp_path: Path) -> None:
    case: dict[str, Any] = {
        "patch": [{"set": ["messages", "Command", "fields", 0, "default"], "value": 500}]
    }
    issues = vectors.validate_project(_write_case(tmp_path, case))
    assert len(issues) == 1
    assert issues[0].pointer == "/messages/Command/fields/0/default"
    assert issues[0].file.endswith("project.yaml")


def test_main_reports_ok_and_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert validate.main([str(HERE / "semantic_base.yaml")]) == 0
    bad = _write_case(tmp_path, CASES[0])
    assert validate.main([str(bad)]) == 1
    assert "SR-1" in capsys.readouterr().out
