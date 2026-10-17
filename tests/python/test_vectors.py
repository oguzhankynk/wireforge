from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from wireforge import cpp_tests, fmt, golden, test_project, vectors
from wireforge.loader import load_project

if TYPE_CHECKING:
    from wireforge.model import Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
AVIONICS = EXAMPLES / "02_multi_interface_avionics"
PLUGINS = golden.load_plugins(AVIONICS / "plugins" / "vendor_sum.py")


def _project(location: Path) -> Project:
    result = load_project(location)
    assert result.project is not None, result.issues
    return result.project


def _inline(tmp_path: Path, text: str) -> Project:
    (tmp_path / "project.yaml").write_text(text, encoding="utf-8")
    return _project(tmp_path)


def test_basic_vectors_run_once_without_profiles() -> None:
    runs = vectors.run(_project(EXAMPLES / "01_basic_telemetry"))
    assert [(item.vector.name, item.profile, item.status) for item in runs] == [
        ("reading_nominal", None, "Ok"),
        ("reading_sensor_id_out_of_range", None, "OutOfRange"),
        ("frame_nominal", None, "Ok"),
        ("frame_bad_checksum", None, "ChecksumMismatch"),
    ]
    both = runs[0]
    assert both.phase == "encode"
    assert both.decoded is not None
    assert both.encoded == both.vector.data


def test_vectors_expand_over_the_profiles() -> None:
    runs = vectors.run(_project(AVIONICS), PLUGINS)
    where = {(item.vector.name, item.profile) for item in runs}
    assert ("nodeb_status_truncated", "platformA") in where
    assert ("nodeb_status_truncated", "platformB") in where, "no profile means every profile"
    assert ("nodeb_status_nominal_a", "platformB") not in where, "a named profile only"
    assert ("nav_solution_nominal", "platformA") in where


def test_a_custom_checksum_without_plugins_is_skipped_with_a_warning() -> None:
    issues = vectors.check(_project(AVIONICS))
    assert issues
    assert all(issue.severity == "warning" for issue in issues)
    assert all("needs its plugin" in issue.message for issue in issues)
    assert vectors.check(_project(AVIONICS), PLUGINS) == []


INLINE = """\
schema_version: 1
project: inline
messages:
  M:
    id: 1
    fields:
      - { name: level, type: f32 }
      - { name: tag, type: char, occurs: { max: 4 } }
      - { name: blob, type: Raw4 }
types: {}
user_types:
  Raw4: { cpp: app::Raw4, size_bytes: 4 }
vectors:
  v:
    message: M
    bytes: "3DCCCCCD 41424300 01020304"
    expect: { fields: { level: 0.1, tag: ABC } }
    reqs: [R]
requirements:
  R: { text: x }
"""


def test_f32_expectations_are_rounded_like_the_wire() -> None:
    assert vectors.f32(0.1) != 0.1
    assert vectors.f32(21.5) == 21.5


def test_user_type_fields_do_not_block_a_vector(tmp_path: Path) -> None:
    project = _inline(tmp_path, INLINE)
    assert vectors.check(project) == []
    (run,) = vectors.run(project)
    assert run.decoded is not None
    assert run.decoded["blob"] == bytes([1, 2, 3, 4])
    source = cpp_tests.hlr_vector_test_source(project, [run])
    assert "EXPECT_EQ(decoded.level, 0.1F);" in source
    assert "decoded.blob" not in source


def test_the_generated_test_uses_the_value_of_the_model_not_the_codec(tmp_path: Path) -> None:
    project = _inline(tmp_path, INLINE)
    wrong = project.vectors["v"]
    broken = type(wrong)(
        wrong.name,
        wrong.message,
        wrong.text,
        wrong.data,
        wrong.source,
        fields={"level": 7.0, "tag": "ZZ"},
        reqs=wrong.reqs,
    )
    project.vectors["v"] = broken
    assert vectors.check(project), "the codec disagrees, so validation reports it"
    (run,) = vectors.run(project)
    source = cpp_tests.hlr_vector_test_source(project, [run])
    assert "EXPECT_EQ(decoded.level, 7.0F);" in source
    assert '"ZZ"' in source
    assert "0.1F" not in source


def test_project_without_vectors_has_no_vector_files() -> None:
    project = _project(EXAMPLES / "03_edge_cases")
    files = test_project.render(project, EXAMPLES / "03_edge_cases", ROOT / "build" / "x")
    assert "hlr_vector_test.cpp" not in files
    assert "hlr_vector_test.cpp" not in files["CMakeLists.txt"]
    assert "requirement_vector" not in files["selftest.cpp"]


def test_vector_sources_are_part_of_the_test_project() -> None:
    project = _project(AVIONICS)
    directory = ROOT / "build" / "x" / project.name / "generated_tests"
    options = test_project.TestOptions(plugins=AVIONICS / "plugins" / "vendor_sum.py")
    files = test_project.render(project, AVIONICS, directory, options)
    source = files["hlr_vector_test.cpp"]
    assert "hlr_vector_test.cpp" in files["CMakeLists.txt"]
    assert source.count("TEST(HlrVector, nodeb_status_truncated)") == 2, "one per profile"
    assert '::testing::Test::RecordProperty("requirements", "HLR-NAV-002");' in source
    assert "requirement_vector_nav_solution_nominal" in files["selftest.cpp"]
    assert "hlr_vector_test.cpp" in files["README.md"]


def test_vector_section_keeps_the_canonical_order() -> None:
    text = fmt.format_text(
        "vectors:\n  v:\n    reqs: [R]\n    expect: { status: Truncated, }\n    bytes: '00'\n"
        "    message: M\nschema_version: 1\nproject: p\n"
    )
    keys = [line.split(":")[0].strip() for line in text.splitlines() if line.startswith("    ")]
    assert keys == ["message", "bytes", "expect", "reqs"]
