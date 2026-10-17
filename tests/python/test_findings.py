from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from wireforge import findings, service
from wireforge import results as test_results

AVIONICS = Path(__file__).resolve().parents[2] / "examples" / "02_multi_interface_avionics"
HEADER = "gen/avionics_demo/interfaces/our_to_nodeA.hpp"


def _sarif(tool: str, items: list[tuple[str, str, int, str | None]]) -> dict[str, object]:
    results = []
    for rule, uri, line, function in items:
        result: dict[str, object] = {
            "ruleId": rule,
            "level": "warning",
            "message": {"text": f"{rule} violated\nsecond line"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri},
                        "region": {"startLine": line},
                    },
                    **({"logicalLocations": [{"name": function}]} if function else {}),
                }
            ],
        }
        results.append(result)
    return {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": tool}}, "results": results}]}


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_sarif_with_two_tools_and_out_of_scope_files(tmp_path: Path) -> None:
    one = _write(
        tmp_path / "a.sarif",
        _sarif(
            "Checker A",
            [
                ("M5-0-1", f"file://{tmp_path}/{HEADER}", 12, "decode"),
                ("M5-0-2", "src/app.cpp", 3, None),
            ],
        ),
    )
    two = _write(
        tmp_path / "b.sarif",
        _sarif("Checker B", [("A7-1-1", "/opt/runtime/include/wireforge/ring.hpp", 9, None)]),
    )
    inside, outside = findings.load([one, two])
    assert outside == 1
    assert [(f.tool, f.rule, f.file, f.line) for f in inside] == [
        ("Checker A", "M5-0-1", "avionics_demo/interfaces/our_to_nodeA.hpp", 12),
        ("Checker B", "A7-1-1", "wireforge/ring.hpp", 9),
    ]
    assert inside[0].function == "decode"
    assert inside[0].message == "M5-0-1 violated"


def test_cppcheck_xml(tmp_path: Path) -> None:
    path = tmp_path / "c.xml"
    path.write_text(
        '<?xml version="1.0"?><results version="2"><errors>'
        '<error id="misra-c2012-1.1" severity="style" msg="m">'
        '<location file="x/wireforge/span.hpp" '
        'line="4"/></error></errors></results>',
        encoding="utf-8",
    )
    inside, outside = findings.load([path])
    assert (len(inside), outside) == (1, 0)
    assert (inside[0].tool, inside[0].file, inside[0].line) == ("cppcheck", "wireforge/span.hpp", 4)


def test_unknown_formats_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": "1.0"}', encoding="utf-8")
    with pytest.raises(test_results.ResultsError, match="SARIF"):
        findings.read(bad)
    xml = tmp_path / "x.xml"
    xml.write_text("<other/>", encoding="utf-8")
    with pytest.raises(test_results.ResultsError, match="neither"):
        findings.read(xml)
    with pytest.raises(test_results.ResultsError, match="cannot read"):
        findings.read(tmp_path / "missing")


def _deviation(**changes: str) -> test_results.Deviation:
    values = {
        "kind": "rule",
        "file": "wireforge/ring.hpp",
        "function": "*",
        "tool": "checker a",
        "rule": "R1",
        "category": "justified",
        "rationale": "Reviewed.",
        "reviewer": "J. Doe",
    }
    values.update(changes)
    return test_results.Deviation(
        kind=values["kind"],
        file=values["file"],
        function=values["function"],
        lines=(),
        category=values["category"],
        rationale=values["rationale"],
        reviewer=values["reviewer"],
        tool=values["tool"],
        rule=values["rule"],
    )


def test_matching_by_tool_rule_file_and_function() -> None:
    def finding(rule: str, function: str | None) -> findings.RuleFinding:
        return findings.RuleFinding(
            "Checker A", rule, "warning", "wireforge/ring.hpp", 5, function, ""
        )

    items = [finding("R1", "push"), finding("R2", "push"), finding("R3", "pop")]
    deviations = [
        _deviation(),
        _deviation(rule="R3", function="po*"),
        _deviation(rule="R9"),
        _deviation(kind="coverage"),
    ]
    open_findings, unused = findings.match_rules(items, deviations)
    assert [f.rule for f in open_findings] == ["R2"]
    assert [d.rule for d in unused] == ["R9"]
    gaps = findings.group(open_findings)
    assert [(g.rule, g.count) for g in gaps] == [("R2", 1)]


def _project(tmp_path: Path) -> Path:
    target = tmp_path / "avionics"
    shutil.copytree(AVIONICS, target)
    return target


def test_docs_without_rule_findings_say_so(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert service.lifecycle_docs(_project(tmp_path), out).ok
    text = (out / "code_standard.md").read_text(encoding="utf-8")
    assert "No external rule checker results provided" in text
    assert "DS-9" not in (out / "index.md").read_text(encoding="utf-8")


def test_docs_with_rule_findings_and_a_deviation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    report = _write(
        tmp_path / "r.sarif",
        _sarif(
            "Checker A",
            [
                ("R1", HEADER, 7, "decode"),
                ("R2", HEADER, 8, "encode"),
                ("R4", "other.cpp", 1, None),
            ],
        ),
    )
    (project / "deviations.yaml").write_text(
        "schema_version: 1\ndeviations:\n"
        "  - kind: rule\n    tool: checker a\n    rule: R1\n"
        '    file: "avionics_demo/interfaces/*.hpp"\n    category: justified\n'
        "    rationale: Reviewed.\n    reviewer: J. Doe\n"
        '  - kind: rule\n    tool: Checker A\n    rule: R7\n    file: "*"\n'
        "    category: false-positive\n    rationale: Gone.\n    reviewer: J. Doe\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    options = service.DocsOptions(rule_findings=(report,))
    result = service.lifecycle_docs(project, out, strict=True, options=options)
    text = (out / "code_standard.md").read_text(encoding="utf-8")
    assert "1 elsewhere (outside scope)" in text
    assert "Checker A R7" in text, "unused deviation is listed"
    assert any(item["rule"] == "DS-9" for item in result.data["strict"])
    assert "R2" in text
    assert not result.ok
