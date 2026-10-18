"""Operating-system neutrality of the runtime and the generated code.

The runtime and every generated header may include only standard headers that exist on a hosted
and on a freestanding-plus-library target alike (an allow list), and must not test for an operating
system (testing for a compiler is the compiler's business and is allowed). `<mutex>` is allowed
only where `WIREFORGE_NO_STD_MUTEX` guards it.
A user type or a user include (quoted include) is the project's own and is not checked.

Usage: python3 quality/check_os_neutral.py   (needs the Python package)
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from wireforge import generate, test_project

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "include"
ALLOWED_STD = {
    "algorithm",
    "array",
    "atomic",
    "cstddef",
    "cstdint",
    "cstring",
    "initializer_list",
    "iterator",
    "limits",
    "string_view",
    "type_traits",
    "utility",
}
# Needs WIREFORGE_NO_STD_MUTEX around it; allowed only in these files.
MUTEX_FILES = {"lock.hpp", "config.hpp"}
OS_MACROS = re.compile(
    r"\b(_WIN32|_WIN64|__linux__|__APPLE__|__ANDROID__|__unix__|__unix|__FreeRTOS__|"
    r"__MINGW32__|__CYGWIN__|configUSE_\w+|FREERTOS\w*)\b"
)
INCLUDE = re.compile(r"^\s*#\s*include\s*<([^>]+)>")
CONDITIONAL = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif)\b(.*)")


def scan_text(text: str, name: str) -> list[str]:
    """Return the findings (one line each) in the source text of the file `name`."""
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        include = INCLUDE.match(line)
        if include:
            header = include.group(1)
            if header.startswith("wireforge/"):
                continue
            if header == "mutex" and name in MUTEX_FILES:
                continue
            if header not in ALLOWED_STD:
                findings.append(f"{name}:{number}: <{header}> is not in the allow list")
            continue
        condition = CONDITIONAL.match(line)
        if condition:
            findings += [
                f"{name}:{number}: tests for {macro}"
                for macro in OS_MACROS.findall(condition.group(2))
            ]
    return findings


def _scan_tree(root: Path, patterns: tuple[str, ...]) -> list[str]:
    findings = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            findings += scan_text(text, path.name)
    return findings


def main() -> int:
    """Scan the runtime and the generated headers of every example."""
    findings = _scan_tree(RUNTIME, ("wireforge/*.hpp",))
    with tempfile.TemporaryDirectory() as work:
        generated = Path(work)
        for project in sorted(
            [*ROOT.glob("examples/*/project.yaml"), *ROOT.glob("protocols/*/project.yaml")]
        ):
            example = project.parent
            plugins = next(iter(sorted((example / "plugins").glob("[!_]*.py"))), None)
            issues = generate.generate(
                example, generated, test_project.TestOptions(plugins=plugins)
            )
            if issues:
                sys.stdout.write(f"{example}: {issues[0]}\n")
                return 1
        findings += _scan_tree(
            generated, ("*/model.hpp", "*/interfaces/*.hpp", "*/user_prelude.hpp")
        )
    for finding in findings:
        sys.stdout.write(f"{finding}\n")
    sys.stdout.write(
        f"os neutrality: {len(findings)} finding(s) in the runtime and the generated headers\n"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
