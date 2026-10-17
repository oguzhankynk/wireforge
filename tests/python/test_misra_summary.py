from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

ROOT = Path(__file__).resolve().parents[2]

REPORT = """\
<results version="2">
  <errors>
    <error id="misra-c2012-8.2" severity="style" msg="x">
      <location file="runtime/include/wireforge/span.hpp" line="3" column="1"/>
    </error>
    <error id="misra-c2012-8.2" severity="style" msg="x">
      <location file="tests/generated/avionics_test.cpp" line="9" column="1"/>
    </error>
    <error id="misra-c2012-15.5" severity="style" msg="y">
      <location file="/usr/include/c++/12/vector" line="1" column="1"/>
    </error>
    <error id="misra-c2012-2.7" severity="style" msg="z">
      <location file="build/dev/generated/model.hpp" line="1" column="1"/>
    </error>
  </errors>
</results>
"""


def _summary() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "misra_summary", ROOT / "quality" / "misra_summary.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_counts_only_findings_in_scope() -> None:
    counts = _summary().count(REPORT)
    assert dict(counts) == {"misra-c2012-8.2": 2, "misra-c2012-2.7": 1}


def test_main_prints_rules_and_total(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "findings.xml"
    report.write_text(REPORT, encoding="utf-8")
    assert _summary().main(["misra_summary", str(report)]) == 0
    printed = capsys.readouterr().out
    assert printed.splitlines()[0].endswith("misra-c2012-8.2")
    assert printed.splitlines()[-1].strip() == "3  total in 2 rules"
