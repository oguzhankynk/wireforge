from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pytest

from wireforge import cpp_tests, generate, test_project
from wireforge.coverage_points import CoveragePointError, Points


class _Writer(Protocol):
    points: Points


ROOT = Path(__file__).resolve().parents[2]
MODELS = sorted(
    path.parent
    for pattern in (
        "examples/*/project.yaml",
        "protocols/*/project.yaml",
        "tests/models/*/project.yaml",
        "codegen/wireforge/templates/*/project.yaml",
    )
    for path in ROOT.glob(pattern)
)


def test_a_point_is_required_exercised_and_checked() -> None:
    points = Points("x.cpp")
    points.require("room", "profile", "iface", "M1")
    points.require("room", "profile", "iface", "M2")
    points.exercise("room", "profile", "iface", "M1")
    assert points.gaps() == [("room", "profile", "iface", "M2")]
    with pytest.raises(CoveragePointError, match="room / profile / iface / M2"):
        points.check()
    points.exercise("room", "profile", "iface", "M2")
    points.check()


def test_trailing_empty_parts_do_not_matter() -> None:
    points = Points("x.cpp")
    points.require("corrupt", None, "link")
    points.exercise("corrupt", None, "link", "")
    points.check()


@pytest.mark.parametrize("model", MODELS, ids=lambda path: path.name)
def test_every_required_point_of_every_model_is_exercised(model: Path, tmp_path: Path) -> None:
    plugins = next(iter(sorted((model / "plugins").glob("[!_]*.py"))), None)
    issues = generate.generate(model, tmp_path, test_project.TestOptions(plugins=plugins))
    assert [str(issue) for issue in issues] == []


def test_a_test_that_skips_a_message_is_a_generator_defect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writer = vars(cpp_tests)["_InterfaceTests"]
    original = writer.too_small

    def forget_the_rooms(self: _Writer, cases: object, suite: str) -> None:
        original(self, cases, suite)
        self.points.exercised = {key for key in self.points.exercised if key[0] != "frame_room"}

    monkeypatch.setattr(writer, "too_small", forget_the_rooms)
    model = ROOT / "examples" / "02_multi_interface_avionics"
    plugins = model / "plugins" / "vendor_sum.py"
    issues = generate.generate(model, tmp_path, test_project.TestOptions(plugins=plugins))
    assert any("frame_room" in str(issue) for issue in issues)
    assert any("defect of the generator" in str(issue) for issue in issues)
