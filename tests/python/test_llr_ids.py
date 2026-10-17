from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wireforge import cpp_tests, lifecycle, llr, llr_ids, service
from wireforge.loader import load_project

if TYPE_CHECKING:
    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "basic"
    shutil.copytree(EXAMPLES / "01_basic_telemetry", target)
    return target


def _keys(project: Project) -> list[str]:
    return [item.key for item in llr.derive(project)]


def test_assign_numbers_new_keys_in_order() -> None:
    ids = llr_ids.assign(["B.x.RANGE", "A.LAYOUT"], None)
    assert ids.ids == {"B.x.RANGE": "SWLLR-00001", "A.LAYOUT": "SWLLR-00002"}
    assert ids.next == 3
    assert llr_ids.assign(["B.x.RANGE", "A.LAYOUT"], None) == ids, "deterministic"


def test_assign_is_append_only_and_never_reuses_a_number() -> None:
    first = llr_ids.assign(["A.LAYOUT", "A.SIZE"], None)
    second = llr_ids.assign(["A.LAYOUT", "A.SIZE", "A.TRAILING"], first)
    assert second.ids["A.LAYOUT"] == first.ids["A.LAYOUT"]
    assert second.ids["A.TRAILING"] == "SWLLR-00003"
    third = llr_ids.assign(["A.LAYOUT", "A.TRAILING"], second)
    assert "A.SIZE" not in third.ids
    assert third.retired == {"A.SIZE": "SWLLR-00002"}
    fourth = llr_ids.assign(["A.LAYOUT", "A.TRAILING", "A.SIZE"], third)
    assert fourth.ids["A.SIZE"] == "SWLLR-00004", "a returning key gets a new number"
    assert fourth.retired["A.SIZE"] == "SWLLR-00002"


def test_rename_keeps_the_number() -> None:
    ids = llr_ids.assign(["A.x.RANGE", "A.LAYOUT"], None)
    renamed = llr_ids.rename(ids, "A.x.RANGE", "A.y.RANGE")
    assert renamed.ids == {"A.y.RANGE": "SWLLR-00001", "A.LAYOUT": "SWLLR-00002"}
    with pytest.raises(llr_ids.LockError, match="has no number"):
        llr_ids.rename(ids, "A.none", "A.z")
    with pytest.raises(llr_ids.LockError, match="already has"):
        llr_ids.rename(ids, "A.x.RANGE", "A.LAYOUT")


def test_the_lock_file_round_trips(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    result = service.requirements_lock(target)
    assert result.ok, result.text
    loaded = load_project(target).project
    assert loaded is not None
    assert loaded.llr_ids is not None
    assert set(loaded.llr_ids.ids) == set(_keys(loaded))
    assert llr_ids.dump(loaded.llr_ids) == (target / "llr_ids.lock").read_text(encoding="utf-8")
    again = service.requirements_lock(target)
    assert again.data["added"] == 0
    assert again.data["retired"] == 0


def test_a_format_outside_the_id_alphabet_is_a_schema_error(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    (target / "llr_ids.lock").write_text(
        "schema_version: 1\nformat: 'SW LLR {n}'\nnext: 1\nids: {}\n", encoding="utf-8"
    )
    result = load_project(target)
    assert any("llr_ids.lock" in issue.file for issue in result.issues)


def test_numbers_reach_code_tests_and_documents(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    assert service.requirements_lock(target, "REQ_{n:04d}").ok
    project = load_project(target).project
    assert project is not None
    assert project.llr_ids is not None
    number = project.llr_ids.ids["SensorReading.temperature.RANGE"]
    assert number.startswith("REQ_")
    assert llr.display_id("SensorReading.temperature.RANGE", project.llr_ids) == number
    assert llr.display_id("Other.LAYOUT", project.llr_ids) == "LLR-Other.LAYOUT"
    notes = llr.requirement_notes(project)
    assert number in "\n".join(notes["SensorReading"])
    sources = cpp_tests.message_test_source(project, ())
    assert "LLR-" not in sources
    output = tmp_path / "docs"
    assert service.lifecycle_docs(target, output, strict=True).ok
    requirements = (output / "requirements.md").read_text(encoding="utf-8")
    assert f"{number}</a>" in requirements or number in requirements
    assert "LLR-SensorReading" not in requirements


def test_a_requirement_without_a_number_is_ds8(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    assert service.requirements_lock(target).ok
    lock = (target / "llr_ids.lock").read_text(encoding="utf-8").splitlines()
    kept = [line for line in lock if "SensorReading.temperature.RANGE" not in line]
    (target / "llr_ids.lock").write_text("\n".join(kept) + "\n", encoding="utf-8")
    result = service.lifecycle_docs(target, tmp_path / "docs", strict=True)
    assert not result.ok
    assert any(
        "[DS-8]" in str(issue) and "temperature.RANGE" in str(issue) for issue in result.issues
    )
    plain = service.lifecycle_docs(target, tmp_path / "plain")
    assert plain.ok, "without --strict a missing number is not an error"


def test_rename_key_through_the_service(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    with pytest.raises(service.ServiceError, match=r"no llr_ids\.lock"):
        service.requirements_rename_key(target, "A", "B")
    assert service.requirements_lock(target).ok
    result = service.requirements_rename_key(
        target, "SensorReading.temperature.RANGE", "SensorReading.temp.RANGE"
    )
    assert "SWLLR-" in result.text
    project = load_project(target).project
    assert project is not None
    assert project.llr_ids is not None
    assert "SensorReading.temp.RANGE" in project.llr_ids.ids
    with pytest.raises(service.ServiceError, match="has no number"):
        service.requirements_rename_key(target, "SensorReading.temperature.RANGE", "X.y")


def test_the_list_command(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    listed = service.requirements_list(target)
    assert listed.ok
    assert listed.text.splitlines()[0].startswith("LLR-")
    assert service.requirements_lock(target).ok
    assert service.requirements_list(target).text.splitlines()[0].startswith("SWLLR-")
    assert lifecycle.slug("SWLLR-00001") == "swllr-00001"
