"""WireForge expression language.

One parser feeds three back ends:
- `evaluate`: the Python reference semantics (64-bit signed, C division, overflow is an error);
- `to_cpp`: a C++17 expression in which every operand is widened to `std::int64_t`;
- `to_preprocessor`: an `#if` condition (preprocessor subset only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
MAX_SHIFT = 63

COMPARISONS = frozenset({"==", "!=", "<", "<=", ">", ">="})
LOGICAL = frozenset({"&&", "||"})
FUNCTIONS = {"min": 2, "max": 2, "ceil_div": 2, "sizeof": 1, "defined": 1}
# Binary operators from lowest to highest precedence (C rules).
BINARY_LEVELS: tuple[frozenset[str], ...] = (
    frozenset({"||"}),
    frozenset({"&&"}),
    frozenset({"|"}),
    frozenset({"^"}),
    frozenset({"&"}),
    frozenset({"==", "!="}),
    frozenset({"<", "<=", ">", ">="}),
    frozenset({"<<", ">>"}),
    frozenset({"+", "-"}),
    frozenset({"*", "/", "%"}),
)
UNARY = frozenset({"-", "~", "!"})

_TOKEN = re.compile(
    r"\s*(?:(?P<number>0[xX][0-9A-Fa-f]+|0[bB][01]+|[0-9]+)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<op><<|>>|<=|>=|==|!=|&&|\|\||[?:|^&<>+\-*/%~!(),.]))"
)


class ExprError(ValueError):
    """Syntax or evaluation error; `position` is a 0-based offset into the source when known."""

    def __init__(self, message: str, position: int | None = None) -> None:
        """Store the message and the optional source offset."""
        super().__init__(message if position is None else f"{message} (at offset {position})")
        self.message = message
        self.position = position


@dataclass(frozen=True)
class Int:
    """Integer literal (`true`/`false` are 1/0)."""

    value: int


@dataclass(frozen=True)
class Name:
    """Define, user constant or field name."""

    name: str


@dataclass(frozen=True)
class Member:
    """Enum member `Enum.member`."""

    enum: str
    member: str


@dataclass(frozen=True)
class Unary:
    """Unary operator: `-`, `~` or `!`."""

    op: str
    operand: Node


@dataclass(frozen=True)
class Binary:
    """Binary operator."""

    op: str
    left: Node
    right: Node


@dataclass(frozen=True)
class Ternary:
    """`condition ? then : otherwise`."""

    condition: Node
    then: Node
    otherwise: Node


@dataclass(frozen=True)
class Call:
    """Built-in function call; `defined` and `sizeof` take a single Name argument."""

    function: str
    args: tuple[Node, ...]


Node = Int | Name | Member | Unary | Binary | Ternary | Call


@dataclass(frozen=True)
class _Token:
    kind: str  # "number", "name", "op" or "end"
    text: str
    position: int


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    stripped_end = len(text.rstrip())
    while position < stripped_end:
        match = _TOKEN.match(text, position)
        if match is None or match.lastgroup is None:
            offset = len(text) - len(text[position:].lstrip())
            msg = f"unexpected character {text[offset]!r}"
            raise ExprError(msg, offset)
        kind = match.lastgroup
        tokens.append(_Token(kind, match.group(kind), match.start(kind)))
        position = match.end()
    tokens.append(_Token("end", "", len(text)))
    return tokens


def _literal(token: _Token) -> int:
    text = token.text
    if len(text) > 1 and text[0] == "0" and text[1].isdigit():
        msg = f"literal {text!r} has a leading zero (octal is not supported)"
        raise ExprError(msg, token.position)
    value = int(text, 0)
    if value > INT64_MAX:
        msg = f"literal {text} does not fit a 64-bit signed integer"
        raise ExprError(msg, token.position)
    return value


class _Parser:
    def __init__(self, text: str) -> None:
        self._tokens = _tokenize(text)
        self._index = 0

    def _peek(self) -> _Token:
        return self._tokens[self._index]

    def _next(self) -> _Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _expect(self, text: str) -> None:
        token = self._next()
        if token.text != text or token.kind != "op":
            msg = f"expected {text!r}, found {token.text or 'end of input'!r}"
            raise ExprError(msg, token.position)

    def parse(self) -> Node:
        node = self._ternary()
        token = self._peek()
        if token.kind != "end":
            msg = f"unexpected {token.text!r}"
            raise ExprError(msg, token.position)
        return node

    def _ternary(self) -> Node:
        condition = self._binary(0)
        if self._peek().text != "?":
            return condition
        self._next()
        then = self._ternary()
        self._expect(":")
        return Ternary(condition, then, self._ternary())

    def _binary(self, level: int) -> Node:
        if level == len(BINARY_LEVELS):
            return self._unary()
        left = self._binary(level + 1)
        while self._peek().kind == "op" and self._peek().text in BINARY_LEVELS[level]:
            op = self._next().text
            left = Binary(op, left, self._binary(level + 1))
        return left

    def _unary(self) -> Node:
        token = self._peek()
        if token.kind == "op" and token.text in UNARY:
            self._next()
            return Unary(token.text, self._unary())
        return self._primary()

    def _primary(self) -> Node:
        token = self._next()
        if token.kind == "number":
            return Int(_literal(token))
        if token.kind == "name":
            return self._named(token)
        if token.text == "(":
            node = self._ternary()
            self._expect(")")
            return node
        msg = f"unexpected {token.text or 'end of input'!r}"
        raise ExprError(msg, token.position)

    def _named(self, token: _Token) -> Node:
        if token.text in {"true", "false"}:
            return Int(1 if token.text == "true" else 0)
        following = self._peek().text
        if following == "(":
            return self._call(token)
        if following == ".":
            self._next()
            member = self._next()
            if member.kind != "name":
                msg = "expected an enum member name after '.'"
                raise ExprError(msg, member.position)
            return Member(token.text, member.text)
        return Name(token.text)

    def _call(self, token: _Token) -> Node:
        arity = FUNCTIONS.get(token.text)
        if arity is None:
            msg = f"unknown function {token.text!r}"
            raise ExprError(msg, token.position)
        self._expect("(")
        args = [self._ternary()]
        while self._peek().text == ",":
            self._next()
            args.append(self._ternary())
        self._expect(")")
        if len(args) != arity:
            msg = f"{token.text}() takes {arity} argument(s), got {len(args)}"
            raise ExprError(msg, token.position)
        if token.text in {"defined", "sizeof"} and not isinstance(args[0], Name):
            msg = f"{token.text}() takes a plain name"
            raise ExprError(msg, token.position)
        return Call(token.text, tuple(args))


def _check_condition(node: Node) -> None:
    """Reject anything outside the preprocessor subset allowed in `if:` conditions."""
    for item in walk(node):
        allowed = (
            isinstance(item, (Int, Name))
            or (isinstance(item, Call) and item.function == "defined")
            or (isinstance(item, Unary) and item.op in {"!", "-"})
            or (isinstance(item, Binary) and item.op in COMPARISONS | LOGICAL)
        )
        if not allowed:
            msg = f"{describe(item)} is not allowed in an if: condition"
            raise ExprError(msg)


def _check_value(node: Node) -> None:
    for item in walk(node):
        if isinstance(item, Call) and item.function == "defined":
            msg = "defined() is only allowed in if: conditions"
            raise ExprError(msg)


def parse(text: str) -> Node:
    """Parse a value expression (min, max, occurs, sizes); raises ExprError."""
    node = _Parser(text).parse()
    _check_value(node)
    return node


def parse_condition(text: str) -> Node:
    """Parse an `if:` condition (preprocessor subset); raises ExprError."""
    node = _Parser(text).parse()
    _check_condition(node)
    return node


def as_expression(value: int | str) -> Node:
    """Accept a model value that is either a YAML integer or an expression string."""
    if isinstance(value, bool):
        return Int(int(value))
    if isinstance(value, int):
        if not INT64_MIN <= value <= INT64_MAX:
            msg = f"{value} does not fit a 64-bit signed integer"
            raise ExprError(msg)
        return Int(value)
    return parse(value)


def describe(node: Node) -> str:
    """Short human-readable description of one node, for diagnostics."""
    if isinstance(node, (Unary, Binary)):
        return f"operator {node.op!r}"
    if isinstance(node, Call):
        return f"{node.function}()"
    if isinstance(node, Ternary):
        return "operator '?:'"
    if isinstance(node, Member):
        return f"enum member {node.enum}.{node.member}"
    return "literal" if isinstance(node, Int) else f"name {node.name!r}"


def _children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, Unary):
        return (node.operand,)
    if isinstance(node, Binary):
        return (node.left, node.right)
    if isinstance(node, Ternary):
        return (node.condition, node.then, node.otherwise)
    if isinstance(node, Call) and node.function not in {"defined", "sizeof"}:
        return node.args
    return ()


def walk(node: Node) -> Iterator[Node]:
    """Pre-order traversal; arguments of defined() and sizeof() are not values and are skipped."""
    yield node
    for child in _children(node):
        yield from walk(child)


def referenced_names(node: Node) -> set[str]:
    """Names used as values (defines, constants, fields)."""
    return {item.name for item in walk(node) if isinstance(item, Name)}


def call_arguments(node: Node, function: str) -> set[str]:
    """Names passed to `defined` or `sizeof`."""
    return {
        arg.name
        for item in walk(node)
        if isinstance(item, Call) and item.function == function
        for arg in item.args
        if isinstance(arg, Name)
    }


def referenced_members(node: Node) -> set[tuple[str, str]]:
    """Enum members used as `Enum.member`."""
    return {(item.enum, item.member) for item in walk(node) if isinstance(item, Member)}


# --- Evaluation -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """Values visible to an expression. `defined` lists macro names that exist without a value."""

    values: Mapping[str, int] = field(default_factory=dict)
    members: Mapping[tuple[str, str], int] = field(default_factory=dict)
    sizes: Mapping[str, int] = field(default_factory=dict)
    defined: frozenset[str] = frozenset()


def _checked(value: int) -> int:
    if not INT64_MIN <= value <= INT64_MAX:
        msg = f"64-bit overflow: result {value} is out of range"
        raise ExprError(msg)
    return value


def c_div(left: int, right: int) -> int:
    """C/C++ integer division: truncates toward zero; division by zero is an error."""
    if right == 0:
        msg = "division by zero"
        raise ExprError(msg)
    quotient = abs(left) // abs(right)
    return _checked(-quotient if (left < 0) != (right < 0) else quotient)


def c_mod(left: int, right: int) -> int:
    """C/C++ remainder: the sign follows the dividend."""
    return left - right * c_div(left, right)


def ceil_div(left: int, right: int) -> int:
    """Quotient rounded toward positive infinity."""
    quotient = c_div(left, right)
    exact = quotient * right == left
    return quotient if exact or (left < 0) != (right < 0) else _checked(quotient + 1)


def _shift_count(count: int) -> int:
    if not 0 <= count <= MAX_SHIFT:
        msg = f"shift count {count} is outside 0..{MAX_SHIFT}"
        raise ExprError(msg)
    return count


def _shift_left(left: int, right: int) -> int:
    if left < 0:
        msg = "left shift of a negative value"
        raise ExprError(msg)
    return _checked(left << _shift_count(right))


def _shift_right(left: int, right: int) -> int:
    if left < 0:
        msg = "right shift of a negative value"
        raise ExprError(msg)
    return left >> _shift_count(right)


_ARITHMETIC: dict[str, Callable[[int, int], int]] = {
    "+": lambda a, b: _checked(a + b),
    "-": lambda a, b: _checked(a - b),
    "*": lambda a, b: _checked(a * b),
    "/": c_div,
    "%": c_mod,
    "<<": _shift_left,
    ">>": _shift_right,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    "<=": lambda a, b: int(a <= b),
    ">": lambda a, b: int(a > b),
    ">=": lambda a, b: int(a >= b),
}

_UNARY: dict[str, Callable[[int], int]] = {
    "-": lambda a: _checked(-a),
    "~": lambda a: ~a,
    "!": lambda a: int(a == 0),
}

_FUNCTIONS: dict[str, Callable[[int, int], int]] = {"min": min, "max": max, "ceil_div": ceil_div}


def _lookup(mapping: Mapping[str, int], name: str, kind: str) -> int:
    if name not in mapping:
        msg = f"unknown {kind} {name!r}"
        raise ExprError(msg)
    return mapping[name]


def _evaluate_binary(node: Binary, scope: Scope) -> int:
    left = evaluate(node.left, scope)
    if node.op == "&&":
        return int(left != 0 and evaluate(node.right, scope) != 0)
    if node.op == "||":
        return int(left != 0 or evaluate(node.right, scope) != 0)
    return _ARITHMETIC[node.op](left, evaluate(node.right, scope))


def _evaluate_call(node: Call, scope: Scope) -> int:
    first = node.args[0]
    if node.function == "defined":
        name = first.name if isinstance(first, Name) else ""
        return int(name in scope.values or name in scope.defined)
    if node.function == "sizeof":
        return _lookup(scope.sizes, first.name if isinstance(first, Name) else "", "type")
    return _FUNCTIONS[node.function](evaluate(first, scope), evaluate(node.args[1], scope))


def _evaluate_member(node: Member, scope: Scope) -> int:
    key = (node.enum, node.member)
    if key not in scope.members:
        msg = f"unknown enum member {node.enum}.{node.member}"
        raise ExprError(msg)
    return scope.members[key]


def _evaluate_leaf(node: Int | Name | Member, scope: Scope) -> int:
    if isinstance(node, Int):
        return node.value
    if isinstance(node, Name):
        return _lookup(scope.values, node.name, "name")
    return _evaluate_member(node, scope)


def evaluate(node: Node, scope: Scope | None = None) -> int:
    """Evaluate with WireForge semantics (64-bit signed, C division, overflow is an error)."""
    active = scope or Scope()
    if isinstance(node, (Int, Name, Member)):
        return _evaluate_leaf(node, active)
    if isinstance(node, Unary):
        return _UNARY[node.op](evaluate(node.operand, active))
    if isinstance(node, Binary):
        return _evaluate_binary(node, active)
    if isinstance(node, Ternary):
        chosen = node.then if evaluate(node.condition, active) != 0 else node.otherwise
        return evaluate(chosen, active)
    return _evaluate_call(node, active)


# --- C++ emission ------------------------------------------------------------------------------


def _cpp_name(name: str) -> str:
    return f"static_cast<std::int64_t>({name})"


def _cpp_member(enum: str, member: str) -> str:
    return f"static_cast<std::int64_t>({enum}::{member})"


def _cpp_size(type_name: str) -> str:
    return f"static_cast<std::int64_t>({type_name}::kWireSize)"


@dataclass(frozen=True)
class CppNaming:
    """How names, enum members and sizeof() are spelled in generated C++."""

    name: Callable[[str], str] = _cpp_name
    member: Callable[[str, str], str] = _cpp_member
    size_of: Callable[[str], str] = _cpp_size


def _bool_to_cpp(text: str) -> str:
    return f"static_cast<std::int64_t>({text})"


def _binary_to_cpp(node: Binary, naming: CppNaming) -> str:
    left = to_cpp(node.left, naming)
    right = to_cpp(node.right, naming)
    if node.op in LOGICAL:
        return _bool_to_cpp(f"({left} != 0) {node.op} ({right} != 0)")
    if node.op in COMPARISONS:
        return _bool_to_cpp(f"{left} {node.op} {right}")
    return f"({left} {node.op} {right})"


def _call_to_cpp(node: Call, naming: CppNaming) -> str:
    first = node.args[0]
    if node.function == "sizeof" and isinstance(first, Name):
        return naming.size_of(first.name)
    if node.function == "defined":
        msg = "defined() has no C++ value form; use to_preprocessor"
        raise ExprError(msg)
    args = ", ".join(to_cpp(arg, naming) for arg in node.args)
    return f"wireforge::expr::{node.function}({args})"


def _unary_to_cpp(node: Unary, naming: CppNaming) -> str:
    operand = to_cpp(node.operand, naming)
    if node.op == "!":
        return _bool_to_cpp(f"{operand} == 0")
    return f"({node.op}{operand})"


def _leaf_to_cpp(node: Int | Name | Member, naming: CppNaming) -> str:
    if isinstance(node, Int):
        return f"std::int64_t{{{node.value}}}"
    if isinstance(node, Name):
        return naming.name(node.name)
    return naming.member(node.enum, node.member)


def to_cpp(node: Node, naming: CppNaming | None = None) -> str:
    """Render as a C++17 expression of type std::int64_t (needs <cstdint> and expr_support.hpp)."""
    active = naming or CppNaming()
    if isinstance(node, (Int, Name, Member)):
        return _leaf_to_cpp(node, active)
    if isinstance(node, Unary):
        return _unary_to_cpp(node, active)
    if isinstance(node, Binary):
        return _binary_to_cpp(node, active)
    if isinstance(node, Ternary):
        condition = to_cpp(node.condition, active)
        then = to_cpp(node.then, active)
        otherwise = to_cpp(node.otherwise, active)
        return f"({condition} != 0 ? {then} : {otherwise})"
    return _call_to_cpp(node, active)


# --- C emission --------------------------------------------------------------------------------


def _c_name(name: str) -> str:
    return f"((int64_t)({name}))"


def _c_member(enum: str, member: str) -> str:
    return f"((int64_t)({enum}_{member}))"


def _c_size(type_name: str) -> str:
    return f"((int64_t)({type_name}_MAX_WIRE_SIZE))"


@dataclass(frozen=True)
class CNaming:
    """How names, enum members and sizeof() are spelled in generated C."""

    name: Callable[[str], str] = _c_name
    member: Callable[[str, str], str] = _c_member
    size_of: Callable[[str], str] = _c_size


def _binary_to_c(node: Binary, naming: CNaming) -> str:
    left = to_c(node.left, naming)
    right = to_c(node.right, naming)
    if node.op in LOGICAL:
        return f"((int64_t)(({left} != 0) {node.op} ({right} != 0)))"
    if node.op in COMPARISONS:
        return f"((int64_t)({left} {node.op} {right}))"
    return f"({left} {node.op} {right})"


def _call_to_c(node: Call, naming: CNaming) -> str:
    first = node.args[0]
    if node.function == "sizeof" and isinstance(first, Name):
        return naming.size_of(first.name)
    if node.function == "defined":
        msg = "defined() has no C value form; use to_preprocessor"
        raise ExprError(msg)
    args = ", ".join(to_c(arg, naming) for arg in node.args)
    return f"wf_expr_{node.function}({args})"


def _unary_to_c(node: Unary, naming: CNaming) -> str:
    operand = to_c(node.operand, naming)
    if node.op == "!":
        return f"((int64_t)({operand} == 0))"
    return f"({node.op}{operand})"


def _leaf_to_c(node: Int | Name | Member, naming: CNaming) -> str:
    if isinstance(node, Int):
        return f"INT64_C({node.value})"
    if isinstance(node, Name):
        return naming.name(node.name)
    return naming.member(node.enum, node.member)


def to_c(node: Node, naming: CNaming | None = None) -> str:
    """Render as a C99 expression of type int64_t (needs <stdint.h> and wireforge/c/expr.h)."""
    active = naming or CNaming()
    if isinstance(node, (Int, Name, Member)):
        return _leaf_to_c(node, active)
    if isinstance(node, Unary):
        return _unary_to_c(node, active)
    if isinstance(node, Binary):
        return _binary_to_c(node, active)
    if isinstance(node, Ternary):
        condition = to_c(node.condition, active)
        then = to_c(node.then, active)
        otherwise = to_c(node.otherwise, active)
        return f"({condition} != 0 ? {then} : {otherwise})"
    return _call_to_c(node, active)


def to_preprocessor(node: Node) -> str:
    """Render an `if:` condition for `#if` (the node must come from parse_condition)."""
    _check_condition(node)
    return _preprocessor(node)


def _preprocessor(node: Node) -> str:
    if isinstance(node, Int):
        return str(node.value)
    if isinstance(node, Name):
        return node.name
    if isinstance(node, Call):
        return f"defined({_preprocessor(node.args[0])})"
    if isinstance(node, Unary):
        return f"({node.op}{_preprocessor(node.operand)})"
    if isinstance(node, Binary):
        return f"({_preprocessor(node.left)} {node.op} {_preprocessor(node.right)})"
    msg = f"{describe(node)} is not allowed in an if: condition"
    raise ExprError(msg)
