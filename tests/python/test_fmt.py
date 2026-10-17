from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from wireforge import fmt

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
FILES = sorted(EXAMPLES.rglob("*.yaml"))


@pytest.mark.parametrize("path", FILES, ids=lambda path: str(path.relative_to(EXAMPLES)))
def test_examples_are_canonical(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert fmt.format_text(text) == text


SCRAMBLED = """\
# Project comment.
messages:   # eol of messages
  M:
    fields:
      - { type: u8, name: a }   # eol a
      # above b
      - type: u16
        name: b # eol b
        # above min
        min: 0
        # above units
        units: m
      # above c
      - name: c
        type: u8
    id: 7
project: p
schema_version: 1
defaults: {endian: little,   bit_order: lsb_first}
empty: {}
# trailing comment
"""


def test_reorders_keys_and_keeps_comments() -> None:
    text = fmt.format_text(SCRAMBLED)
    assert text.splitlines()[:3] == ["# Project comment.", "schema_version: 1", "project: p"]
    assert "defaults: { endian: little, bit_order: lsb_first }" in text
    assert "empty: {}" in text
    assert "      - { name: a, type: u8 }   # eol a" in text
    assert (
        "      - name: b # eol b\n        type: u16\n        # above min\n        min: 0\n" in text
    )
    assert "    id: 7\n    fields:" in text, "message keys follow the schema order"
    comments = [line.strip() for line in SCRAMBLED.splitlines() if "#" in line]
    for comment in comments:
        marker = comment[comment.index("#") :]
        assert marker in text, marker
    assert fmt.format_text(text) == text, "idempotent"


REQUIREMENTS = """\
messages:
  M:
    reqs: [HLR-1]
    fields:
      - { name: a, type: u8, reqs: [HLR-1], description: first }
    description: d
requirements:
  HLR-1:
    verification: test
    text: Text.
schema_version: 1
project: p
"""


def test_requirements_follow_the_canonical_order() -> None:
    text = fmt.format_text(REQUIREMENTS)
    assert text.splitlines()[:3] == ["schema_version: 1", "project: p", "requirements:"]
    assert "    text: Text.\n    verification: test\n" in text
    assert "    description: d\n    reqs: [HLR-1]\n    fields:" in text
    assert "description: first, reqs: [HLR-1] }" in text
    assert fmt.format_text(text) == text


def test_format_file(tmp_path: Path) -> None:
    path = tmp_path / "project.yaml"
    path.write_text(SCRAMBLED, encoding="utf-8")
    assert fmt.format_file(path, check=True)
    assert path.read_text(encoding="utf-8") == SCRAMBLED
    assert fmt.format_file(path)
    assert not fmt.format_file(path)


def test_examples_survive_round_trips(tmp_path: Path) -> None:
    copy = tmp_path / "example"
    shutil.copytree(EXAMPLES / "02_multi_interface_avionics", copy)
    for path in sorted(copy.rglob("*.yaml")):
        document = fmt.load(path.read_text(encoding="utf-8"))
        fmt.canonicalize(document)
        assert fmt.dump(document) == path.read_text(encoding="utf-8")
