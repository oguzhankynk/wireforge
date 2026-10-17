import itertools
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from wireforge import expr
from wireforge.expr import ExprError, Scope

RUNTIME_INCLUDE = Path(__file__).resolve().parents[2] / "runtime" / "include"

SCOPE = Scope(
    values={"A": 7, "B": -3, "BIG": expr.INT64_MAX, "ZERO": 0},
    members={("Color", "Red"): 1, ("Color", "Green"): 2},
    sizes={"Header": 6},
    defined=frozenset({"ONLY_DEFINED"}),
)

VALUE_CASES = [
    ("42", 42),
    ("0x2A", 42),
    ("0b101010", 42),
    ("0", 0),
    ("true + true", 2),
    ("false", 0),
    ("1 + 2 * 3", 7),
    ("(1 + 2) * 3", 9),
    ("10 - 4 - 3", 3),
    ("-7 / 2", -3),
    ("7 / -2", -3),
    ("-7 % 2", -1),
    ("7 % -2", 1),
    ("1 << 62", 2**62),
    ("BIG >> 60", 7),
    ("A & 3 | 8 ^ 1", 3 | 9),
    ("~0", -1),
    ("!0 + !5", 1),
    ("-B", 3),
    ("A > B && B < 0", 1),
    ("A == 7 || 1 / ZERO", 1),
    ("ZERO && 1 / ZERO", 0),
    ("A >= 7 == 1", 1),
    ("A != 7", 0),
    ("A <= 6", 0),
    ("ZERO ? 1 / ZERO : 5", 5),
    ("A ? B : 0", -3),
    ("1 ? 2 : 3 ? 4 : 5", 2),
    ("0 ? 2 : 0 ? 4 : 5", 5),
    ("min(A, B) + max(A, B)", 4),
    ("ceil_div(7, 2)", 4),
    ("ceil_div(8, 2)", 4),
    ("ceil_div(-7, 2)", -3),
    ("ceil_div(7, -2)", -3),
    ("ceil_div(-7, -2)", 4),
    ("sizeof(Header) * 2", 12),
    ("Color.Green - Color.Red", 1),
    ("BIG - 1 + 1", expr.INT64_MAX),
    ("-BIG - 1", expr.INT64_MIN),
    ("9223372036854775807", expr.INT64_MAX),
]


@pytest.mark.parametrize(("text", "expected"), VALUE_CASES)
def test_evaluate(text: str, expected: int) -> None:
    assert expr.evaluate(expr.parse(text), SCOPE) == expected


ERROR_CASES = [
    ("", "unexpected"),
    ("1 +", "unexpected"),
    ("(1", "expected ')'"),
    ("1 2", "unexpected '2'"),
    ("1 $ 2", "unexpected character '$'"),
    ("012", "leading zero"),
    ("9223372036854775808", "does not fit"),
    ("foo(1)", "unknown function"),
    ("min(1)", "takes 2 argument"),
    ("sizeof(1)", "plain name"),
    ("Color.", "enum member name"),
    ("defined(A)", "only allowed in if"),
    ("1 ? 2", "expected ':'"),
]


@pytest.mark.parametrize(("text", "fragment"), ERROR_CASES)
def test_syntax_errors(text: str, fragment: str) -> None:
    with pytest.raises(ExprError, match=re.escape(fragment)):
        expr.parse(text)


EVALUATION_ERRORS = [
    ("1 / ZERO", "division by zero"),
    ("1 % ZERO", "division by zero"),
    ("ceil_div(1, 0)", "division by zero"),
    ("BIG + 1", "overflow"),
    ("-BIG - 2", "overflow"),
    ("BIG * 2", "overflow"),
    ("1 << 63", "overflow"),
    ("1 << 64", "shift count"),
    ("1 >> -1", "shift count"),
    ("B << 1", "negative"),
    ("B >> 1", "negative"),
    ("(-BIG - 1) / -1", "overflow"),
    ("-(-BIG - 1)", "overflow"),
    ("UNKNOWN", "unknown name"),
    ("Color.Blue", "unknown enum member"),
    ("sizeof(Payload)", "unknown type"),
]


@pytest.mark.parametrize(("text", "fragment"), EVALUATION_ERRORS)
def test_evaluation_errors(text: str, fragment: str) -> None:
    with pytest.raises(ExprError, match=fragment):
        expr.evaluate(expr.parse(text), SCOPE)


CONDITION_CASES = [
    ("defined(A)", 1),
    ("defined(ONLY_DEFINED)", 1),
    ("defined(MISSING)", 0),
    ("!defined(MISSING) && A == 7", 1),
    ("A > 5 || B > 5", 1),
    ("(A < 0)", 0),
    ("B == -3", 1),
    ("1", 1),
]


@pytest.mark.parametrize(("text", "expected"), CONDITION_CASES)
def test_conditions(text: str, expected: int) -> None:
    assert expr.evaluate(expr.parse_condition(text), SCOPE) == expected


@pytest.mark.parametrize(
    "text", ["A + 1", "A ? 1 : 0", "min(A, 1)", "sizeof(Header)", "Color.Red == 1", "~A"]
)
def test_condition_subset_rejects(text: str) -> None:
    with pytest.raises(ExprError, match="not allowed in an if"):
        expr.parse_condition(text)
    with pytest.raises(ExprError, match="not allowed in an if"):
        expr.to_preprocessor(expr.parse(text))


def test_as_expression_accepts_yaml_values() -> None:
    assert expr.as_expression(5) == expr.Int(5)
    assert expr.as_expression(value=True) == expr.Int(1)
    assert expr.as_expression("A") == expr.Name("A")
    with pytest.raises(ExprError, match="does not fit"):
        expr.as_expression(2**64)


def test_reference_analysis() -> None:
    node = expr.parse("min(count, LIMIT) + sizeof(Header) + Color.Red")
    assert expr.referenced_names(node) == {"count", "LIMIT"}
    assert expr.call_arguments(node, "sizeof") == {"Header"}
    assert expr.referenced_members(node) == {("Color", "Red")}
    condition = expr.parse_condition("defined(PLATFORM_B) && LEVEL > 2")
    assert expr.referenced_names(condition) == {"LEVEL"}
    assert expr.call_arguments(condition, "defined") == {"PLATFORM_B"}


def test_describe_names_every_node_kind() -> None:
    node = expr.parse("A ? -B : Color.Red + min(1, 2)")
    descriptions = {expr.describe(item) for item in expr.walk(node)}
    assert descriptions >= {
        "operator '?:'",
        "operator '-'",
        "operator '+'",
        "enum member Color.Red",
        "min()",
        "name 'A'",
        "literal",
    }


def test_error_position_is_reported() -> None:
    with pytest.raises(ExprError) as info:
        expr.parse("1 + $")
    assert info.value.position == 4
    assert "offset 4" in str(info.value)


def test_cpp_rendering_shape() -> None:
    node = expr.parse("count * 2 + sizeof(Header)")
    assert expr.to_cpp(node) == (
        "((static_cast<std::int64_t>(count) * std::int64_t{2}) + "
        "static_cast<std::int64_t>(Header::kWireSize))"
    )
    naming = expr.CppNaming(name=lambda name: f"msg.{name}")
    assert expr.to_cpp(expr.parse("n"), naming) == "msg.n"
    with pytest.raises(ExprError, match="no C\\+\\+ value form"):
        expr.to_cpp(expr.parse_condition("defined(X)"))


def test_preprocessor_rendering_shape() -> None:
    node = expr.parse_condition("defined(PLATFORM_B) && !(LEVEL > -2)")
    assert expr.to_preprocessor(node) == "(defined(PLATFORM_B) && (!(LEVEL > (-2))))"


# --- Truth tables compiled with a real C++ compiler ---------------------------------------------

OPERANDS = ["0", "1", "-1", "7", "-7", "3", "63", "A", "B", "BIG", "(-BIG - 1)"]
OPERATORS = sorted(expr.BINARY_LEVELS[0].union(*expr.BINARY_LEVELS[1:]))


def _generated_cases() -> list[tuple[str, int]]:
    """Every binary operator over a grid of operands, plus the hand-written cases."""
    cases = list(VALUE_CASES)
    for op, left, right in itertools.product(OPERATORS, OPERANDS, OPERANDS):
        text = f"{left} {op} {right}"
        try:
            cases.append((text, expr.evaluate(expr.parse(text), SCOPE)))
        except ExprError:
            continue  # errors in Python must be compile errors in C++ (see the next test)
    extra = ("min(-7, 3) * ceil_div(BIG, 7)", "!A ? B : ~B", "(A | B) ^ (A & B)")
    cases.extend((text, expr.evaluate(expr.parse(text), SCOPE)) for text in extra)
    return cases


PRELUDE = """#include <cstddef>
#include <cstdint>
#include "wireforge/expr_support.hpp"
#define A 7
#define B (-3)
#define BIG 9223372036854775807
#define ZERO 0
#define ONLY_DEFINED
enum class Color : std::uint8_t { Red = 1, Green = 2 };
struct Header { static constexpr std::size_t kWireSize = 6; };
"""


def _compiler() -> str:
    found = shutil.which("g++") or shutil.which("clang++")
    if found is None:
        pytest.skip("no C++ compiler available")
    return found


def _compile(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "truth_table.cpp"
    path.write_text(PRELUDE + source, encoding="utf-8")
    flags = ["-std=c++17", "-fsyntax-only", "-Wall", "-Wextra", "-Wpedantic", "-Wconversion"]
    flags += ["-Wsign-conversion", "-Wold-style-cast", "-Wundef", "-Werror"]
    command = [_compiler(), *flags, f"-I{RUNTIME_INCLUDE}", str(path)]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _int64_literal(value: int) -> str:
    if value == expr.INT64_MIN:
        return "(-9223372036854775807 - 1)"
    return f"std::int64_t{{{value}}}" if value >= 0 else f"(-std::int64_t{{{-value}}})"


def test_cpp_matches_python_truth_table(tmp_path: Path) -> None:
    cases = _generated_cases()
    lines = [
        f'static_assert({expr.to_cpp(expr.parse(text))} == {_int64_literal(value)}, "{text}");'
        for text, value in cases
    ]
    result = _compile(tmp_path, "\n".join(lines) + "\n")
    assert result.returncode == 0, result.stderr[:4000]
    assert len(cases) > 1000


def test_preprocessor_matches_python_truth_table(tmp_path: Path) -> None:
    lines = []
    for text, expected in CONDITION_CASES:
        rendered = expr.to_preprocessor(expr.parse_condition(text))
        lines += [f"#if {rendered}", f'static_assert({expected} == 1, "{text}");']
        lines += ["#else", f'static_assert({expected} == 0, "{text}");', "#endif"]
    result = _compile(tmp_path, "\n".join(lines) + "\n")
    assert result.returncode == 0, result.stderr[:4000]


@pytest.mark.parametrize("text", ["BIG + 1", "4 << 62", "1 / ZERO", "(-BIG - 1) / -1"])
def test_python_errors_are_cpp_compile_errors(tmp_path: Path, text: str) -> None:
    source = f"constexpr std::int64_t kValue = {expr.to_cpp(expr.parse(text))};\n"
    assert _compile(tmp_path, source).returncode != 0
