from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from wireforge import bundle, versioning
from wireforge.loader import load_project
from wireforge.validate import validate_project

ROOT = Path(__file__).resolve().parents[2]
AVIONICS = ROOT / "examples" / "02_multi_interface_avionics"
EXAMPLES = sorted(path.parent for path in (ROOT / "examples").glob("*/project.yaml"))


def _read(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    return loaded


def _write(path: Path, document: dict[str, Any]) -> Path:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    with path.open("w", encoding="utf-8") as stream:
        yaml.dump(document, stream)
    return path


@pytest.mark.parametrize("project", EXAMPLES, ids=lambda path: path.name)
def test_bundle_is_a_valid_single_file_project(tmp_path: Path, project: Path) -> None:
    output = tmp_path / "bundle.yaml"
    bundle.export_bundle(project, output)
    document = _read(output)
    assert "include" not in document
    assert document["schema_version"] == versioning.FORMAT_MAJOR
    assert document["generator"]["name"] == "wireforge"
    assert len(document["bundle"]["content_hash"]) == 64
    assert validate_project(output) == []


def test_bundle_merges_fragments_and_lists_sources(tmp_path: Path) -> None:
    output = tmp_path / "bundle.yaml"
    bundle.export_bundle(AVIONICS, output)
    document = _read(output)
    assert set(document["interfaces"]) == {"nodeB_to_our", "our_to_nodeA", "our_to_nodeC"}
    assert set(document["messages"]) == {"NavSolution", "NodeBStatus", "Command"}
    assert "profiles/platforms.yaml" in document["bundle"]["source_files"]
    assert document["bundle"]["source_files"][0] == "project.yaml"


def test_hash_ignores_stamps_and_key_order() -> None:
    document = bundle.build_bundle(AVIONICS)
    expected = document["bundle"]["content_hash"]
    reordered = dict(reversed(list(document.items())))
    reordered["generator"] = {"name": "wireforge", "version": "9.9.9"}
    assert bundle.content_hash(reordered) == expected
    reordered["version"] = "2.1.1"
    assert bundle.content_hash(reordered) != expected


def test_import_round_trip_preserves_content(tmp_path: Path) -> None:
    output = tmp_path / "bundle.yaml"
    bundle.export_bundle(AVIONICS, output)
    destination = tmp_path / "imported"
    assert bundle.import_bundle(output, destination) == []
    assert (destination / "project.yaml").is_file()
    assert (destination / "interfaces" / "our_to_nodeA.yaml").is_file()
    assert (destination / "types" / "types.yaml").is_file()
    for path in destination.rglob("*.yaml"):
        assert _read(path)["schema_version"] == versioning.FORMAT_MAJOR, path
    again = bundle.build_bundle(destination)
    assert again["bundle"]["content_hash"] == _read(output)["bundle"]["content_hash"]


def test_import_single_file(tmp_path: Path) -> None:
    output = tmp_path / "bundle.yaml"
    bundle.export_bundle(AVIONICS, output)
    destination = tmp_path / "single"
    assert bundle.import_bundle(output, destination, single_file=True) == []
    assert [path.name for path in destination.iterdir()] == ["project.yaml"]
    assert "bundle" not in _read(destination / "project.yaml")


def test_tampered_bundle_is_refused_unless_unverified(tmp_path: Path) -> None:
    output = tmp_path / "bundle.yaml"
    bundle.export_bundle(AVIONICS, output)
    document = _read(output)
    document["messages"]["Command"]["fields"][0]["max"] = 5
    _write(output, document)
    with pytest.raises(bundle.BundleError, match="content hash mismatch"):
        bundle.import_bundle(output, tmp_path / "a")
    assert bundle.import_bundle(output, tmp_path / "b", verify=False) == []


def test_plain_project_needs_no_verify(tmp_path: Path) -> None:
    project = ROOT / "examples" / "01_basic_telemetry" / "project.yaml"
    with pytest.raises(bundle.BundleError, match="not a bundle"):
        bundle.import_bundle(project, tmp_path / "a")
    assert bundle.import_bundle(project, tmp_path / "b", verify=False) == []


def test_non_empty_destination_is_refused(tmp_path: Path) -> None:
    output = tmp_path / "bundle.yaml"
    bundle.export_bundle(AVIONICS, output)
    with pytest.raises(bundle.BundleError, match="not empty"):
        bundle.import_bundle(output, tmp_path)


def test_export_refuses_invalid_project(tmp_path: Path) -> None:
    document = _read(ROOT / "examples" / "01_basic_telemetry" / "project.yaml")
    document["messages"]["SensorReading"]["fields"][0]["default"] = 99
    _write(tmp_path / "project.yaml", document)
    with pytest.raises(bundle.BundleError, match="export refused") as info:
        bundle.build_bundle(tmp_path)
    assert any("SR-6" in issue.message for issue in info.value.issues)


def test_import_refuses_invalid_structure(tmp_path: Path) -> None:
    path = _write(tmp_path / "bad.yaml", {"schema_version": 1, "project": "p", "bogus": 1})
    with pytest.raises(bundle.BundleError, match="not a valid project") as info:
        bundle.import_bundle(path, tmp_path / "out", verify=False)
    assert info.value.issues


def test_unreadable_bundle(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(bundle.BundleError, match="must be a mapping"):
        bundle.import_bundle(path, tmp_path / "out")
    with pytest.raises(bundle.BundleError, match="cannot read"):
        bundle.import_bundle(tmp_path / "missing.yaml", tmp_path / "out")


def _rename_project(document: MutableMapping[str, object]) -> None:
    document["project"] = "migrated"


def test_import_applies_migrations(tmp_path: Path) -> None:
    content = bundle.build_bundle(AVIONICS)
    content["schema_version"] = 0
    path = _write(tmp_path / "old.yaml", content)
    with pytest.raises(bundle.BundleError, match="no migration path"):
        bundle.import_bundle(path, tmp_path / "a")
    with versioning.temporary_migration(0, _rename_project):
        assert bundle.import_bundle(path, tmp_path / "b") == []
    result = load_project(tmp_path / "b")
    assert result.project is not None
    assert result.project.name == "migrated"
