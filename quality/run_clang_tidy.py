"""Run clang-tidy over every project translation unit; any diagnostic (warning/error/note) fails."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DIAGNOSTIC = re.compile(r": (warning|error|note): ")


def run_one(build_dir: Path, source: str) -> tuple[str, str]:
    """Run clang-tidy on one source file and return its combined output."""
    result = subprocess.run(
        [
            shutil.which("clang-tidy") or "clang-tidy",
            "-p",
            str(build_dir),
            "--quiet",
            "--extra-arg=-Wno-unknown-warning-option",
            source,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return source, result.stdout + result.stderr


def main(argv: list[str]) -> int:
    """Entry point: returns a non-zero exit code when any diagnostic is reported."""
    build_dir = Path(argv[1] if len(argv) > 1 else "build/dev").resolve()
    entries = json.loads((build_dir / "compile_commands.json").read_text(encoding="utf-8"))
    sources = sorted({entry["file"] for entry in entries if "/_deps/" not in entry["file"]})
    failures = 0
    with ThreadPoolExecutor() as pool:
        for source, output in pool.map(lambda s: run_one(build_dir, s), sources):
            if DIAGNOSTIC.search(output):
                failures += 1
                sys.stdout.write(f"--- {source}\n{output}\n")
    sys.stdout.write(f"clang-tidy: {len(sources)} files, {failures} with findings\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
