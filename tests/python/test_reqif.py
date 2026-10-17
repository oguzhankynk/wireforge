from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wireforge import bundle, hlr_import, lifecycle, reqif, service
from wireforge.loader import load_project
from wireforge.results import ResultsError, parse_xml

if TYPE_CHECKING:
    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"
SAMPLE = AVIONICS / "requirements" / "srs_sample.reqif"

FOREIGN = Path(__file__).parent / "reqif_fixture" / "other_tool.reqif"


def _project(location: Path) -> Project:
    result = load_project(location)
    assert result.project is not None, result.issues
    return result.project


def _context(location: Path) -> lifecycle.DocsContext:
    project = _project(location)
    content_hash = bundle.build_bundle(location)["bundle"]["content_hash"]
    return lifecycle.context(
        project, content_hash, service.load_plugins(service.find_plugins(location))
    )


def test_the_sample_is_read_with_its_xhtml_text() -> None:
    document = reqif.read(SAMPLE)
    assert document.tool == "Sample SRS export"
    ids = [item.id for item in document.requirements]
    assert ids == [
        "HLR-NAV-001",
        "HLR-NAV-002",
        "HLR-NBS-001",
        "HLR-CMD-001",
        "HLR-LNK-001",
    ]
    assert document.requirements[3].text == "Commands to nodeC shall carry the vendor checksum."
    assert "ReqIF.ForeignID" in document.attributes


def test_a_foreign_file_needs_its_attribute_names() -> None:
    with pytest.raises(ResultsError, match="Object Identifier, Object Text"):
        reqif.read(FOREIGN)
    document = reqif.read(FOREIGN, "Object Identifier", "Object Text")
    assert [(r.id, r.text) for r in document.requirements] == [("SYS-1", "The system shall do it.")]


def test_reqifz_is_read_like_reqif(tmp_path: Path) -> None:
    archive = tmp_path / "srs.reqifz"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.write(SAMPLE, "srs.reqif")
    assert reqif.read(archive).requirements == reqif.read(SAMPLE).requirements
    empty = tmp_path / "empty.reqifz"
    with zipfile.ZipFile(empty, "w") as zipped:
        zipped.writestr("readme.txt", "x")
    with pytest.raises(ResultsError, match=r"no \.reqif file"):
        reqif.read(empty)
    with pytest.raises(ResultsError):
        reqif.read(tmp_path / "missing.reqif")


def test_the_written_llrs_can_be_read_back(tmp_path: Path) -> None:
    ctx = _context(AVIONICS)
    path = tmp_path / "llr.reqifz"
    reqif.write_llrs(path, ctx, AVIONICS)
    document = reqif.read(path)
    assert document.tool == "WireForge"
    written = {item.id for item in document.requirements}
    wanted = {row.id for row in ctx.llr_rows} | {
        parent for row in ctx.llr_rows for parent in row.parents
    }
    assert written == wanted, "every requirement and the placeholders of the parents"
    texts = {item.id: item.text for item in document.requirements}
    assert texts["LLR-NavSolution.LAYOUT"].startswith("`NavSolution` shall be encoded")
    assert texts["HLR-NAV-001"] == "", "a placeholder carries the id only"


def test_the_output_is_deterministic(tmp_path: Path) -> None:
    ctx = _context(AVIONICS)
    first, second = tmp_path / "a.reqif", tmp_path / "b.reqif"
    reqif.write_llrs(first, ctx, AVIONICS)
    reqif.write_llrs(second, ctx, AVIONICS)
    assert first.read_bytes() == second.read_bytes()
    zipped_a, zipped_b = tmp_path / "a.reqifz", tmp_path / "b.reqifz"
    reqif.write_llrs(zipped_a, ctx, AVIONICS)
    reqif.write_llrs(zipped_b, ctx, AVIONICS)
    assert zipped_a.read_bytes() == zipped_b.read_bytes()
    text = first.read_text(encoding="utf-8")
    assert text.count('IDENTIFIER="_wf_') > 100
    assert reqif.EPOCH in text or "T" in text


def test_the_xml_is_well_formed_and_has_the_reqif_structure(tmp_path: Path) -> None:
    path = tmp_path / "llr.reqif"
    reqif.write_llrs(path, _context(AVIONICS), AVIONICS)
    root = parse_xml(path.read_text(encoding="utf-8"))
    assert root.tag == "REQ-IF"
    assert root.find("THE-HEADER") is not None
    content = root.find("CORE-CONTENT")
    assert content is not None
    for name in ("DATATYPES", "SPEC-TYPES", "SPEC-OBJECTS", "SPEC-RELATIONS", "SPECIFICATIONS"):
        assert content.children[0].find(name) is not None, name
    identifiers = [node.get("IDENTIFIER") for node in root.iter("SPEC-OBJECT")]
    assert len(identifiers) == len(set(identifiers))


def test_export_through_the_service(tmp_path: Path) -> None:
    output = tmp_path / "docs"
    options = service.DocsOptions(export=("reqif", "csv"))
    result = service.lifecycle_docs(AVIONICS, output, options=options)
    assert result.ok
    assert (output / "llr.reqifz").exists()
    assert "llr.reqifz" in result.data["files"]
    with pytest.raises(service.ServiceError, match="unknown export format"):
        service.lifecycle_docs(AVIONICS, output, options=service.DocsOptions(export=("pdf",)))


def _hlr_copy(tmp_path: Path) -> Path:
    target = tmp_path / "avionics"
    shutil.copytree(AVIONICS, target)
    (target / "requirements" / "hlr.yaml").unlink()
    root = target / "project.yaml"
    root.write_text(
        root.read_text(encoding="utf-8").replace("  - requirements/*.yaml\n", ""), encoding="utf-8"
    )
    return target


def test_import_writes_a_snapshot_and_includes_it(tmp_path: Path) -> None:
    target = _hlr_copy(tmp_path)
    result = service.requirements_import(
        target, target / "requirements" / "srs_sample.reqif", hlr_import.ImportOptions("B1")
    )
    assert result.ok, result.text
    assert result.data["requirements"] == 5
    project = _project(target)
    assert set(project.requirements) >= {"HLR-NAV-001", "HLR-CMD-001"}
    assert project.requirements_source is not None
    assert project.requirements_source["baseline"] == "B1"
    assert "requirements/*.yaml" in (target / "project.yaml").read_text(encoding="utf-8")


def test_importing_again_is_idempotent_and_refuses_hand_edits(tmp_path: Path) -> None:
    target = _hlr_copy(tmp_path)
    source = target / "requirements" / "srs_sample.reqif"
    options = hlr_import.ImportOptions("B1")
    service.requirements_import(target, source, options)
    snapshot = target / "requirements" / "hlr_snapshot.yaml"
    first = snapshot.read_text(encoding="utf-8")
    service.requirements_import(target, source, options)
    assert snapshot.read_text(encoding="utf-8") == first
    snapshot.write_text(first.replace("vendor checksum", "VENDOR CHECKSUM"), encoding="utf-8")
    with pytest.raises(service.ServiceError, match="modified by hand"):
        service.requirements_import(target, source, options)
    forced = hlr_import.ImportOptions("B1", force=True)
    service.requirements_import(target, source, forced)
    assert snapshot.read_text(encoding="utf-8") == first


def test_csv_import_and_bad_ids(tmp_path: Path) -> None:
    target = _hlr_copy(tmp_path)
    table = tmp_path / "srs.csv"
    table.write_text(
        "Key,Statement\nHLR-A,Do the first thing.\nHLR-B,Do the second.\n", encoding="utf-8-sig"
    )
    with pytest.raises(service.ServiceError, match="no column 'id'"):
        service.requirements_import(target, table, hlr_import.ImportOptions("B1"))
    options = hlr_import.ImportOptions("B1", csv_id_column="Key", csv_text_column="Statement")
    assert service.requirements_import(target, table, options).data["requirements"] == 2
    table.write_text("Key,Statement\n1 bad id,Text.\n", encoding="utf-8")
    with pytest.raises(service.ServiceError, match="not a usable requirement id"):
        service.requirements_import(
            target,
            table,
            hlr_import.ImportOptions(
                "B2", csv_id_column="Key", csv_text_column="Statement", force=True
            ),
        )
