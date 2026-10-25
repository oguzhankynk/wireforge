"""Run the commands of the tutorial (docs/tutorial/) in order, in an empty directory.

A fenced `sh` block right after the line `<!-- tutorial: run -->` is run as shown; the commands of a
comment `<!-- tutorial: hidden ... -->` are run without being shown to the reader (the terminal
equivalent of a step done in an editor or a GUI). Blocks without a marker are not run (installs,
GUI tools, services). A chapter with `<!-- tutorial: needs TOOL ... -->` is skipped when one of the
tools is not on the PATH. Every chapter continues in the directory of the previous one, as the
reader does. `WIREFORGE_REPO` points the commands to this repository.

Usage: python quality/run_tutorial.py [--chapters 01,02,...] [--keep DIR] [--print]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TUTORIAL = ROOT / "docs" / "tutorial"
RUN = "<!-- tutorial: run -->"
HIDDEN = re.compile(r"<!-- tutorial: hidden\n(?P<body>.*?)\n-->", re.DOTALL)
FENCE = re.compile(r"```sh\n(?P<body>.*?)\n```", re.DOTALL)
NEEDS = re.compile(r"<!-- tutorial: needs (?P<tools>[^>]*?) -->")


def steps(text: str) -> list[str]:
    """Return the commands of one chapter in the order they appear."""
    found = [(match.start(), match.group("body")) for match in HIDDEN.finditer(text)]
    position = text.find(RUN)
    while position >= 0:
        block = FENCE.search(text, position)
        if block is None or text[position + len(RUN) : block.start()].strip():
            message = f"{RUN} at offset {position} is not followed by a ```sh block"
            raise ValueError(message)
        found.append((position, block.group("body")))
        position = text.find(RUN, block.end())
    return [body for _, body in sorted(found)]


def missing_tools(text: str) -> list[str]:
    """Return the tools a chapter needs that are not on the PATH."""
    needed = [tool for match in NEEDS.finditer(text) for tool in match.group("tools").split()]
    return [tool for tool in needed if shutil.which(tool) is None]


def script(chapters: set[str] | None = None) -> str:
    """Return the bash script of the tutorial (of the chapters numbered in `chapters`)."""
    parts = ["set -euo pipefail"]
    for chapter in sorted(TUTORIAL.glob("[0-9][0-9]_*.md")):
        if chapters is not None and chapter.name[:2] not in chapters:
            continue
        text = chapter.read_text(encoding="utf-8")
        missing = missing_tools(text)
        if missing:
            parts.append(f'echo "== {chapter.name}: skipped, not on the PATH: {" ".join(missing)}"')
            continue
        parts.append(f'echo "== {chapter.name}"')
        parts.extend(steps(text))
    return "\n".join(parts) + "\n"


def main() -> int:
    """Run (or print) the tutorial; return the exit code of the commands."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chapters", help="numbers of the chapters to run, e.g. 01,02,04,05")
    parser.add_argument("--keep", type=Path, help="run in this (new) directory and keep it")
    parser.add_argument(
        "--print", action="store_true", help="print the script instead of running it"
    )
    args = parser.parse_args()
    chapters = set(args.chapters.split(",")) if args.chapters else None
    text = script(chapters)
    if args.print:
        sys.stdout.write(text)
        return 0
    env = {**os.environ, "WIREFORGE_REPO": str(ROOT)}
    with tempfile.TemporaryDirectory(prefix="wireforge-tutorial-") as scratch:
        work = args.keep or Path(scratch)
        work.mkdir(parents=True, exist_ok=args.keep is None)
        bash = shutil.which("bash") or "/bin/bash"
        done = subprocess.run([bash, "-c", text], cwd=work, env=env, check=False)
    sys.stdout.write(
        "tutorial: all commands passed\n" if done.returncode == 0 else "tutorial: FAILED\n"
    )
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())
