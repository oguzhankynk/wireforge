"""Random WireForge models, valid by construction, and their run through the whole chain.

`random_model(seed)` returns the plain dictionary of a `project.yaml`. The same seed gives the same
model on every platform (a private generator, no `random` module). The models use every kind of
field, the occurrence forms, bit groups, enums, nested structs, checksums, the framing kinds and the
header roles of the schema; they are small so that the generated test project builds in seconds.

`features(model)` lists the schema features a model uses, which `quality/feature_matrix.py` checks
against the matrix of features the models of a release have to cover.

The driver runs the seeds of a range through the chain: validate, generate the C++ code and the
GoogleTest project, build it standalone with the quality-policy warning flags, run it and measure
the coverage of the generated code. Anything below 100 percent of the lines, branches and
functions of the generated headers, any warning, any failing test and any invalid model is a
failure; the model and the log of a failure stay in `build/random-models/failures/<seed>/`, to be
reduced and added to tests/models/.

Usage: python3 quality/random_models.py [FIRST-LAST | SEED ...] [--jobs N]
       (default range 0-23; WIREFORGE_RANDOM_SEEDS gives the default range)
       python3 quality/random_models.py --write SEED DIRECTORY   (only writes the project.yaml)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from ruamel.yaml import YAML

if TYPE_CHECKING:
    from collections.abc import Sequence

T = TypeVar("T")
ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "random-models"
RANDOM_RANGE = (0, 23)  # the seeds the CI builds and measures

Model = dict[str, Any]

INTEGERS = ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64")
WIDTHS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64, "i8": 8, "i16": 16, "i32": 32, "i64": 64}
ENDIANS = ("big", "little")
BIT_ORDERS = ("msb_first", "lsb_first")
MAX_FRAME = 600
CHECKSUM_FIELDS = {"crc32": "u32", "crc16": "u16"}
BYTE = 8
MASK = 2**64 - 1
RANGE_SPAN = 10_000
SYNC = 0xA55A


class Rng:
    """A small deterministic generator (xorshift64*), identical on every platform."""

    def __init__(self, seed: int) -> None:
        """Start the sequence of `seed`."""
        self.state = (seed * 0x9E3779B97F4A7C15 + 0x1234567) & MASK or 1

    def next(self) -> int:
        """Return the next 64-bit value."""
        state = self.state
        state ^= state >> 12
        state ^= (state << 25) & MASK
        state ^= state >> 27
        self.state = state
        return (state * 0x2545F4914F6CDD1D) & MASK

    def below(self, bound: int) -> int:
        """Return a value in [0, bound)."""
        return (self.next() >> 11) % bound

    def randint(self, low: int, high: int) -> int:
        """Return a value in [low, high]."""
        return low + self.below(high - low + 1)

    def choice(self, items: Sequence[T]) -> T:
        """Return one of the items."""
        return items[self.below(len(items))]

    def percent(self, probability: int) -> bool:
        """Return True with the given probability in percent."""
        return self.below(100) < probability

    def weighted(self, weights: dict[str, int]) -> str:
        """Return a key with a probability proportional to its weight."""
        point = self.below(sum(weights.values()))
        for key, weight in weights.items():
            if point < weight:
                return key
            point -= weight
        return next(iter(weights))

    def shuffle(self, items: list[Any]) -> None:
        """Shuffle the list in place."""
        for index in range(len(items) - 1, 0, -1):
            other = self.below(index + 1)
            items[index], items[other] = items[other], items[index]


def _bounds(kind: str) -> tuple[int, int]:
    width = WIDTHS[kind]
    if kind.startswith("u"):
        return 0, 2**width - 1
    return -(2 ** (width - 1)), 2 ** (width - 1) - 1


class _Builder:
    """Builds one model; every method draws from the same generator."""

    def __init__(self, seed: int) -> None:
        self.rng = Rng(seed)
        self.enums: dict[str, dict[str, Any]] = {}
        self.structs: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, dict[str, Any]] = {}
        self.names = 0

    def name(self, prefix: str) -> str:
        self.names += 1
        return f"{prefix}{self.names}"

    # --- types ---------------------------------------------------------------------------

    def enum(self) -> str:
        name = f"E{len(self.enums) + 1}"
        values: dict[str, int] = {}
        used: set[int] = set()
        for index in range(self.rng.randint(2, 5)):
            number = self.rng.randint(0, 200)
            while number in used:
                number += 1
            used.add(number)
            values[f"v{index}"] = number
        self.enums[name] = {"type": self.rng.choice(("u8", "u16")), "values": values}
        return name

    def bit_fields(self) -> list[dict[str, Any]]:
        """Return bit-fields (a bit group) whose widths add up to a multiple of 8."""
        left = self.rng.choice((8, 16, 24, 32))
        fields: list[dict[str, Any]] = []
        while left > 0:
            bits = self.rng.randint(1, min(left, 2 * BYTE))
            left -= bits
            signed = bits > 1 and self.rng.percent(30)
            if bits == 1:
                kind = "bool"
            elif bits <= BYTE:
                kind = "i8" if signed else "u8"
            else:
                kind = "i16" if signed else "u16"
            field: dict[str, Any] = {"name": self.name("b"), "type": kind, "bits": bits}
            if kind != "bool" and self.rng.percent(20):
                field["reserved"] = True
            fields.append(field)
        return fields

    def struct(self) -> str:
        name = f"S{len(self.structs) + 1}"
        if self.rng.percent(40):
            fields = self.bit_fields()
        else:
            fields = [self.scalar(allow_struct=False) for _ in range(self.rng.randint(1, 3))]
        self.structs[name] = {"fields": self.legal(fields)}
        return name

    # --- fields --------------------------------------------------------------------------

    def integer(self, name: str) -> dict[str, Any]:
        kind = self.rng.choice(INTEGERS)
        low, high = _bounds(kind)
        field: dict[str, Any] = {"name": name, "type": kind}
        if self.rng.percent(50):
            span = min(high - low, RANGE_SPAN)
            minimum = low + self.rng.randint(0, high - low - span)
            maximum = min(minimum + self.rng.randint(1, max(1, span)), high)
            field["min"], field["max"] = minimum, maximum
            field["default"] = self.rng.randint(minimum, maximum)
        elif self.rng.percent(30):
            field["const"] = self.rng.randint(max(low, 0), min(high, 200))
        return field

    def scalar(self, *, allow_struct: bool = True) -> dict[str, Any]:
        """Return one plain field: an integer, bool, float, enum or struct."""
        name = self.name("f")
        kinds = {"integer": 50, "bool": 10, "float": 12, "enum": 10}
        if allow_struct:
            kinds["struct"] = 10
        kind = self.rng.weighted(kinds)
        if kind == "integer":
            return self.integer(name)
        if kind == "bool":
            return {"name": name, "type": "bool"}
        if kind == "float":
            field: dict[str, Any] = {"name": name, "type": self.rng.choice(("f32", "f64"))}
            if self.rng.percent(50):
                field.update({"min": -100.0, "max": 100.0, "default": 0.5})
            return field
        if kind == "enum":
            enum = self.rng.choice(sorted(self.enums)) if self.enums else self.enum()
            members = list(self.enums[enum]["values"])
            default = self.rng.choice(members) if self.rng.percent(50) else members[0]
            return {"name": name, "type": enum, "default": default}
        struct = self.rng.choice(sorted(self.structs)) if self.structs else self.struct()
        return {"name": name, "type": struct}

    def array(self) -> list[dict[str, Any]]:
        """Return a fixed array, a counted array or an optional field."""
        form = self.rng.weighted({"fixed": 40, "counted": 40, "optional": 20})
        if form == "fixed":
            kind = self.rng.choice(("u8", "u16", "i32", "f32", "char"))
            occurs = {"max": self.rng.randint(2, 6)}
            return [{"name": self.name("a"), "type": kind, "occurs": occurs}]
        if form == "counted":
            count = self.name("n")
            kind = self.rng.choice(("u8", "u16", "i16", "char"))
            return [
                {"name": count, "type": self.rng.choice(("u8", "u16")), "max": BYTE},
                {
                    "name": self.name("d"),
                    "type": kind,
                    "occurs": {"max": self.rng.randint(1, BYTE), "count": count},
                },
            ]
        present = self.name("has")
        return [
            {"name": present, "type": "bool"},
            {
                "name": self.name("o"),
                "type": self.rng.choice(("u8", "u32", "i16", "f32")),
                "occurs": {"max": 1, "count": present},
            },
        ]

    @staticmethod
    def legal(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop min/max/default/const from fields where they make no sense."""
        for field in fields:
            if field["type"] in {"char", "bool"} or "bits" in field:
                for key in ("min", "max", "default", "const"):
                    field.pop(key, None)
        return fields

    def message(self, identifier: int) -> dict[str, Any]:
        fields: list[dict[str, Any]] = []
        for _ in range(self.rng.randint(2, 6)):
            form = self.rng.weighted({"scalar": 55, "bits": 15, "array": 30})
            if form == "scalar":
                fields.append(self.scalar())
            elif form == "bits":
                if fields and "bits" in fields[-1]:  # two bit groups side by side would be one
                    fields.append({"name": self.name("sep"), "type": "u8"})
                fields.extend(self.bit_fields())
            else:
                fields.extend(self.array())
        if self.rng.percent(25):
            fields.append(
                {
                    "name": self.name("tail"),
                    "type": self.rng.choice(("u8", "u16", "i16", "u32", "f32", "char")),
                    "occurs": {"max": self.rng.randint(4, 12), "fill": True},
                }
            )
        return {"id": identifier, "fields": self.legal(fields)}

    # --- interfaces ----------------------------------------------------------------------

    def interface(self, kind: str, messages: list[str]) -> dict[str, Any]:
        interface: dict[str, Any] = {
            "version": "1.0.0",
            "direction": self.rng.choice(("tx", "rx", "both")),
        }
        if self.rng.percent(40):
            interface["endian"] = self.rng.choice(ENDIANS)
        if kind == "raw":
            interface["header"] = "none"
            interface["framing"] = {"kind": "none"}
            interface["messages"] = messages[:1]
            if self.rng.percent(30):
                interface["allow_trailing_bytes"] = True
            return interface
        header: list[dict[str, Any]] = []
        if kind == "sync_length":
            header.append({"name": "sync", "type": "u16", "const": SYNC, "role": "sync"})
        if self.rng.percent(30):
            header.append({"name": "proto", "type": "u8", "const": 1, "role": "protocol_version"})
        identifier = self.rng.choice(("u8", "u16"))
        header.append({"name": "msgId", "type": identifier, "role": "message_id"})
        if self.rng.percent(40):
            header.append({"name": "seq", "type": "u8", "role": "sequence"})
        length = self.rng.choice(("u8", "u16"))
        header.append({"name": "length", "type": length, "role": "payload_length"})
        interface["header"] = {"fields": header}
        checksum = self.rng.choice((None, "crc32", "crc16"))
        if checksum:
            interface["trailer"] = {
                "fields": [
                    {
                        "name": "crc",
                        "type": CHECKSUM_FIELDS[checksum],
                        "checksum": {"use": checksum, "covers": {"scope": "header_and_payload"}},
                    }
                ]
            }
        framing: dict[str, Any] = {"kind": kind, "max_frame_size": MAX_FRAME}
        if kind == "sync_length":
            framing["resync"] = self.rng.percent(70)
        interface["framing"] = framing
        interface["messages"] = list(messages)
        return interface


def random_model(seed: int) -> Model:
    """Return the dictionary of a valid `project.yaml` for `seed`."""
    builder = _Builder(seed)
    rng = builder.rng
    for _ in range(rng.randint(0, 2)):
        builder.enum()
    for _ in range(rng.randint(0, 2)):
        builder.struct()
    for index in range(rng.randint(1, 3)):
        builder.messages[f"M{index + 1}"] = builder.message(0x10 + index)
    names = list(builder.messages)
    kinds = ["raw", "sync_length", "length_prefixed"]
    rng.shuffle(kinds)
    interfaces = {
        f"if{number + 1}": builder.interface(kind, names)
        for number, kind in enumerate(kinds[: rng.randint(1, 3)])
    }
    model: Model = {
        "schema_version": 1,
        "project": f"rnd{seed}",
        "version": "0.1.0",
        "namespace": f"rnd::m{seed}",
        "defaults": {
            "endian": rng.choice(ENDIANS),
            "bit_order": rng.choice(BIT_ORDERS),
            "range_check": {"encode": rng.percent(50), "decode": rng.percent(50)},
        },
        "checksums": {
            "crc32": {"algo": "crc32"},
            "crc16": {
                "algo": "crc",
                "width": 16,
                "poly": 0x1021,
                "init": 0xFFFF,
                "refin": False,
                "refout": False,
                "xorout": 0,
            },
        },
        "messages": builder.messages,
        "interfaces": interfaces,
    }
    if builder.enums:
        model["enums"] = builder.enums
    if builder.structs:
        model["types"] = builder.structs
    return model


def _field_features(field: dict[str, Any]) -> set[str]:
    kind = field["type"]
    found = {
        f"type:{kind}" if kind in WIDTHS or kind in {"bool", "f32", "f64", "char"} else "type:named"
    }
    for key, feature in (
        ("bits", "bits"),
        ("reserved", "reserved"),
        ("const", "const"),
        ("min", "range"),
    ):
        if key in field:
            found.add(feature)
    occurs = field.get("occurs")
    if occurs and occurs.get("fill"):
        found.add("occurs:fill")
    elif occurs and "count" in occurs:
        found.add("occurs:optional" if occurs["max"] == 1 else "occurs:counted")
    elif occurs:
        found.add("occurs:fixed")
    return found


def _interface_features(interface: dict[str, Any]) -> set[str]:
    found = {f"framing:{interface['framing']['kind']}", f"direction:{interface['direction']}"}
    if interface["header"] != "none":
        found.add("header")
        found |= {f"role:{field['role']}" for field in interface["header"]["fields"]}
    if "trailer" in interface:
        found.add("trailer")
        found.add(f"checksum:{interface['trailer']['fields'][0]['checksum']['use']}")
    if interface["framing"].get("resync"):
        found.add("resync")
    if "endian" in interface:
        found.add("interface_endian")
    return found


def _type_features(model: Model) -> set[str]:
    found: set[str] = set()
    if model.get("enums"):
        found.add("enum")
    if model.get("types"):
        found.add("struct")
    for struct in model.get("types", {}).values():
        if all("bits" in field for field in struct["fields"]):
            found.add("bit_struct")
        for field in struct["fields"]:
            found |= _field_features(field)
    return found


def features(model: Model) -> set[str]:
    """Return the schema features a model uses, as short names."""
    defaults = model.get("defaults", {})
    found = {
        f"endian:{defaults.get('endian', 'big')}",
        f"bit_order:{defaults.get('bit_order', 'msb_first')}",
    } | _type_features(model)
    for message in model["messages"].values():
        for field in message["fields"]:
            found |= _field_features(field)
    for interface in model["interfaces"].values():
        found |= _interface_features(interface)
    if len(model["interfaces"]) > 1:
        found.add("multi_interface")
    if len(model["messages"]) > 1:
        found.add("multi_message")
    return found


def write_model(seed: int, directory: Path) -> Path:
    """Write the model of a seed as `project.yaml` into `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "project.yaml"
    with target.open("w", encoding="utf-8") as stream:
        YAML().dump(random_model(seed), stream)
    return target


@dataclass(frozen=True)
class Outcome:
    """The result of one seed."""

    seed: int
    ok: bool
    message: str


def _run(command: list[str], log: Path, env: dict[str, str] | None = None) -> bool:
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"$ {' '.join(command)}\n")
        stream.flush()
        result = subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT, check=False, env=env
        )
    return result.returncode == 0


def run_seed(seed: int) -> Outcome:
    """Run one seed through the chain; keep the project and the log of a failure."""
    directory = WORK / str(seed)
    shutil.rmtree(directory, ignore_errors=True)
    model_dir = directory / "model"
    write_model(seed, model_dir)
    log = directory / "log.txt"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "codegen")}
    steps: list[tuple[str, list[str]]] = [
        (
            "validate",
            [sys.executable, "-m", "wireforge.validate", str(model_dir)],
        ),
        (
            "generate",
            [
                sys.executable,
                "-m",
                "wireforge.generate",
                str(model_dir),
                "-o",
                str(directory / "out"),
                "--tests",
                "--coverage-threshold",
                "100",
            ],
        ),
    ]
    for name, command in steps:
        if not _run(command, log, env):
            return _keep(seed, directory, f"{name} failed")
    project = next((directory / "out").glob("*/generated_tests"))
    build = project / "build"
    configure = ["cmake", "-S", str(project), "-B", str(build), "-G", "Ninja"]
    configure += ["-DCMAKE_BUILD_TYPE=Debug", "-DWIREFORGE_COVERAGE=ON"]
    gtest = ROOT / "build" / "dev" / "_deps" / "googletest-src"
    if gtest.is_dir():
        configure.append(f"-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST={gtest}")
    for name, command in (
        ("configure", configure),
        ("build", ["cmake", "--build", str(build)]),
        ("coverage", ["cmake", "--build", str(build), "--target", "coverage"]),
    ):
        if not _run(command, log, env):
            return _keep(seed, directory, f"{name} failed")
    shutil.rmtree(directory)
    return Outcome(seed, ok=True, message="ok")


def _keep(seed: int, directory: Path, message: str) -> Outcome:
    failures = WORK / "failures" / str(seed)
    shutil.rmtree(failures, ignore_errors=True)
    failures.mkdir(parents=True)
    shutil.copy(directory / "model" / "project.yaml", failures / "project.yaml")
    shutil.copy(directory / "log.txt", failures / "log.txt")
    shutil.rmtree(directory, ignore_errors=True)
    return Outcome(seed, ok=False, message=message)


def seeds_of(arguments: list[str]) -> list[int]:
    """Expand `FIRST-LAST` ranges and single seeds."""
    seeds: list[int] = []
    for argument in arguments:
        if "-" in argument:
            first, last = argument.split("-", 1)
            seeds += list(range(int(first), int(last) + 1))
        else:
            seeds.append(int(argument))
    return seeds


def main() -> int:
    """Run the seeds in parallel and report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds", nargs="*", help="FIRST-LAST or single seeds")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    parser.add_argument("--write", nargs=2, metavar=("SEED", "DIRECTORY"))
    args = parser.parse_args()
    if args.write:
        sys.stdout.write(f"{write_model(int(args.write[0]), Path(args.write[1]))}\n")
        return 0
    seeds = seeds_of(
        args.seeds
        or [os.environ.get("WIREFORGE_RANDOM_SEEDS", f"{RANDOM_RANGE[0]}-{RANDOM_RANGE[1]}")]
    )
    WORK.mkdir(parents=True, exist_ok=True)
    failed = 0
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for outcome in pool.map(run_seed, seeds):
            if outcome.ok:
                sys.stdout.write(f"seed {outcome.seed}: ok\n")
            else:
                failed += 1
                sys.stdout.write(
                    f"seed {outcome.seed}: {outcome.message} "
                    f"(build/random-models/failures/{outcome.seed})\n"
                )
            sys.stdout.flush()
    sys.stdout.write(f"random models: {len(seeds) - failed} of {len(seeds)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
