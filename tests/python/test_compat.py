import copy
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from wireforge import compat

HERE = Path(__file__).parent
BASE: dict[str, Any] = YAML(typ="safe").load((HERE / "semantic_base.yaml").read_text("utf-8"))


def _apply(document: dict[str, Any], operation: dict[str, Any]) -> None:
    key = next(name for name in ("set", "del", "append", "insert") if name in operation)
    path: list[str | int] = operation[key]
    parent: Any = document
    for part in path[:-1]:
        parent = parent[part]
    if key == "set":
        parent[path[-1]] = operation["value"]
    elif key == "del":
        del parent[path[-1]]
    elif key == "insert":
        parent.insert(path[-1], operation["value"])
    else:
        parent[path[-1]].append(operation["value"])


def _write(directory: Path, operations: list[dict[str, Any]]) -> Path:
    document = copy.deepcopy(BASE)
    for operation in operations:
        _apply(document, operation)
    directory.mkdir()
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    with (directory / "project.yaml").open("w", encoding="utf-8") as stream:
        yaml.dump(document, stream)
    return directory


def _version(interface: str, version: str) -> dict[str, Any]:
    return {"set": ["interfaces", interface, "version"], "value": version}


def _report(
    tmp_path: Path, operations: list[dict[str, Any]], interface: str = "main"
) -> compat.InterfaceReport:
    old = _write(tmp_path / "old", [])
    new = _write(tmp_path / "new", operations)
    reports, issues = compat.compare_paths(old, new, [interface])
    assert issues == [], [str(issue) for issue in issues]
    return reports[0]


# (name, patch, expected severity, fragment of the change text)
MATRIX: list[tuple[str, list[dict[str, Any]], str, str]] = [
    (
        "field_type_widened",
        [{"set": ["messages", "Command", "fields", 0, "type"], "value": "u32"}],
        "breaking",
        "type 'u16' -> 'u32'",
    ),
    ("field_removed", [{"del": ["messages", "Command", "fields", 1]}], "breaking", "removed"),
    (
        "field_inserted_in_middle",
        [{"insert": ["messages", "Command", "fields", 1], "value": {"name": "pad", "type": "u32"}}],
        "breaking",
        "field pad: field inserted or moved",
    ),
    (
        "range_narrowed",
        [{"set": ["messages", "Command", "fields", 0, "max"], "value": 50}],
        "breaking",
        "accepted values changed: max 100 -> 50",
    ),
    (
        "enum_value_added",
        [{"set": ["enums", "Mode", "values", "pause"], "value": 3}],
        "breaking",
        "accepted values changed",
    ),
    (
        "message_id_changed",
        [{"set": ["messages", "Command", "id"], "value": 7}],
        "breaking",
        "id 2 -> 7",
    ),
    (
        "message_removed",
        [{"set": ["interfaces", "main", "messages"], "value": ["Status", "Command"]}],
        "breaking",
        "message Blob: removed",
    ),
    (
        "header_changed",
        [{"set": ["interfaces", "main", "header", "fields", 1, "type"], "value": "u16"}],
        "breaking",
        "header / field id",
    ),
    (
        "max_frame_size_changed",
        [{"set": ["interfaces", "main", "framing", "max_frame_size"], "value": 200}],
        "breaking",
        "max_frame_size",
    ),
    (
        "endian_changed",
        [{"set": ["messages", "Command", "endian"], "value": "little"}],
        "breaking",
        "endian big -> little",
    ),
    (
        "checksum_algorithm_changed",
        [
            {"set": ["checksums", "crc16", "init"], "value": 0},
            {"del": ["checksums", "crc16", "check"]},
        ],
        "breaking",
        "field sum",
    ),
    (
        "nested_struct_field_changed",
        [
            {"set": ["types", "Point", "fields", 1, "type"], "value": "i16"},
            {"set": ["types", "Point", "fields", 1, "min"], "value": -100},
        ],
        "breaking",
        "field points[].y",
    ),
    (
        "profile_specific_capacity",
        [{"set": ["profiles", "large", "defines", "MAX_ITEMS"], "value": 7}],
        "breaking",
        "[large]",
    ),
    (
        "trailing_field_rejected_by_old_receivers",
        [
            {"append": ["messages", "Blob", "fields"], "value": {"name": "tail", "type": "u8"}},
            {"set": ["messages", "Blob", "fields", 1, "occurs"], "value": {"max": 32}},
        ],
        "breaking",
        "field data",
    ),
    (
        "field_renamed",
        [{"set": ["messages", "Command", "fields", 1, "name"], "value": "unused"}],
        "compatible",
        "renamed from spare",
    ),
    (
        "default_changed",
        [{"set": ["messages", "Command", "fields", 0, "default"], "value": 5}],
        "compatible",
        "default changed",
    ),
    (
        "message_added",
        [
            {
                "set": ["messages", "Ping"],
                "value": {"id": 5, "fields": [{"name": "a", "type": "u8"}]},
            },
            {"append": ["interfaces", "main", "messages"], "value": "Ping"},
        ],
        "compatible",
        "message Ping: added",
    ),
    (
        "resync_changed",
        [{"set": ["interfaces", "main", "framing", "resync"], "value": False}],
        "compatible",
        "resync True -> False",
    ),
    (
        "description_changed",
        [{"set": ["messages", "Command", "fields", 0, "description"], "value": "Opcode."}],
        "doc",
        "documentation changed",
    ),
]


@pytest.mark.parametrize(
    ("patch", "severity", "fragment"),
    [row[1:] for row in MATRIX],
    ids=[row[0] for row in MATRIX],
)
def test_change_matrix(
    tmp_path: Path, patch: list[dict[str, Any]], severity: str, fragment: str
) -> None:
    report = _report(tmp_path, patch)
    text = "\n".join(str(change) for change in report.changes)
    assert any(
        change.severity == severity and fragment in str(change) for change in report.changes
    ), text
    assert report.required_bump == compat.BUMP_FOR[severity]
    assert not report.version_ok, "the version was not bumped"


def test_identical_projects_have_no_changes(tmp_path: Path) -> None:
    report = _report(tmp_path, [])
    assert report.changes == []
    assert report.required_bump == "none"
    assert report.version_ok


def test_trailing_field_is_compatible_when_old_receivers_ignore_trailing_bytes(
    tmp_path: Path,
) -> None:
    old = _write(
        tmp_path / "old",
        [
            {"set": ["interfaces", "raw", "allow_trailing_bytes"], "value": True},
            {"set": ["messages", "Blob", "fields", 1, "occurs"], "value": {"max": 4}},
        ],
    )
    new = _write(
        tmp_path / "new",
        [
            {"set": ["interfaces", "raw", "allow_trailing_bytes"], "value": True},
            {"set": ["messages", "Blob", "fields", 1, "occurs"], "value": {"max": 4}},
            {"append": ["messages", "Blob", "fields"], "value": {"name": "tail", "type": "u8"}},
        ],
    )
    reports, _ = compat.compare_paths(old, new, ["raw"])
    assert [change.severity for change in reports[0].changes] == ["compatible"]
    assert "old receivers ignore trailing bytes" in reports[0].changes[0].message


@pytest.mark.parametrize(
    ("new_version", "verdict"),
    [("2.0.0", "ok"), ("1.1.0", "too small"), ("1.0.1", "too small"), ("0.9.0", "too small")],
)
def test_breaking_change_needs_major_bump(tmp_path: Path, new_version: str, verdict: str) -> None:
    patch = [{"del": ["messages", "Command", "fields", 1]}, _version("main", new_version)]
    report = _report(tmp_path, patch)
    assert report.required_bump == "major"
    assert report.version_ok is (verdict == "ok")


def test_compatible_change_accepts_minor_bump(tmp_path: Path) -> None:
    patch = [
        {"set": ["messages", "Command", "fields", 0, "default"], "value": 5},
        _version("main", "1.1.0"),
    ]
    report = _report(tmp_path, patch)
    assert report.version_ok
    assert "requires minor bump: OK" in report.summary()


def test_interface_added_and_removed(tmp_path: Path) -> None:
    old = _write(tmp_path / "old", [])
    new = _write(tmp_path / "new", [{"del": ["interfaces", "fixed"]}])
    reports, _ = compat.compare_paths(old, new)
    by_name = {report.name: report for report in reports}
    assert by_name["fixed"].changes[0].severity == "breaking"
    reversed_reports, _ = compat.compare_paths(new, old, ["fixed"])
    assert reversed_reports[0].changes[0].message == "interface added"
    assert reversed_reports[0].version_ok


@pytest.mark.parametrize(
    ("old", "new", "bump"),
    [
        ("1.0.0", "2.0.0", "major"),
        ("1.0.0", "1.2.0", "minor"),
        ("1.0.0", "1.0.3", "patch"),
        ("1.0.0", "1.0.0", "none"),
        ("2.0.0", "1.9.9", "downgrade"),
        ("x", "1.0.0", "none"),
    ],
)
def test_version_bump(old: str, new: str, bump: str) -> None:
    assert compat.version_bump(old, new) == bump


def test_invalid_project_gives_issues_and_no_reports(tmp_path: Path) -> None:
    old = _write(tmp_path / "old", [])
    new = _write(
        tmp_path / "new", [{"set": ["messages", "Command", "fields", 0, "default"], "value": 500}]
    )
    reports, issues = compat.compare_paths(old, new)
    assert reports == []
    assert any("SR-6" in issue.message for issue in issues)


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old = _write(tmp_path / "old", [])
    same = _write(tmp_path / "same", [])
    broken = _write(tmp_path / "broken", [{"del": ["messages", "Command", "fields", 1]}])
    assert compat.main([str(old), str(same)]) == 0
    assert compat.main([str(old), str(broken), "--interface", "main"]) == 1
    output = capsys.readouterr().out
    assert "VERSION BUMP TOO SMALL" in output
    assert "breaking: interface main / message Command" in output
