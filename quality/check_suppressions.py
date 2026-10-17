"""Fail when a suppression exists that is not registered in quality/suppressions.md."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "quality" / "suppressions.md"

SCAN_DIRS = ("runtime", "codegen", "server", "mcp", "ui/src", "tests", "cmake")
SCAN_SUFFIXES = {".hpp", ".h", ".cpp", ".cc", ".py", ".ts", ".tsx", ".js", ".cmake", ".txt"}
SCAN_FILES = ("pyproject.toml", "CMakeLists.txt", "ui/eslint.config.js", "ui/tsconfig.json")

MARKERS: dict[str, re.Pattern[str]] = {
    "NOLINT": re.compile(r"\bNOLINT"),
    "cppcheck-suppress": re.compile(r"cppcheck-suppress"),
    "pragma-diagnostic": re.compile(r"#\s*pragma\s+(?:GCC|clang|warning)\b"),
    "coverage-exclude": re.compile(r"(?:LCOV|GCOVR)_EXCL"),
    "noqa": re.compile(r"#\s*noqa"),
    "type-ignore": re.compile(r"#\s*type:\s*ignore"),
    "no-cover": re.compile(r"pragma:\s*no cover"),
    "eslint-disable": re.compile(r"eslint-disable"),
    "ts-suppress": re.compile(r"@ts-(?:ignore|expect-error|nocheck)"),
    "per-file-ignores": re.compile(r"^\s*(?:\[tool\.ruff\.lint\.)?per-file-ignores", re.MULTILINE),
    "mypy-override": re.compile(r"\[\[tool\.mypy\.overrides\]\]|ignore_errors"),
}
CONFIG_OVERRIDE = "config-override"
MIN_REGISTRY_CELLS = 4


def candidate_files() -> list[Path]:
    """Return every file that may carry a suppression marker."""
    files: list[Path] = []
    for directory in SCAN_DIRS:
        base = ROOT / directory
        if base.is_dir():
            files.extend(
                path
                for path in base.rglob("*")
                if path.is_file() and (path.suffix in SCAN_SUFFIXES or path.name == ".clang-tidy")
            )
    files.extend(ROOT / name for name in SCAN_FILES if (ROOT / name).is_file())
    return sorted(set(files))


def scan() -> Counter[tuple[str, str]]:
    """Count suppression markers per (file, marker)."""
    found: Counter[tuple[str, str]] = Counter()
    for path in candidate_files():
        relative = path.relative_to(ROOT).as_posix()
        if path.name == ".clang-tidy" and relative != ".clang-tidy":
            found[(relative, CONFIG_OVERRIDE)] += 1
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in MARKERS.items():
            count = len(pattern.findall(text))
            if count:
                found[(relative, name)] += count
    root_ignore = ROOT / ".clang-tidy"
    if root_ignore.is_file() and "NOLINT" in root_ignore.read_text(encoding="utf-8"):
        found[(".clang-tidy", "NOLINT")] += 1
    return found


def registered() -> Counter[tuple[str, str]]:
    """Read the (file, marker) counts declared in the registry."""
    entries: Counter[tuple[str, str]] = Counter()
    for line in REGISTRY.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= MIN_REGISTRY_CELLS and re.fullmatch(r"S-\d+", cells[0]):
            entries[(cells[1], cells[2])] += int(cells[3])
    return entries


def main() -> int:
    """Compare code and registry; non-zero exit when they disagree."""
    found = scan()
    allowed = registered()
    problems = [
        f"unregistered suppression: {file} [{marker}] x{count - allowed.get((file, marker), 0)}"
        for (file, marker), count in sorted(found.items())
        if count > allowed.get((file, marker), 0)
    ]
    stale = [
        f"stale registry entry: {file} [{marker}]"
        for (file, marker) in sorted(allowed)
        if (file, marker) not in found
    ]
    for message in [*problems, *stale]:
        sys.stdout.write(message + "\n")
    sys.stdout.write(
        f"suppressions in code: {sum(found.values())}, registered: {sum(allowed.values())}\n"
    )
    return 1 if problems or stale else 0


if __name__ == "__main__":
    sys.exit(main())
