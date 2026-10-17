"""No-heap evidence: the library symbols the runtime and the generated code need.

Every runtime header, every generated header of every example (per profile) and the generated
self-tests are compiled with `-fno-exceptions -fno-rtti -DWIREFORGE_NO_STD_MUTEX` at -O0 and -O2.
`nm` lists the undefined symbols of each object; any symbol outside a short allow list (compiler
runtime and string functions, plus printing in the self-tests) fails the check, so references to
the heap (operator new/delete, malloc family), exceptions (__cxa_throw, std::__throw_*), RTTI or
threads cannot slip through. The report (Markdown) lists every object and its external symbols.

Usage: python3 quality/check_no_heap.py [report.md]   (needs g++, nm and the Python package)
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wireforge import generate, test_project
from wireforge.loader import load_project

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "include"
FLAGS = ("-std=c++17", "-fno-exceptions", "-fno-rtti", "-DWIREFORGE_NO_STD_MUTEX", "-w")
LEVELS = ("-O0", "-O2")
# `nm -u` prints the symbol type and the (demangled) name.
NM_COLUMNS = 2
# Allowed only in the self-tests, which print their result.
TEST_ONLY = {"fputs", "fputc", "fwrite", "stdout", "strlen"}
# Always allowed: compiler runtime, string functions the optimizer may emit.
ALLOWED = {"__stack_chk_fail", "__cxa_pure_virtual", "memcpy", "memmove", "memset", "memcmp"}


@dataclass(frozen=True)
class Unit:
    """One translation unit to check."""

    label: str
    source: Path
    includes: tuple[Path, ...]
    defines: tuple[str, ...] = ()
    test: bool = False


@dataclass(frozen=True)
class Finding:
    """External symbols of one object file and the forbidden ones among them."""

    label: str
    level: str
    symbols: tuple[str, ...]
    forbidden: tuple[str, ...]


def _units(work: Path) -> list[Unit]:
    units = []
    checks = work / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    for header in sorted(RUNTIME.glob("wireforge/*.hpp")):
        source = checks / f"runtime_{header.stem}.cpp"
        source.write_text(f"#include <wireforge/{header.name}>\n", encoding="utf-8")
        units.append(Unit(f"runtime {header.name}", source, (RUNTIME,)))
    generated = work / "generated"
    for root in sorted(ROOT.glob("examples/*/project.yaml")):
        example = root.parent
        plugins = next(iter(sorted((example / "plugins").glob("[!_]*.py"))), None)
        options = test_project.TestOptions(plugins=plugins)
        issues = generate.generate(example, generated, options)
        if issues:
            message = f"{example}: {issues[0]}"
            raise RuntimeError(message)
        project = load_project(example).project
        if project is None:
            continue
        includes = (
            generated,
            RUNTIME,
            generated / project.name / "generated_tests",
            *(example / path for path, _ in project.include_dirs),
        )
        profiles = [
            (
                f" ({name})",
                (
                    *(f"{key}={value}" for key, value in profile.defines.items()),
                    f"WIREFORGE_PROFILE_{name}",
                ),
            )
            for name, profile in project.profiles.items()
        ] or [("", ())]
        files = ["model.hpp", *(f"interfaces/{name}.hpp" for name in sorted(project.interfaces))]
        for suffix, defines in profiles:
            for relative in files:
                stem = f"{project.name}_{relative.replace('/', '_')}{suffix.strip(' ()')}"
                source = checks / f"{stem}.cpp"
                source.write_text(f'#include "{project.name}/{relative}"\n', encoding="utf-8")
                units.append(Unit(f"{project.name}/{relative}{suffix}", source, includes, defines))
            units.append(
                Unit(
                    f"{project.name} selftest{suffix}",
                    generated / project.name / "generated_tests" / "selftest.cpp",
                    includes,
                    defines,
                    test=True,
                )
            )
    return units


def _check(unit: Unit, level: str, work: Path, compiler: str, nm: str) -> Finding:
    target = work / "objects" / f"{re.sub(r'[^A-Za-z0-9]+', '_', unit.label)}{level}.o"
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [compiler, *FLAGS, level, "-c", str(unit.source), "-o", str(target)]
    command += [f"-I{path}" for path in unit.includes] + [f"-D{define}" for define in unit.defines]
    subprocess.run(command, check=True)
    listing = subprocess.run(
        [nm, "-C", "-u", str(target)], check=True, capture_output=True, text=True
    )
    names = (line.split(maxsplit=1) for line in listing.stdout.splitlines() if line.strip())
    symbols = tuple(sorted({parts[1] for parts in names if len(parts) == NM_COLUMNS}))
    allowed = ALLOWED | (TEST_ONLY if unit.test else set())
    forbidden = tuple(symbol for symbol in symbols if symbol not in allowed)
    return Finding(unit.label, level, symbols, forbidden)


def report(findings: list[Finding]) -> str:
    """Markdown evidence report."""
    lines = [
        "# No-heap evidence",
        "",
        f"Flags: `{' '.join(FLAGS)}`, at {' and '.join(LEVELS)}.",
        "An object passes when it references no symbol outside the allowed list: "
        + ", ".join(f"`{name}`" for name in sorted(ALLOWED))
        + "; self-tests may also use "
        + ", ".join(f"`{name}`" for name in sorted(TEST_ONLY))
        + ".",
        "",
        "| Object | Level | External symbols | Result |",
        "|---|---|---|---|",
    ]
    for item in findings:
        symbols = ", ".join(f"`{symbol}`" for symbol in item.symbols) or "none"
        result = "FAIL: " + ", ".join(item.forbidden) if item.forbidden else "pass"
        lines.append(f"| {item.label} | {item.level} | {symbols} | {result} |")
    failed = sum(1 for item in findings if item.forbidden)
    lines += ["", f"{len(findings)} objects checked, {failed} failed."]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    """Compile, inspect and write the report; exit 1 when an object needs a forbidden symbol."""
    compiler = shutil.which("g++") or "g++"
    nm = shutil.which("nm") or "nm"
    output = Path(argv[1]) if len(argv) > 1 else ROOT / "build" / "evidence" / "no_heap.md"
    with tempfile.TemporaryDirectory(prefix="wireforge-noheap-") as temporary:
        work = Path(temporary)
        findings = [
            _check(unit, level, work, compiler, nm) for unit in _units(work) for level in LEVELS
        ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report(findings), encoding="utf-8")
    failed = [item for item in findings if item.forbidden]
    for item in failed:
        sys.stdout.write(f"FAIL {item.label} {item.level}: {', '.join(item.forbidden)}\n")
    sys.stdout.write(
        f"no-heap evidence: {len(findings)} objects, {len(failed)} failed -> {output}\n"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
