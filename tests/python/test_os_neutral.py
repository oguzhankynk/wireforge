from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_os_neutral", ROOT / "quality" / "check_os_neutral.py"
)
assert SPEC is not None
assert SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def test_standard_headers_and_compiler_tests_pass() -> None:
    text = (
        '#include <array>\n#include <cstdint>\n#include "wireforge/span.hpp"\n'
        "#if defined(_MSC_VER)\n#endif\n"
    )
    assert CHECK.scan_text(text, "a.hpp") == []


def test_os_headers_and_os_macros_are_findings() -> None:
    text = (
        "#include <windows.h>\n#include <thread>\n"
        "#if defined(__linux__) && !defined(__APPLE__)\n#endif\n"
    )
    found = CHECK.scan_text(text, "a.hpp")
    assert len(found) == 4
    assert "<windows.h>" in found[0]
    assert "__linux__" in found[2]
    assert "__APPLE__" in found[3]


def test_mutex_is_allowed_only_in_the_lock_files() -> None:
    assert CHECK.scan_text("#include <mutex>\n", "lock.hpp") == []
    assert len(CHECK.scan_text("#include <mutex>\n", "link.hpp")) == 1


def test_the_runtime_and_the_generated_code_are_neutral() -> None:
    assert CHECK.main() == 0
