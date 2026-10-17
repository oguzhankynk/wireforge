"""Differential testing: generated C++ against the reference codec.

The generated test project contains a harness executable per profile
(`<project>_diff[_<profile>]`). It reads one request per line from stdin and answers each with one
line:

- `M <message> <hex>`: deserialize the payload; answer `<decode status> <encode status> <hex>`,
  where the encode status and hex are those of serializing the decoded value again (`-` when the
  decode failed);
- `F <interface> <hex>`: send the bytes as one frame through a loopback link of the interface;
  answer `<frames> <errors> <hex>`, the dispatched frames, the rejected frames and the
  re-encoded payload of the last received message (`-` when none).

This module produces the inputs: random valid values, the boundary values of `wireforge.test_cases`,
random mutations of valid payloads and frames, and every stored regression. It computes the
expected answers with the reference codec, runs the harness and reports every difference. Inputs
that differ are written to the regression directory, which is replayed by every later run.
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from wireforge import expr, golden
from wireforge.codec import FLOAT32_MAX, CodecError, ReferenceCodec
from wireforge.expr import ExprError
from wireforge.loader import load_project
from wireforge.model import FLOAT_TYPES, Field, Interface, Project, Struct
from wireforge.test_cases import cases as boundary_cases

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from wireforge.codec import ChecksumPlugin

OK = "Ok"
FRAME_WORDS = 3
NONE = "-"
CHAR_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 _-"
MAX_EXTRA_BYTES = 4
BYTE_VALUES = 256
MASK64 = (1 << 64) - 1
UNIT = float(1 << 53)


class Rng:
    """Small deterministic xorshift64* generator (reproducible test inputs)."""

    def __init__(self, seed: int) -> None:
        """Start the sequence at `seed` (any integer)."""
        self.state = (seed * 0x9E3779B97F4A7C15 + 1) & MASK64 or 1

    def next(self) -> int:
        """Return the next 64-bit value."""
        x = self.state
        x ^= x >> 12
        x ^= (x << 25) & MASK64
        x ^= x >> 27
        self.state = x
        return (x * 0x2545F4914F6CDD1D) & MASK64

    def randrange(self, stop: int) -> int:
        """Return an integer in [0, stop)."""
        return self.next() % stop if stop > 0 else 0

    def randint(self, low: int, high: int) -> int:
        """Return an integer in [low, high]."""
        return low + self.randrange(high - low + 1)

    def random(self) -> float:
        """Return a float in [0, 1)."""
        return (self.next() >> 11) / UNIT

    def uniform(self, low: float, high: float) -> float:
        """Return a float in [low, high]."""
        return low + (high - low) * self.random()

    def choice(self, items: Sequence[str]) -> str:
        """Return one element of a non-empty sequence."""
        return items[self.randrange(len(items))]

    def byte(self) -> int:
        """Return a random byte value."""
        return self.randrange(BYTE_VALUES)


@dataclass(frozen=True)
class Request:
    """One harness input: `kind` is `M` (message payload) or `F` (frame)."""

    kind: str
    target: str
    data: bytes

    def line(self) -> str:
        """Return the request line sent to the harness."""
        return f"{self.kind} {self.target} {self.data.hex() or NONE}"

    @staticmethod
    def parse(line: str) -> Request:
        """Read a request line (as stored in regression files)."""
        kind, target, text = line.split()
        return Request(kind, target, b"" if text == NONE else bytes.fromhex(text))


@dataclass(frozen=True)
class Mismatch:
    """A request whose harness answer differs from the reference answer."""

    request: Request
    expected: str
    actual: str


class Harness(Protocol):
    """Runs a batch of requests and returns one answer line per request."""

    def __call__(self, requests: Sequence[Request]) -> list[str]:
        """Return the answers in request order."""
        ...


def process_harness(executable: Path) -> Harness:
    """Harness backed by a generated C++ executable."""

    def run(requests: Sequence[Request]) -> list[str]:
        text = "".join(request.line() + "\n" for request in requests)
        result = subprocess.run(
            [str(executable)], input=text, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            message = f"{executable} failed with {result.returncode}: {result.stderr}"
            raise RuntimeError(message)
        return result.stdout.splitlines()

    return run


def _f32(value: float) -> float:
    return float(struct.unpack(">f", struct.pack(">f", value))[0])


class RandomSampler:
    """Random valid values of the messages of one profile."""

    def __init__(self, codec: ReferenceCodec, rng: Rng) -> None:
        """Draw values for the profile of `codec` from `rng`."""
        self.codec = codec
        self.project = codec.project
        self.rng = rng
        self.bounds = golden.Sampler(codec).bounds

    def struct(self, fields: tuple[Field, ...]) -> dict[str, Any]:
        """Return a random value of a struct with these fields."""
        value: dict[str, Any] = {}
        for item in fields:
            if not self.codec.validator.present(item, self.codec.variant):
                continue
            if item.checksum is not None or item.const is not None or item.reserved:
                continue
            value[item.name] = self.field(item, value)
        return value

    def length(self, item: Field, value: dict[str, Any]) -> int:
        """Return a random element count (an expression count is evaluated instead)."""
        occurs = item.occurs
        if occurs is None:
            return 1
        variant = self.codec.variant
        capacity = self.codec.validator.static_value(occurs.max, variant) or 0
        count = occurs.count
        if isinstance(count, str) and not count.strip().isidentifier():
            scope = variant.scope
            values = dict(scope.values)
            values.update({k: int(v) for k, v in value.items() if isinstance(v, (bool, int))})
            try:
                return expr.evaluate(
                    expr.parse(count), type(scope)(values, scope.members, scope.sizes)
                )
            except ExprError:
                return 0
        if count is None and not occurs.fill:
            return capacity
        low = self.codec.validator.static_value(occurs.min, variant) if occurs.min else 0
        return self.rng.randint(low or 0, capacity)

    def field(self, item: Field, value: dict[str, Any]) -> object:
        """Return a random value of one field."""
        if item.occurs is None:
            return self._scalar_field(item)
        return self._array_field(item, value)

    def _scalar_field(self, item: Field) -> object:
        inner = self.project.types.get(item.type)
        if inner is not None:
            return self.struct(inner.fields)
        user = self.project.user_types.get(item.type)
        if user is not None:
            return bytes(self.rng.byte() for _ in range(user.size_bytes))
        return self.scalar(item)

    def _array_field(self, item: Field, value: dict[str, Any]) -> object:
        count = self.length(item, value)
        if item.type == "char":
            return "".join(self.rng.choice(CHAR_ALPHABET) for _ in range(count))
        inner = self.project.types.get(item.type)
        if inner is not None:
            return [self.struct(inner.fields) for _ in range(count)]
        user = self.project.user_types.get(item.type)
        if user is not None:
            return [bytes(self.rng.byte() for _ in range(user.size_bytes)) for _ in range(count)]
        return [self.scalar(item) for _ in range(count)]

    def scalar(self, item: Field) -> object:
        """Return a random value of a scalar inside its range."""
        enum = self.project.enums.get(item.type)
        if enum is not None:
            return self.rng.choice(sorted(enum.values))
        if item.type == "bool":
            return self.rng.randrange(2) == 1
        if item.type == "char":
            return self.rng.choice(CHAR_ALPHABET)
        low, high = self.bounds(item)
        if item.type in FLOAT_TYPES:
            number = self.rng.uniform(low, high)
            if item.type == "f32":
                number = min(max(_f32(number), -FLOAT32_MAX), FLOAT32_MAX)
                number = number if low <= number <= high else _f32(low)
            return number
        return self.rng.randint(int(low), int(high))


@dataclass(frozen=True)
class Expectation:
    """Reference answer of one request; `exact` is false when only acceptance is comparable."""

    answer: str
    exact: bool = True


class Oracle:
    """Reference answers of harness requests for one profile."""

    def __init__(self, codec: ReferenceCodec) -> None:
        """Answer requests with `codec`."""
        self.codec = codec

    def message(self, name: str, data: bytes) -> Expectation:
        """Answer an `M` request."""
        try:
            value = self.codec.decode_message(name, data)
        except CodecError as error:
            return Expectation(f"{error.status} {NONE} {NONE}")
        try:
            again = self.codec.encode_message(name, value)
        except CodecError as error:
            return Expectation(f"{OK} {error.status} {NONE}")
        return Expectation(f"{OK} {OK} {again.hex() or NONE}")

    def frame(self, interface: str, data: bytes) -> Expectation:
        """Answer an `F` request (frames, errors and payload of the last message).

        A rejected frame is only compared for not being dispatched cleanly: the stream decoder
        may consume it in other pieces than the reference codec, which sees one frame.
        """
        try:
            frame = self.codec.decode_frame(interface, data)
        except CodecError:
            return Expectation("rejected", exact=False)
        try:
            payload = self.codec.encode_message(frame.message, frame.value, interface).hex()
        except CodecError:
            payload = ""  # received, but the value does not encode (decode without range checks)
        return Expectation(f"1 0 {payload or NONE}")

    def expect(self, request: Request) -> Expectation:
        """Answer any request."""
        if request.kind == "M":
            return self.message(request.target, request.data)
        return self.frame(request.target, request.data)


def matches(expectation: Expectation, actual: str) -> bool:
    """Compare a harness answer with the reference answer."""
    if expectation.exact:
        return actual.strip() == expectation.answer
    words = actual.split()
    return len(words) == FRAME_WORDS and (words[0] == "0" or words[1] != "0")


def _extra(rng: Rng) -> bytes:
    return bytes(rng.byte() for _ in range(rng.randint(1, MAX_EXTRA_BYTES)))


def _replace_byte(data: bytes, rng: Rng) -> bytes:
    index = rng.randrange(len(data))
    return data[:index] + bytes([rng.byte()]) + data[index + 1 :]


def _flip_bit(data: bytes, rng: Rng) -> bytes:
    index = rng.randrange(len(data))
    return data[:index] + bytes([data[index] ^ (1 << rng.randrange(8))]) + data[index + 1 :]


def _truncate(data: bytes, rng: Rng) -> bytes:
    return data[: rng.randrange(len(data))]


def _extend(data: bytes, rng: Rng) -> bytes:
    return data + _extra(rng)


def _mutations(data: bytes, rng: Rng, count: int, *, extend: bool = True) -> Iterable[bytes]:
    """Random single mutations; `extend` allows appending bytes."""
    kinds = [_replace_byte, _flip_bit, _truncate] + ([_extend] if extend else [])
    for _ in range(count):
        if not data:
            yield _extra(rng)
            continue
        yield kinds[rng.randrange(len(kinds))](data, rng)


def _resyncs_blindly(interface: Interface) -> bool:
    """Return whether the decoder resynchronizes without sync bytes (finds frames in garbage)."""
    framing = interface.framing
    has_sync = any(item.role == "sync" for item in interface.header or ())
    return framing.kind != "none" and framing.resync and not has_sync


def _holds(codec: ReferenceCodec, condition: str | None) -> bool:
    if condition is None:
        return True
    try:
        return expr.evaluate(expr.parse_condition(condition), codec.variant.scope) != 0
    except ExprError:
        return True


class Generator:
    """Requests of one profile."""

    def __init__(self, project: Project, codec: ReferenceCodec, seed: int) -> None:
        """Draw requests for the profile of `codec`, reproducibly from `seed`."""
        self.project = project
        self.codec = codec
        self.rng = Rng(seed)
        self.sampler = RandomSampler(codec, self.rng)

    def messages(self) -> list[Struct]:
        """Messages that exist in this profile."""
        return [
            item for item in self.project.messages.values() if _holds(self.codec, item.condition)
        ]

    def payloads(self, message: Struct, count: int) -> list[bytes]:
        """Return the encodings of random valid values of a message."""
        encoded = (self.encode(message, self.sampler.struct(message.fields)) for _ in range(count))
        return [item for item in encoded if item is not None]

    def encode(self, message: Struct, value: dict[str, Any]) -> bytes | None:
        """Return the payload of `value`, or None when the reference codec rejects it."""
        try:
            return self.codec.encode_message(message.name, value)
        except CodecError:
            return None

    def encode_frame(self, interface: str, message: Struct) -> bytes | None:
        """Return a frame of a random value, or None when the reference codec rejects it."""
        try:
            return self.codec.encode_frame(
                interface, message.name, self.sampler.struct(message.fields)
            )
        except CodecError:
            return None

    def message_requests(self, count: int, mutations: int) -> list[Request]:
        """Return valid random payloads and their mutations, for every message."""
        requests = []
        for message in self.messages():
            for payload in self.payloads(message, count):
                requests.append(Request("M", message.name, payload))
                requests += [
                    Request("M", message.name, data)
                    for data in _mutations(payload, self.rng, mutations)
                ]
        return requests

    def frame_requests(self, count: int, mutations: int) -> list[Request]:
        """Return valid random frames of every interface message and their mutations."""
        requests = []
        for name, interface in sorted(self.project.interfaces.items()):
            for entry in interface.messages:
                message = self.project.messages[entry.name]
                if not _holds(self.codec, entry.condition) or not _holds(
                    self.codec, message.condition
                ):
                    continue
                for _ in range(count):
                    frame = self.encode_frame(name, message)
                    if frame is None:
                        continue
                    requests.append(Request("F", name, frame))
                    if _resyncs_blindly(interface):
                        continue
                    # Bytes after a complete frame start the next frame of a byte stream.
                    extend = interface.framing.kind == "none"
                    requests += [
                        Request("F", name, data)
                        for data in _mutations(frame, self.rng, mutations, extend=extend)
                    ]
        return requests


def boundary_requests(
    project: Project, plugins: dict[str, ChecksumPlugin] | None, profile: str | None
) -> list[Request]:
    """Payloads of the boundary and decode cases of `wireforge.test_cases`."""
    requests = []
    for profile_cases in boundary_cases(project, plugins):
        if profile_cases.profile != profile:
            continue
        for message in profile_cases.messages:
            name = message.message.name
            requests += [
                Request("M", name, case.encoded)
                for case in message.encodes
                if isinstance(case.encoded, bytes)
            ]
            requests += [Request("M", name, case.payload) for case in message.decodes]
    return requests


def read_regressions(directory: Path | None) -> list[Request]:
    """Every stored request below `directory` (one request per line, `#` starts a comment)."""
    if directory is None or not directory.exists():
        return []
    requests = []
    for path in sorted(directory.glob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.split("#", 1)[0].strip()
            if text:
                requests.append(Request.parse(text))
    return requests


def write_regressions(directory: Path, mismatches: Sequence[Mismatch]) -> Path | None:
    """Append the inputs of `mismatches` to `<directory>/found.txt`."""
    if not mismatches:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "found.txt"
    known = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
    lines = []
    for item in mismatches:
        line = item.request.line()
        if line not in known:
            known.add(line)
            lines.append(line)
    with path.open("a", encoding="utf-8") as stream:
        for line in lines:
            stream.write(line + "\n")
    return path


def write_fuzz_seeds(
    project: Project, directory: Path, plugins: dict[str, ChecksumPlugin] | None = None
) -> int:
    """Write the golden payloads and frames as libFuzzer seeds of the generated fuzz target.

    The first byte of a seed selects the target like the generated `fuzz_target.cpp`: messages
    in declaration order, then interfaces in name order. Returns the number of files written.
    """
    selectors = {name: index for index, name in enumerate(project.messages)}
    interfaces = {
        name: len(selectors) + index for index, name in enumerate(sorted(project.interfaces))
    }
    directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for vector in golden.vectors(project, plugins):
        profile = vector["profile"] or "default"
        stem = f"{profile}_{vector['interface']}_{vector['message']}_{vector['sample']}"
        seeds = {
            f"{stem}_payload": bytes([selectors[vector["message"]]])
            + bytes.fromhex(vector["payload"]),
            f"{stem}_frame": bytes([interfaces[vector["interface"]]])
            + bytes.fromhex(vector["frame"]),
        }
        for name, data in seeds.items():
            (directory / name).write_bytes(data)
            count += 1
    return count


@dataclass(frozen=True)
class Options:
    """Profile, stored inputs and amount of random input of one run."""

    seed: int = 1
    profile: str | None = None
    regressions: Path | None = None
    values: int = 50
    mutations: int = 8
    frames: int = 20


def run(
    project: Project,
    harness: Harness,
    options: Options | None = None,
    plugins: dict[str, ChecksumPlugin] | None = None,
) -> tuple[int, list[Mismatch]]:
    """Run every request of one profile; return the request count and the mismatches."""
    options = options or Options()
    profile, regressions = options.profile, options.regressions
    codec = ReferenceCodec(project, profile, plugins)
    generator = Generator(project, codec, options.seed)
    requests = [
        *read_regressions(regressions),
        *boundary_requests(project, plugins, profile),
        *generator.message_requests(options.values, options.mutations),
        *generator.frame_requests(options.frames, options.mutations),
    ]
    oracle = Oracle(codec)
    answers = harness(requests)
    mismatches = []
    for index, request in enumerate(requests):
        actual = answers[index] if index < len(answers) else "<no answer>"
        expected = oracle.expect(request)
        if not matches(expected, actual):
            mismatches.append(Mismatch(request, expected.answer, actual))
    return len(requests), mismatches


def main(argv: list[str] | None = None) -> int:
    """Command line: python -m wireforge.differential PROJECT [--harness EXE] [options]."""
    parser = argparse.ArgumentParser(prog="python -m wireforge.differential")
    parser.add_argument("project", type=Path)
    parser.add_argument("--harness", type=Path, help="generated diff executable")
    parser.add_argument("--fuzz-seeds", type=Path, help="write fuzz seeds to this directory")
    parser.add_argument("--profile")
    parser.add_argument("--plugins", type=Path, help="Python file defining checksum PLUGINS")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--values", type=int, default=50, help="random values per message")
    parser.add_argument("--mutations", type=int, default=8, help="mutations per valid input")
    parser.add_argument("--frames", type=int, default=20, help="random frames per message")
    parser.add_argument("--regressions", type=Path, help="directory of stored inputs")
    args = parser.parse_args(argv)
    result = load_project(args.project)
    if result.project is None:
        for issue in result.issues:
            sys.stdout.write(f"{issue}\n")
        return 1
    plugins = golden.load_plugins(args.plugins) if args.plugins else None
    if args.fuzz_seeds is not None:
        written = write_fuzz_seeds(result.project, args.fuzz_seeds, plugins)
        sys.stdout.write(f"{written} fuzz seeds in {args.fuzz_seeds}\n")
    if args.harness is None:
        return 0
    options = Options(
        seed=args.seed,
        profile=args.profile,
        regressions=args.regressions,
        values=args.values,
        mutations=args.mutations,
        frames=args.frames,
    )
    count, mismatches = run(result.project, process_harness(args.harness), options, plugins)
    for item in mismatches[:20]:
        sys.stdout.write(f"MISMATCH {item.request.line()}\n")
        sys.stdout.write(f"  expected {item.expected}\n  actual   {item.actual}\n")
    stored = write_regressions(args.regressions, mismatches) if args.regressions else None
    label = f" ({args.profile})" if args.profile else ""
    sys.stdout.write(f"differential{label}: {count} inputs, {len(mismatches)} mismatches\n")
    if stored is not None:
        sys.stdout.write(f"stored in {stored}\n")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
