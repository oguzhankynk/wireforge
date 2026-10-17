"""Service layer shared by the CLI, the Python API, the server and the MCP server.

Every operation returns a `Result`: success flag, issues, JSON-compatible data and a human-readable
text. Operations never print and never exit; front ends decide how to present results.

Editing operations (`get`, `set_value`, `upsert`, `remove`) address model elements by JSON pointer
(`/messages/NavSolution/fields/2/max`). The first two segments locate the entry; the file that
defines it (root project file or fragment) is edited in place with comments, styles and key order
kept, then written in the canonical layout of `wireforge.fmt`, so tool edits and hand edits
produce minimal diffs. With `dry_run` nothing is written and the result carries the diff.
"""

from __future__ import annotations

import dataclasses
import difflib
import io
import json
import math
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from wireforge import (
    bundle,
    compat,
    fmt,
    generate,
    golden,
    importers,
    layout,
    lifecycle,
    schema,
    test_project,
    trace,
    vectors,
    versioning,
    wireshark,
)
from wireforge import results as test_results
from wireforge.codec import CodecError, ReferenceCodec
from wireforge.cpp_interface import InterfaceError
from wireforge.issues import Issue, errors_only
from wireforge.loader import SECTIONS, load_project, to_plain
from wireforge.validate import Validator

if TYPE_CHECKING:
    from wireforge.codec import ChecksumPlugin
    from wireforge.model import Project

SIMPLE_FLOW_KEYS = 8
# Pointer segments that name a section entry: /<section>/<name>.
ENTRY_DEPTH = 2


class ServiceError(Exception):
    """An operation could not run (bad argument, missing file or tool)."""


@dataclass
class Result:
    """Outcome of a service operation."""

    ok: bool
    issues: list[Issue] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the result as JSON-compatible data."""
        return {
            "ok": self.ok,
            "issues": [asdict(issue) for issue in self.issues],
            "data": self.data,
            "text": self.text,
        }


def _issues_text(issues: list[Issue]) -> str:
    return "".join(f"{issue}\n" for issue in issues)


def _root(location: Path) -> Path:
    return location / schema.ROOT_FILE_NAME if location.is_dir() else location


def _load_valid(location: Path) -> tuple[Project | None, list[Issue]]:
    result = load_project(location)
    if result.project is None:
        return None, result.issues
    issues = result.issues + Validator(result.project).run()
    if not errors_only(issues):
        plugins = load_plugins(find_plugins(location))
        issues += vectors.check(result.project, plugins)
    return (None if errors_only(issues) else result.project), issues


# --- validate, fmt, migrate ---------------------------------------------------------------------


def validate(location: Path) -> Result:
    """Load and validate a project (structural and semantic rules)."""
    if not _root(location).exists():
        message = f"{_root(location)}: no such project file"
        raise ServiceError(message)
    result = load_project(location)
    issues = list(result.issues)
    if result.project is not None:
        issues += Validator(result.project).run()
        if not errors_only(issues):
            issues += vectors.check(result.project, load_plugins(find_plugins(location)))
    ok = not errors_only(issues)
    files = result.project.files if result.project is not None else []
    text = _issues_text(issues) or f"{location}: OK\n"
    return Result(ok, issues, {"files": files}, text)


def project_files(location: Path) -> list[Path]:
    """Return the root file and every fragment it includes."""
    root = _root(location)
    if not root.exists():
        message = f"{root}: no such project file"
        raise ServiceError(message)
    result = load_project(location)
    if result.documents:
        return [Path(name) for name in result.documents]
    return [root]


def format_project(location: Path, *, check: bool = False) -> Result:
    """Canonical layout of every project file; with `check` only report files that would change."""
    changed = [str(path) for path in project_files(location) if fmt.format_file(path, check=check)]
    verb = "would reformat" if check else "reformatted"
    text = "".join(f"{verb} {name}\n" for name in changed) or "all files canonical\n"
    return Result(not (check and changed), data={"changed": changed}, text=text)


def migrate(location: Path, *, check: bool = False) -> Result:
    """Upgrade every project file to the current file format and refresh its stamps."""
    applied: dict[str, list[str]] = {}
    for path in project_files(location):
        document = fmt.load(path.read_text(encoding="utf-8"))
        if not isinstance(document, CommentedMap):
            continue
        steps = versioning.migrate_document(document)
        versioning.stamp(document)
        fmt.canonicalize(document)
        text = fmt.dump(document)
        if text != path.read_text(encoding="utf-8"):
            applied[str(path)] = steps
            if not check:
                path.write_text(text, encoding="utf-8")
    lines = [f"{name}: {', '.join(steps) or 'stamped'}\n" for name, steps in applied.items()]
    return Result(
        not (check and applied), data={"migrated": applied}, text="".join(lines) or "up to date\n"
    )


# --- generate, analyze, compat ------------------------------------------------------------------


def generate_code(
    location: Path, output: Path, tests: test_project.TestOptions | None = None
) -> Result:
    """Generate the C++ code (and with `tests` the GoogleTest project) below `output`."""
    issues = generate.generate(location, output, tests)
    result = load_project(location)
    name = result.project.name if result.project is not None else ""
    data = {"output": str(output), "project": name, "tests": tests is not None}
    text = _issues_text(issues) or f"generated {name} in {output}\n"
    return Result(not issues, issues, data, text)


def analyze(location: Path, profile: str | None = None, export: str = "markdown") -> Result:
    """Layout report of a valid project (`export`: markdown, csv or json)."""
    project, issues = _load_valid(location)
    if project is None:
        return Result(ok=False, issues=issues, text=_issues_text(issues))
    try:
        report = layout.analyze(project, profile)
    except layout.LayoutError as error:
        raise ServiceError(str(error)) from error
    text = layout.EXPORTERS[export](report)
    return Result(ok=True, issues=issues, data=report.to_dict(), text=text)


def compatibility(old: Path, new: Path, interfaces: list[str] | None = None) -> Result:
    """Compare two versions of a project; fails when a version bump is insufficient."""
    reports, issues = compat.compare_paths(old, new, interfaces)
    lines = [_issues_text(issues)]
    data = []
    for report in reports:
        lines.append(f"{report.summary()}\n")
        lines += [f"  {change}\n" for change in report.changes]
        data.append(
            {
                "interface": report.name,
                "old_version": report.old_version,
                "new_version": report.new_version,
                "required_bump": report.required_bump,
                "actual_bump": report.actual_bump,
                "version_ok": report.version_ok,
                "changes": [str(change) for change in report.changes],
            }
        )
    ok = not errors_only(issues) and all(report.version_ok for report in reports)
    return Result(ok, issues, {"interfaces": data}, "".join(lines))


# --- test ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildOptions:
    """How `run_tests` builds the generated test project."""

    build_dir: Path | None = None
    coverage: bool = False
    coverage_runtime: bool = False
    generator: str | None = None
    extra_cmake_args: tuple[str, ...] = ()


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def run_tests(
    location: Path,
    output: Path,
    tests: test_project.TestOptions | None = None,
    build: BuildOptions | None = None,
) -> Result:
    """Generate the code and the test project, build it with CMake and run its tests.

    With `build.coverage` the coverage target runs instead (tests plus the coverage threshold);
    `build.coverage_runtime` adds the runtime headers to the coverage report. The machine-readable
    results are written to `<build_dir>/results` (`wireforge docs --results <build_dir>`).
    """
    build = build or BuildOptions()
    cmake = shutil.which("cmake")
    if cmake is None:
        message = "cmake is not installed"
        raise ServiceError(message)
    generated = generate_code(location, output, tests or test_project.TestOptions())
    if not generated.ok:
        return generated
    project = output / generated.data["project"] / test_project.DIRECTORY
    build_dir = build.build_dir or project / "build" / ("coverage" if build.coverage else "test")
    configure = [cmake, "-S", str(project), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Debug"]
    if build.generator:
        configure += ["-G", build.generator]
    if build.coverage:
        configure.append("-DWIREFORGE_COVERAGE=ON")
    if build.coverage_runtime:
        configure.append("-DWIREFORGE_COVERAGE_RUNTIME=ON")
    configure += list(build.extra_cmake_args)
    steps = [configure, [cmake, "--build", str(build_dir)]]
    if build.coverage:
        steps.append([cmake, "--build", str(build_dir), "--target", "coverage"])
    else:
        ctest = shutil.which("ctest") or "ctest"
        steps.append([ctest, "--test-dir", str(build_dir), "--output-on-failure"])
    log = []
    for command in steps:
        completed = _run(command)
        log.append(completed.stdout + completed.stderr)
        if completed.returncode != 0:
            text = "".join(log)
            return Result(
                ok=False, data={"build_dir": str(build_dir), "failed": command}, text=text
            )
    data = {"build_dir": str(build_dir), "results_dir": str(build_dir / "results")}
    if build.coverage:
        log.append(f"results in {build_dir}\n")
    return Result(ok=True, data=data, text="".join(log))


# --- bundles ------------------------------------------------------------------------------------


def export_project(location: Path, output: Path) -> Result:
    """Write the single-file bundle of a valid project."""
    try:
        bundle.export_bundle(location, output)
    except bundle.BundleError as error:
        return Result(ok=False, issues=error.issues, text=f"{error}\n{_issues_text(error.issues)}")
    return Result(ok=True, data={"bundle": str(output)}, text=f"exported {output}\n")


def import_project(
    source: Path, destination: Path, *, verify: bool = True, single_file: bool = False
) -> Result:
    """Unpack a bundle into the standard layout and validate the result."""
    try:
        issues = bundle.import_bundle(source, destination, verify=verify, single_file=single_file)
    except bundle.BundleError as error:
        return Result(ok=False, issues=error.issues, text=f"{error}\n{_issues_text(error.issues)}")
    text = _issues_text(issues) or f"imported into {destination}\n"
    return Result(not errors_only(issues), issues, {"destination": str(destination)}, text)


# --- encode / decode ----------------------------------------------------------------------------


@dataclass(frozen=True)
class CodecTarget:
    """Which message (and interface/profile) a codec operation uses."""

    message: str | None = None
    interface: str | None = None
    profile: str | None = None
    plugins: dict[str, ChecksumPlugin] | None = None


def _codec(location: Path, target: CodecTarget) -> ReferenceCodec:
    project, issues = _load_valid(location)
    if project is None:
        raise ServiceError(_issues_text(issues))
    try:
        return ReferenceCodec(project, target.profile, target.plugins)
    except ValueError as error:
        raise ServiceError(str(error)) from error


def _jsonable(value: object) -> object:
    if isinstance(value, float) and math.isnan(value):
        return "NaN"
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def encode(location: Path, value: dict[str, Any], target: CodecTarget) -> Result:
    """Encode a message value (payload, and the frame when an interface is given)."""
    codec = _codec(location, target)
    message = target.message or ""
    try:
        payload = codec.encode_message(message, value, target.interface)
        frame = codec.encode_frame(target.interface, message, value) if target.interface else None
    except CodecError as error:
        return Result(
            ok=False, data={"status": error.status, "path": error.path}, text=f"{error}\n"
        )
    data: dict[str, Any] = {"payload": payload.hex()}
    if frame is not None:
        data["frame"] = frame.hex()
    return Result(ok=True, data=data, text=f"{(frame or payload).hex()}\n")


def decode(location: Path, data: bytes, target: CodecTarget) -> Result:
    """Decode a payload (with `message`) or a complete frame (with `interface` only)."""
    codec = _codec(location, target)
    try:
        if target.message is None:
            if target.interface is None:
                message = "decode needs a message or an interface"
                raise ServiceError(message)
            frame = codec.decode_frame(target.interface, data)
            decoded: dict[str, Any] = {
                "message": frame.message,
                "value": _jsonable(frame.value),
                "header": _jsonable(frame.header),
                "trailer": _jsonable(frame.trailer),
            }
        else:
            value = codec.decode_message(target.message, data, target.interface)
            decoded = {"message": target.message, "value": _jsonable(value)}
    except CodecError as error:
        return Result(
            ok=False, data={"status": error.status, "path": error.path}, text=f"{error}\n"
        )
    return Result(ok=True, data=decoded, text=json.dumps(decoded, indent=2) + "\n")


# --- editing ------------------------------------------------------------------------------------


def _segments(pointer: str) -> list[str]:
    if pointer in {"", "/"}:
        return []
    if not pointer.startswith("/"):
        message = f"{pointer!r} is not a JSON pointer"
        raise ServiceError(message)
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _owner(location: Path, segments: list[str]) -> Path:
    """File that defines the element at `segments` (or where a new section entry goes).

    A new entry goes to the file that already holds its section; when every holder holds just
    one entry and they share a directory (one file per entry, e.g. `interfaces/<name>.yaml`),
    it gets a new file `<directory>/<name>.yaml` in that directory instead.
    """
    files = project_files(location)
    if len(segments) < ENTRY_DEPTH or segments[0] not in SECTIONS:
        return files[0]
    section, name = segments[0], segments[1]
    holders: list[tuple[Path, int]] = []
    for path in files:
        document = fmt.load(path.read_text(encoding="utf-8"))
        entries = document.get(section) if isinstance(document, CommentedMap) else None
        if isinstance(entries, CommentedMap):
            if name in entries:
                return path
            holders.append((path, len(entries)))
    if not holders:
        return files[0]
    directories = {path.parent for path, _ in holders}
    one_each = all(count == 1 for _, count in holders) and len(holders) > 1
    if one_each and len(directories) == 1 and holders[0][0] != files[0]:
        return _new_fragment(location, holders[0][0].parent / f"{name}.yaml", section)
    return holders[0][0]


def _new_fragment(location: Path, path: Path, section: str) -> Path:
    """Return the path of a new fragment for `section` when the root's `include` covers it."""
    root = _root(location)
    document = fmt.load(root.read_text(encoding="utf-8"))
    patterns = document.get("include", []) if isinstance(document, CommentedMap) else []
    relative = path.relative_to(root.parent)
    if not any(relative.match(str(pattern)) for pattern in patterns):
        message = f"{relative} would not be included by {root.name}"
        raise ServiceError(message)
    del section
    return path


def _text(path: Path, segments: list[str]) -> str:
    """Return the text of a project file; a fragment that does not exist yet starts empty."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"schema_version: 1\n{segments[0]}: {{}}\n"


def _child(node: object, key: str) -> object:
    if isinstance(node, CommentedSeq):
        return node[int(key)]
    if isinstance(node, CommentedMap):
        return node[key]
    message = f"cannot index {type(node).__name__} with {key!r}"
    raise KeyError(message)


def _resolve(document: object, segments: list[str]) -> object:
    node = document
    for key in segments:
        node = _child(node, key)
    return node


def get(location: Path, pointer: str) -> Result:
    """Return the value at `pointer` as written in the project files."""
    segments = _segments(pointer)
    path = _owner(location, segments)
    document = fmt.load(path.read_text(encoding="utf-8"))
    try:
        value = to_plain(_resolve(document, segments))
    except (KeyError, IndexError, ValueError) as error:
        message = f"{pointer}: not found"
        raise ServiceError(message) from error
    return Result(ok=True, data={"file": str(path), "value": value}, text=json.dumps(value) + "\n")


def _flow_like(container: object) -> bool:
    """Whether new mappings in `container` should be written in flow style (like siblings)."""
    if isinstance(container, CommentedSeq):
        maps = [item for item in container if isinstance(item, CommentedMap)]
        return bool(maps) and all(item.fa.flow_style() for item in maps)
    return False


def _yamlify(value: object, *, flow: bool = False) -> object:
    """Convert plain data to ruamel nodes; small flat mappings use flow style when `flow`."""
    if isinstance(value, dict):
        node = CommentedMap()
        for key, item in value.items():
            node[key] = _yamlify(item)
        simple = all(not isinstance(item, (dict, list)) for item in value.values())
        if flow and simple and len(value) <= SIMPLE_FLOW_KEYS:
            node.fa.set_flow_style()
        return node
    if isinstance(value, list):
        sequence = CommentedSeq()
        flow_items = all(not isinstance(item, (dict, list)) for item in value)
        for item in value:
            sequence.append(_yamlify(item, flow=True))
        if flow_items:
            sequence.fa.set_flow_style()
        return sequence
    return value


def _merge(target: object, value: object) -> object:
    """Update `target` to equal `value`, keeping nodes (and their comments) that survive."""
    if isinstance(target, CommentedMap) and isinstance(value, dict):
        for key in [key for key in target if key not in value]:
            del target[key]
        for key, item in value.items():
            target[key] = _merge(target[key], item) if key in target else _yamlify(item)
        return target
    if isinstance(target, CommentedSeq) and isinstance(value, list):
        return _merge_sequence(target, value)
    if isinstance(value, (dict, list)):
        return _yamlify(value)
    if (
        not isinstance(target, (CommentedMap, CommentedSeq))
        and type(value) is not bool
        and target == value
    ):
        return target  # keep the written form, e.g. 0xA55A
    return value


def _merge_sequence(target: CommentedSeq, value: list[object]) -> CommentedSeq:
    named = {
        item.get("name"): item
        for item in target
        if isinstance(item, CommentedMap) and item.get("name") is not None
    }
    flow = _flow_like(target)
    items = []
    for index, item in enumerate(value):
        name = item.get("name") if isinstance(item, dict) else None
        if name is not None and name in named:
            items.append(_merge(named[name], item))
        elif name is None and index < len(target):
            items.append(_merge(target[index], item))
        else:
            items.append(_yamlify(item, flow=flow))
    for index in range(len(target) - 1, -1, -1):
        if all(target[index] is not item for item in items):
            del target[index]
    for index, item in enumerate(items):
        if index < len(target):
            if target[index] is not item:
                target.insert(index, item)
        else:
            target.append(item)
    while len(target) > len(items):
        del target[len(items)]
    return target


@dataclass(frozen=True)
class EditOptions:
    """How an edit is applied: `dry_run` writes nothing, `strict` refuses invalid results."""

    dry_run: bool = False
    strict: bool = False


def _diff(path: Path, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name}",
    )
    return "".join(lines)


def _write(location: Path, path: Path, document: object, options: EditOptions) -> Result:
    existed = path.exists()
    before = path.read_text(encoding="utf-8") if existed else ""
    fmt.canonicalize(document)
    after = fmt.dump(document)
    diff = _diff(path, before, after)
    data = {"file": str(path), "diff": diff, "written": False}
    if options.dry_run or after == before:
        return Result(ok=True, data=data, text=diff)
    path.write_text(after, encoding="utf-8")
    checked = validate(location)
    if options.strict and not checked.ok:
        if existed:
            path.write_text(before, encoding="utf-8")
        else:
            path.unlink()
        return Result(ok=False, issues=checked.issues, data=data, text=checked.text)
    data["written"] = True
    return Result(ok=True, issues=checked.issues, data=data, text=diff)


def set_value(
    location: Path, pointer: str, value: object, options: EditOptions | None = None
) -> Result:
    """Set the element at `pointer` (missing mappings on the way are created)."""
    options = options or EditOptions()
    segments = _segments(pointer)
    if not segments:
        message = "set needs a pointer below the document root"
        raise ServiceError(message)
    path = _owner(location, segments)
    original = _text(path, segments)
    document = fmt.load(original)
    parent: object = document
    for key in segments[:-1]:
        if isinstance(parent, CommentedMap) and key not in parent:
            parent[key] = CommentedMap()
        parent = _child(parent, key)
    last = segments[-1]
    if isinstance(parent, CommentedSeq):
        index = int(last) if last != "-" else len(parent)
        node = _yamlify(value, flow=_flow_like(parent))
        if index == len(parent):
            parent.append(node)
        else:
            parent[index] = _merge(parent[index], value)
    elif isinstance(parent, CommentedMap):
        parent[last] = _merge(parent[last], value) if last in parent else _yamlify(value)
    else:
        message = f"{pointer}: the parent is not a mapping or sequence"
        raise ServiceError(message)
    return _write(location, path, document, options)


def upsert(
    location: Path,
    section: str,
    name: str,
    definition: dict[str, Any],
    options: EditOptions | None = None,
) -> Result:
    """Create or update the entry `name` of `section` (types, messages, interfaces, ...)."""
    if section not in SECTIONS:
        message = f"unknown section {section!r}; choose one of {', '.join(SECTIONS)}"
        raise ServiceError(message)
    return set_value(location, f"/{section}/{name}", definition, options)


def _delete(parent: object, key: str) -> None:
    if isinstance(parent, CommentedSeq):
        del parent[int(key)]
    elif isinstance(parent, CommentedMap):
        del parent[key]
    else:
        raise KeyError(key)


def remove(location: Path, pointer: str, options: EditOptions | None = None) -> Result:
    """Delete the element at `pointer`."""
    options = options or EditOptions()
    segments = _segments(pointer)
    if not segments:
        message = "remove needs a pointer below the document root"
        raise ServiceError(message)
    path = _owner(location, segments)
    document = fmt.load(path.read_text(encoding="utf-8"))
    try:
        _delete(_resolve(document, segments[:-1]), segments[-1])
    except (KeyError, IndexError, ValueError) as error:
        message = f"{pointer}: not found"
        raise ServiceError(message) from error
    return _write(location, path, document, options)


def load_plugins(path: Path | None) -> dict[str, ChecksumPlugin] | None:
    """Load checksum plugins of the reference codec (None without a file)."""
    return golden.load_plugins(path) if path is not None else None


# --- archives and previews ----------------------------------------------------------------------

ARCHIVE_README = """\
# {name}: generated code

Generated by WireForge {version}. Layout:

- `include/{name}/`: generated C++ headers (include paths: `include/`, `runtime/include/`);
- `include/{name}/generated_tests/`: GoogleTest project with coverage target, self-test, harnesses;
- `runtime/include/wireforge/`: the WireForge runtime (header-only);
- `model/`: the project definition (and its `user_includes` directories).

Build and run the tests:

```sh
cmake -S include/{name}/generated_tests -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
ctest --test-dir build --output-on-failure
```
"""


def find_plugins(location: Path) -> Path | None:
    """Return the checksum plugin file of a project (`plugins/*.py` defining PLUGINS), if any."""
    directory = (location if location.is_dir() else location.parent) / "plugins"
    for path in sorted(directory.glob("*.py")) if directory.is_dir() else []:
        if path.name != "__init__.py" and "PLUGINS" in path.read_text(encoding="utf-8"):
            return path
    return None


def generate_archive(location: Path, *, tests: bool = True) -> tuple[Result, bytes]:
    """Return the zip of generated code, test project, runtime and model (self-contained)."""
    with tempfile.TemporaryDirectory(prefix="wireforge-") as temporary:
        root = Path(temporary)
        model = root / "model"
        shutil.copytree(location if location.is_dir() else location.parent, model)
        runtime = root / "runtime" / "include"
        shutil.copytree(test_project.RUNTIME_INCLUDE, runtime)
        options = test_project.TestOptions(plugins=find_plugins(model), runtime=runtime)
        result = generate_code(model, root / "include", options if tests else None)
        if not result.ok:
            return result, b""
        name = str(result.data["project"])
        (root / "README.md").write_text(
            ARCHIVE_README.format(name=name, version=versioning.TOOL_VERSION), encoding="utf-8"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, f"{name}/{path.relative_to(root).as_posix()}")
        data = buffer.getvalue()
    result.data["bytes"] = len(data)
    return result, data


def preview(location: Path) -> Result:
    """Return the generated C++ files of a valid project (path -> text), writing nothing."""
    project, issues = _load_valid(location)
    if project is None:
        return Result(ok=False, issues=issues, text=_issues_text(issues))
    try:
        content_hash = bundle.build_bundle(location)["bundle"]["content_hash"]
        files = generate.render(project, content_hash)
    except (bundle.BundleError, InterfaceError) as error:
        return Result(ok=False, issues=issues, text=f"{error}\n")
    return Result(ok=True, issues=issues, data={"files": files}, text="".join(files))


def wireshark_plugin(location: Path) -> tuple[Result, str]:
    """Return the Wireshark Lua plugin of a valid project (and its protocol names in `data`)."""
    plugin = wireshark.build(location)
    if plugin.issues:
        return Result(ok=False, issues=plugin.issues, text=_issues_text(plugin.issues)), ""
    data = {"project": plugin.project, "protocols": list(plugin.protocols)}
    text = (
        f"Wireshark plugin of {plugin.project}: {', '.join(plugin.protocols) or 'no protocols'}\n"
    )
    return Result(ok=True, data=data, text=text), plugin.text


def wireshark_dissector(location: Path, output: Path) -> Result:
    """Write the Wireshark Lua plugin of a valid project to `output`."""
    result, text = wireshark_plugin(location)
    if result.ok:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        result.data["output"] = str(output)
        result.text = f"wrote {output} ({', '.join(result.data['protocols'])})\n"
    return result


# --- version history ----------------------------------------------------------------------------


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        message = "git is not installed"
        raise ServiceError(message)
    return _run([git, "-C", str(directory), *args])


def history(location: Path, limit: int = 50) -> Result:
    """Return the commits that changed the project directory (none outside a git repository)."""
    directory = location if location.is_dir() else location.parent
    completed = _git(
        directory, "log", f"-{limit}", "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s", "--", "."
    )
    if completed.returncode != 0:
        return Result(ok=True, data={"commits": []}, text="not a git repository\n")
    commits = []
    for line in completed.stdout.splitlines():
        full, short, author, date, subject = line.split("\x1f")
        commits.append(
            {"commit": full, "short": short, "author": author, "date": date, "subject": subject}
        )
    text = "".join(f"{item['short']} {item['date']} {item['subject']}\n" for item in commits)
    return Result(ok=True, data={"commits": commits}, text=text)


def compatibility_with_revision(
    location: Path, revision: str, interfaces: list[str] | None = None
) -> Result:
    """Compare the project at a git revision (old) with the working copy (new)."""
    directory = (location if location.is_dir() else location.parent).resolve()
    top = _git(directory, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        message = f"{directory} is not in a git repository"
        raise ServiceError(message)
    root = Path(top.stdout.strip())
    relative = directory.relative_to(root).as_posix() or "."
    with tempfile.TemporaryDirectory(prefix="wireforge-rev-") as temporary:
        archive = Path(temporary) / "old.tar"
        exported = _git(root, "archive", "--format=tar", "-o", str(archive), revision, relative)
        if exported.returncode != 0:
            message = f"cannot read {relative} at {revision}: {exported.stderr.strip()}"
            raise ServiceError(message)
        with tarfile.open(archive) as tar:
            tar.extractall(temporary, filter="data")
        result = compatibility(Path(temporary) / relative, directory, interfaces)
    result.data["revision"] = revision
    return result


# --- importers ----------------------------------------------------------------------------------


def _flow_field(item: object) -> None:
    """Write a field like the examples: nested mappings inline, the field inline when flat."""
    if not isinstance(item, CommentedMap):
        return
    nested = [value for value in item.values() if isinstance(value, CommentedMap)]
    for value in nested:
        value.fa.set_flow_style()
    if not nested:
        item.fa.set_flow_style()


def _document_text(document: dict[str, Any]) -> str:
    if isinstance(document.get("version"), str):
        document = {**document, "version": DoubleQuotedScalarString(document["version"])}
    node = _yamlify(document)
    enums = node.get("enums") if isinstance(node, CommentedMap) else None
    for enum in enums.values() if isinstance(enums, CommentedMap) else []:
        values = enum.get("values") if isinstance(enum, CommentedMap) else None
        if isinstance(values, CommentedMap):
            values.fa.set_flow_style()
    for section in ("types", "messages"):
        entries = node.get(section) if isinstance(node, CommentedMap) else None
        for entry in entries.values() if isinstance(entries, CommentedMap) else []:
            fields = entry.get("fields") if isinstance(entry, CommentedMap) else None
            for item in fields if isinstance(fields, CommentedSeq) else []:
                _flow_field(item)
    fmt.canonicalize(node)
    return fmt.dump(node)


def import_icd(
    source: Path,
    destination: Path,
    options: importers.Project,
    messages: list[str] | None = None,
) -> Result:
    """Import an ICD table (.csv, .xlsx) or a C header (.h) as a new project in `destination`."""
    if destination.exists() and any(destination.iterdir()):
        message = f"{destination} is not empty"
        raise ServiceError(message)
    suffix = source.suffix.lower()
    try:
        if suffix == ".csv":
            document = importers.from_csv(source.read_text(encoding="utf-8"), options, str(source))
        elif suffix in {".xlsx", ".xlsm"}:
            document = importers.from_xlsx(source, options)
        elif suffix in {".h", ".hpp", ".hh"}:
            text = source.read_text(encoding="utf-8")
            document = importers.from_c_header(text, options, messages, str(source))
        else:
            message = f"{source.name}: import reads .csv, .xlsx or .h files"
            raise ServiceError(message)
    except importers.IcdImportError as error:
        return Result(ok=False, issues=error.issues, text=_issues_text(error.issues))
    destination.mkdir(parents=True, exist_ok=True)
    root = destination / schema.ROOT_FILE_NAME
    root.write_text(_document_text(document), encoding="utf-8")
    checked = validate(destination)
    checked.data["project"] = str(root)
    checked.text = _issues_text(checked.issues) or f"imported {source.name} into {root}\n"
    return checked


# --- traceability -------------------------------------------------------------------------------


def trace_report(location: Path, export: str = "markdown") -> Result:
    """Trace every model element to its source line, generated symbol and tests."""
    project, issues = _load_valid(location)
    if project is None:
        return Result(ok=False, issues=issues, text=_issues_text(issues))
    content_hash = bundle.build_bundle(location)["bundle"]["content_hash"]
    traced = trace.report(project, content_hash, load_plugins(find_plugins(location)))
    if export == "csv":
        text = trace.to_csv(traced.rows)
    else:
        text = trace.to_markdown(traced.rows, project, content_hash, traced.llrs)
    data = {
        "content_hash": content_hash,
        "rows": [asdict(row) for row in traced.rows],
        "llrs": [asdict(row) for row in traced.llrs],
    }
    return Result(ok=True, issues=issues, data=data, text=text)


# --- lifecycle documents ------------------------------------------------------------------------


def _directory(location: Path) -> Path:
    return location if location.is_dir() else location.parent


def _lifecycle(
    location: Path, *, strict: bool, results_dir: Path | None = None
) -> tuple[Result, dict[str, str]]:
    project, issues = _load_valid(location)
    if project is None:
        return Result(ok=False, issues=issues, text=_issues_text(issues)), {}
    content_hash = bundle.build_bundle(location)["bundle"]["content_hash"]
    context = lifecycle.context(project, content_hash, load_plugins(find_plugins(location)))
    if results_dir is not None:
        try:
            context = dataclasses.replace(
                context,
                results=test_results.load(results_dir),
                deviations=tuple(test_results.load_deviations(_directory(location))),
            )
        except test_results.ResultsError as error:
            raise ServiceError(str(error)) from error
    files = lifecycle.render(context)
    findings = lifecycle.strict_findings(context)
    found = [
        Issue(project.source.file, None, item.pointer, f"[{item.rule}] {item.message}")
        for item in findings
    ]
    data = {
        "files": sorted(files),
        "strict": [{"rule": item.rule, "message": item.message} for item in findings],
        "summary": lifecycle.summary(context),
    }
    ok = not (strict and findings)
    result = Result(ok=ok, issues=[*issues, *found] if strict else issues, data=data)
    return result, files


def lifecycle_docs(
    location: Path, output: Path, *, strict: bool = False, results: Path | None = None
) -> Result:
    """Write the requirements, design, test and traceability documents to `output`.

    Only files with the names of the documents are written; other files are left alone. With
    `strict` the result fails when a review would reject the model (rules DS-1 to DS-4). With
    `results` (the build directory of a test run) the documents include the test results,
    coverage, stack use and toolchain, and strict also applies rules DS-5 to DS-7.
    """
    result, files = _lifecycle(location, strict=strict, results_dir=results)
    if not files:
        return result
    output.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (output / name).write_text(text, encoding="utf-8", newline="\n")
    lines = [f"wrote {len(files)} documents to {output}\n"]
    lines += [f"{item.message}\n" for item in result.issues if "[DS-" in item.message]
    result.text = "".join(lines)
    return result


def lifecycle_archive(location: Path, *, strict: bool = False) -> tuple[Result, bytes]:
    """Return the documents of a project as a zip (empty bytes when the project is invalid)."""
    result, files = _lifecycle(location, strict=strict)
    if not files:
        return result, b""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in sorted(files.items()):
            archive.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), text)
    return result, buffer.getvalue()
