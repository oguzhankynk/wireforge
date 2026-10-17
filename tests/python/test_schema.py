import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from ruamel.yaml import YAML

from wireforge import schema

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted(path.parent for path in (ROOT / "examples").glob("*/project.yaml"))
NEGATIVE = YAML(typ="safe").load((Path(__file__).parent / "negative_cases.yaml").read_text())


def test_three_examples_exist() -> None:
    assert len(EXAMPLES) >= 3


@pytest.mark.parametrize("project", EXAMPLES, ids=lambda path: path.name)
def test_examples_validate(project: Path) -> None:
    issues = schema.validate_project(project)
    assert [str(issue) for issue in issues] == []


def test_schemas_are_valid_json_schema() -> None:
    for name in schema.SCHEMA_FILES:
        contents = json.loads((schema.SCHEMA_DIR / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(contents)


@pytest.mark.parametrize("case", NEGATIVE, ids=lambda case: case["name"])
def test_negative_cases_are_rejected(case: dict[str, object]) -> None:
    schema_file = f"{case['schema']}.schema.json"
    issues = schema.validate_document(case["doc"], schema_file)
    text = "\n".join(str(issue) for issue in issues)
    assert issues, "document must be rejected"
    assert str(case["expect"]) in text, text


def test_yaml_12_keeps_on_off_as_strings(tmp_path: Path) -> None:
    path = tmp_path / "project.yaml"
    path.write_text(
        "schema_version: 1\nproject: p\nenums:\n  S: {type: u8, values: {on: 1, off: 0}}\n"
    )
    assert schema.validate_project(path) == []


def test_errors_carry_line_numbers(tmp_path: Path) -> None:
    path = tmp_path / "project.yaml"
    path.write_text(
        "schema_version: 1\nproject: p\nmessages:\n  M:\n    fields:\n"
        "      - {name: a, type: u8, bits: 99}\n"
    )
    issues = schema.validate_project(path)
    assert len(issues) == 1
    assert issues[0].line == 6
    assert issues[0].pointer == "/messages/M/fields/0/bits"


def test_fragment_errors_are_reported_with_their_own_file(tmp_path: Path) -> None:
    (tmp_path / "project.yaml").write_text(
        "schema_version: 1\nproject: p\ninclude: [types/*.yaml]\n"
    )
    (tmp_path / "types").mkdir()
    (tmp_path / "types" / "t.yaml").write_text("schema_version: 1\nproject: not_allowed_here\n")
    issues = schema.validate_project(tmp_path)
    assert len(issues) == 1
    assert issues[0].file.endswith("t.yaml")


def test_yaml_syntax_error_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "project.yaml"
    path.write_text("schema_version: [1\n")
    issues = schema.validate_project(path)
    assert len(issues) == 1
    assert "YAML syntax error" in issues[0].message


def test_deviations_schema_is_a_valid_json_schema() -> None:
    contents = json.loads(
        (schema.SCHEMA_DIR / "deviations.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(contents)
