import os
import shutil
import subprocess
from pathlib import Path

import pytest

from wireforge import bundle, generate
from wireforge.cpp import Names, float_literal, guard, int_literal, string_literal
from wireforge.loader import load_project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted(
    path.parent
    for folder in ("examples", "protocols")
    for path in (ROOT / folder).glob("*/project.yaml")
)
SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = os.environ.get("WIREFORGE_UPDATE_SNAPSHOTS") == "1"


def _render(example: Path) -> dict[str, str]:
    result = load_project(example)
    assert result.project is not None
    content_hash = bundle.build_bundle(example)["bundle"]["content_hash"]
    return generate.render(result.project, content_hash)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_generated_code_matches_snapshots(example: Path) -> None:
    for relative, text in _render(example).items():
        snapshot = SNAPSHOTS / (relative + ".snap")
        if UPDATE:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(text, encoding="utf-8")
        assert snapshot.read_text(encoding="utf-8") == text, (
            f"{relative} changed; review and rerun with WIREFORGE_UPDATE_SNAPSHOTS=1"
        )


@pytest.mark.parametrize("name", ["03_edge_cases", "04_user_type"])
def test_projects_without_requirements_have_no_trace_lines(name: str) -> None:
    files = _render(ROOT / "examples" / name)
    assert not [path for path, text in files.items() if "Requirements:" in text]


@pytest.mark.parametrize("name", ["01_basic_telemetry", "02_multi_interface_avionics"])
def test_requirements_are_written_into_the_doxygen_blocks(name: str) -> None:
    files = _render(ROOT / "examples" / name)
    text = "\n".join(files.values())
    assert "/// Requirements: HLR-" in text
    assert all(
        len(line) <= 100 for line in text.splitlines() if line.startswith("/// Requirements")
    )


def test_output_is_deterministic() -> None:
    first = _render(EXAMPLES[1])
    assert _render(EXAMPLES[1]) == first


def test_generate_writes_files_and_cli(tmp_path: Path) -> None:
    assert generate.main([str(EXAMPLES[0]), "-o", str(tmp_path)]) == 0
    model = tmp_path / "basic_telemetry" / "model.hpp"
    stamp = model.stat().st_mtime_ns
    assert generate.generate(EXAMPLES[0], tmp_path) == []
    assert model.stat().st_mtime_ns == stamp, "unchanged files are not rewritten"


def test_invalid_project_is_not_generated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "schema_version: 1\nproject: p\nmessages:\n  M:\n    fields:\n"
        "      - { name: a, type: u8, default: 300 }\n",
        encoding="utf-8",
    )
    assert generate.main([str(project), "-o", str(tmp_path / "out")]) == 1
    assert "SR-6" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_cpp_spelling_helpers() -> None:
    assert guard("my-project", "model.hpp") == "MY_PROJECT_MODEL_HPP"
    assert float_literal(1.0, "f32") == "1.0F"
    assert float_literal(1e300, "f64") == "1e+300"
    assert int_literal(-(2**63), unsigned=False) == "(-9223372036854775807 - 1)"
    assert int_literal(5, unsigned=True) == "5U"
    assert string_literal('a"\\\n\u00e9') == '"a\\"\\\\\\012\\351"'
    with pytest.raises(ValueError, match="no C\\+\\+ literal"):
        float_literal(float("inf"), "f64")


def test_conditions_fall_back_to_define_defaults() -> None:
    project = load_project(EXAMPLES[1]).project
    assert project is not None
    names = Names(project)
    assert names.condition("defined(PLATFORM_B)") == "defined(PLATFORM_B)"
    assert names.condition("CHANNELS > 3 && !defined(X)") == (
        "(((defined(CHANNELS) ? CHANNELS : 4) > 3) && (!defined(X)))"
    )
    assert names.value("MAX_TRACKS * 2") == "(defines::kMAX_TRACKS * std::int64_t{2})"
    assert names.value("APP_MAX_FRAME") == "static_cast<std::int64_t>(app::kMaxFrameBytes)"


DOXYFILE = """
INPUT = {input}
OUTPUT_DIRECTORY = {output}
FILE_PATTERNS = *.hpp
RECURSIVE = YES
QUIET = YES
WARN_IF_UNDOCUMENTED = YES
WARN_AS_ERROR = YES
GENERATE_HTML = NO
GENERATE_LATEX = NO
GENERATE_XML = YES
"""


def test_generated_code_is_fully_documented(tmp_path: Path) -> None:
    doxygen = shutil.which("doxygen")
    if doxygen is None:
        pytest.skip("doxygen is not installed")
    generated = tmp_path / "include"
    for example in EXAMPLES:
        assert generate.generate(example, generated) == []
    config = tmp_path / "Doxyfile"
    config.write_text(DOXYFILE.format(input=generated, output=tmp_path / "doc"), encoding="utf-8")
    result = subprocess.run([doxygen, str(config)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout


def test_unsupported_interface_layout_is_reported(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "schema_version: 1\nproject: p\n"
        "messages:\n  M: { id: 1, fields: [{ name: a, type: u8 }] }\n"
        "interfaces:\n  link:\n    version: 1.0.0\n    direction: both\n"
        "    header:\n      fields:\n"
        "        - { name: id, type: u8, bits: 4, role: message_id }\n"
        "        - { name: spare, type: u8, bits: 4, reserved: true }\n"
        "    messages: [M]\n",
        encoding="utf-8",
    )
    issues = generate.generate(project, tmp_path / "out")
    assert len(issues) == 1
    assert "byte-aligned integer" in issues[0].message


def test_interface_headers_are_generated() -> None:
    files = _render(EXAMPLES[1])
    assert sorted(path for path in files if "/interfaces/" in path) == [
        "avionics_demo/interfaces/nodeB_to_our.hpp",
        "avionics_demo/interfaces/our_to_nodeA.hpp",
        "avionics_demo/interfaces/our_to_nodeC.hpp",
    ]
    header = files["avionics_demo/interfaces/our_to_nodeA.hpp"]
    assert "inline namespace v1_2 {" in header
    assert "virtual void onNavSolutionReceived(const NavSolution& message) noexcept = 0;" in header
    assert "virtual wireforge::Status onNavSolutionRaw(" in header
    assert "        const wireforge::RawMessage& raw) noexcept {" in header
    assert "detail::check_frame(raw.frame)" in header, "the protocol version is checked"
    assert "? handler_->onNavSolutionRaw(raw) : checked;" in header
