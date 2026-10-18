from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wireforge.loader import load_project

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
QUALITY = ROOT / "quality"
sys.path.insert(0, str(QUALITY))


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, QUALITY / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODELS = _load("random_models")
MATRIX = _load("feature_matrix")


def test_the_same_seed_gives_the_same_model() -> None:
    assert MODELS.random_model(7) == MODELS.random_model(7)
    assert MODELS.random_model(7) != MODELS.random_model(8)


@pytest.mark.parametrize("seed", range(40))
def test_random_models_are_valid(tmp_path: Path, seed: int) -> None:
    MODELS.write_model(seed, tmp_path)
    loaded = load_project(tmp_path)
    assert loaded.project is not None
    assert [str(issue) for issue in loaded.issues] == []


def test_seed_ranges() -> None:
    assert MODELS.seeds_of(["3", "5-7"]) == [3, 5, 6, 7]


def test_every_feature_of_the_matrix_is_covered() -> None:
    seen = MATRIX.coverage()
    covered = set().union(*seen.values())
    assert [name for name in MATRIX.REQUIRED if name not in covered] == []


def test_every_property_of_the_schema_is_in_the_matrix() -> None:
    schema = json.loads((ROOT / "schema" / "project.schema.json").read_text(encoding="utf-8"))
    assert MATRIX.check_schema(schema) == []
