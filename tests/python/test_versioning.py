from collections.abc import Callable, MutableMapping
from pathlib import Path

import pytest

from wireforge import schema, versioning
from wireforge.issues import errors_only


def _noop(document: MutableMapping[str, object]) -> None:
    document["touched"] = True


def _doc(**extra: object) -> dict[str, object]:
    return {"schema_version": 1, "project": "p", **extra}


def test_current_format_is_accepted() -> None:
    assert versioning.check_document(_doc(), file="f") == []
    assert versioning.check_document(_doc(schema_minor=0), file="f") == []


@pytest.mark.parametrize("bad", [None, "1", True, 1.0])
def test_missing_or_invalid_version(bad: object) -> None:
    document = _doc()
    document["schema_version"] = bad
    issues = versioning.check_document(document, file="f")
    assert len(issues) == 1
    assert "SR-19" in issues[0].message


def test_newer_major_is_a_hard_error() -> None:
    issues = versioning.check_document(_doc(schema_version=99), file="f")
    assert "upgrade WireForge" in issues[0].message
    assert errors_only(issues)


def test_older_major_without_migration_path() -> None:
    issues = versioning.check_document(_doc(schema_version=0), file="f")
    assert "no migration path" in issues[0].message


def test_older_major_with_migration_path() -> None:
    with versioning.temporary_migration(0, _noop):
        issues = versioning.check_document(_doc(schema_version=0), file="f")
    assert "wireforge import" in issues[0].message


def test_newer_minor_is_an_error() -> None:
    issues = versioning.check_document(_doc(schema_minor=versioning.FORMAT_MINOR + 1), file="f")
    assert "needs format" in issues[0].message


def test_newer_generator_is_only_a_warning() -> None:
    generator = {"name": "wireforge", "version": "99.0.0"}
    issues = versioning.check_document(_doc(generator=generator), file="f")
    assert [issue.severity for issue in issues] == ["warning"]
    older = {"name": "wireforge", "version": "0.0.1"}
    assert versioning.check_document(_doc(generator=older), file="f") == []


def test_stamp_writes_format_and_generator() -> None:
    document: dict[str, object] = {}
    versioning.stamp(document)
    assert document["schema_version"] == versioning.FORMAT_MAJOR
    assert document["schema_minor"] == versioning.FORMAT_MINOR
    assert document["generator"] == {"name": "wireforge", "version": versioning.TOOL_VERSION}


def test_migration_chain_runs_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[int] = []

    def step(major: int) -> Callable[[MutableMapping[str, object]], None]:
        def run(document: MutableMapping[str, object]) -> None:
            order.append(major)
            document[f"touched_{major}"] = True

        return run

    monkeypatch.setattr(versioning, "FORMAT_MAJOR", 3)
    document: dict[str, object] = {"schema_version": 1}
    with versioning.temporary_migration(1, step(1)), versioning.temporary_migration(2, step(2)):
        applied = versioning.migrate_document(document)
    assert order == [1, 2]
    assert applied == ["format 1 -> 2", "format 2 -> 3"]
    assert document["schema_version"] == 3
    assert document["touched_1"] is True


def test_migration_errors() -> None:
    with pytest.raises(ValueError, match="no migration path"):
        versioning.migrate_document({"schema_version": 0})
    with pytest.raises(ValueError, match="newer"):
        versioning.migrate_document({"schema_version": 7})
    with pytest.raises(TypeError):
        versioning.migrate_document({})


def test_project_validation_reports_only_the_version_problem(tmp_path: Path) -> None:
    path = tmp_path / "project.yaml"
    path.write_text("schema_version: 99\nproject: 1-invalid\nbogus: true\n")
    issues = schema.validate_project(path)
    assert len(issues) == 1
    assert "upgrade WireForge" in issues[0].message
    assert issues[0].line == 1


def test_fragment_without_version_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "project.yaml").write_text("schema_version: 1\nproject: p\ninclude: [t/*.yaml]\n")
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "a.yaml").write_text("enums: {}\n")
    issues = schema.validate_project(tmp_path)
    assert len(issues) == 1
    assert "schema_version" in issues[0].message
