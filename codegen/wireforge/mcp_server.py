"""MCP server: WireForge tools for AI agents over a workspace of projects.

Every tool is a thin wrapper of `wireforge.service` and has a CLI equivalent (docs/MCP.md). Tools
that write accept `dry_run` and return the unified diff of the change; every result carries
`ok`, `issues`, `data` and `text`. Run with `wireforge mcp --workspace <dir>` (stdio transport).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from wireforge import golden, schema, service, test_project
from wireforge.codec import ReferenceCodec
from wireforge.loader import SECTIONS, load_project
from wireforge.service import CodecTarget, EditOptions, Result, ServiceError

if TYPE_CHECKING:
    from collections.abc import Callable

INSTRUCTIONS = """\
WireForge defines binary messages in YAML and generates C++17 code and tests from them.
Typical flow: create_project, then upsert_entry (checksums, enums, interfaces, profiles) and
upsert_struct (types, messages), validate_project until it reports no issues, generate_code,
run_tests, lifecycle_docs (requirements, design, test and traceability documents). Use
dry_run=true to preview a change as a diff. Field definitions follow
schema/project.schema.json: {name, type, bits?, min?, max?, default?, occurs?: {max, count?,
fill?}, checksum?: {use, covers: {scope}}, if?, description?}.
"""


class Tools:
    """The tool implementations, bound to a workspace directory."""

    def __init__(self, workspace: Path) -> None:
        """Serve the projects below `workspace` (one directory per project)."""
        self.workspace = workspace
        self.output = workspace / ".wireforge" / "generated"

    def project_dir(self, project: str) -> Path:
        """Directory of a project of the workspace."""
        directory = (self.workspace / project).resolve()
        if self.workspace.resolve() not in directory.parents:
            message = f"{project!r} is not a project of the workspace"
            raise ServiceError(message)
        if not (directory / schema.ROOT_FILE_NAME).exists():
            message = f"no project {project!r} in the workspace"
            raise ServiceError(message)
        return directory

    def list_projects(self) -> dict[str, Any]:
        """Return the projects of the workspace."""
        names = sorted(
            path.name
            for path in self.workspace.iterdir()
            if (path / schema.ROOT_FILE_NAME).exists()
        )
        return Result(ok=True, data={"projects": names}, text="\n".join(names)).to_dict()

    def create_project(
        self, project: str, name: str, namespace: str | None = None, version: str = "0.1.0"
    ) -> dict[str, Any]:
        """Create an empty project directory with its root file."""
        directory = self.workspace / project
        if directory.exists():
            message = f"{project} already exists"
            raise ServiceError(message)
        directory.mkdir(parents=True)
        lines = ["schema_version: 1", f"project: {name}", f'version: "{version}"']
        if namespace:
            lines.append(f"namespace: {namespace}")
        (directory / schema.ROOT_FILE_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return service.validate(directory).to_dict()

    def list_messages(self, project: str) -> dict[str, Any]:
        """Return the messages (with ids), types and interfaces of a project."""
        loaded = load_project(self.project_dir(project))
        model = loaded.project
        if model is None:
            return Result(ok=False, issues=loaded.issues).to_dict()
        messages = [
            {"name": item.name, "id": item.message.id if item.message else None}
            for item in model.messages.values()
        ]
        data = {
            "messages": messages,
            "types": list(model.types),
            "user_types": list(model.user_types),
            "interfaces": list(model.interfaces),
        }
        text = "\n".join(f"{item['name']} id={item['id']}" for item in messages)
        return Result(ok=True, data=data, text=text).to_dict()

    def get_message(self, project: str, name: str) -> dict[str, Any]:
        """Return the definition of a message (or of a type) as written in the files."""
        directory = self.project_dir(project)
        for section in ("messages", "types"):
            try:
                result = service.get(directory, f"/{section}/{name}")
            except ServiceError:
                continue
            result.data["section"] = section
            return result.to_dict()
        message = f"no message or type {name!r}"
        raise ServiceError(message)

    def upsert_struct(
        self,
        project: str,
        section: str,
        name: str,
        definition: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create or update a type or message ({description?, id?, fields: [...]})."""
        if section not in {"types", "messages"}:
            message = "section must be 'types' or 'messages'"
            raise ServiceError(message)
        options = EditOptions(dry_run=dry_run)
        return service.upsert(
            self.project_dir(project), section, name, definition, options
        ).to_dict()

    def upsert_entry(
        self,
        project: str,
        section: str,
        name: str,
        definition: object,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create or update an entry of any section (checksums, enums, interfaces, ...)."""
        if section not in SECTIONS:
            message = f"section must be one of {', '.join(SECTIONS)}"
            raise ServiceError(message)
        options = EditOptions(dry_run=dry_run)
        pointer = f"/{section}/{name}"
        return service.set_value(self.project_dir(project), pointer, definition, options).to_dict()

    def validate_project(self, project: str) -> dict[str, Any]:
        """Validate a project; issues carry file, line and JSON pointer."""
        return service.validate(self.project_dir(project)).to_dict()

    def generate_code(self, project: str, *, tests: bool = True) -> dict[str, Any]:
        """Generate the C++ code (and the GoogleTest project) below the workspace output."""
        directory = self.project_dir(project)
        options = test_project.TestOptions(plugins=service.find_plugins(directory))
        result = service.generate_code(directory, self.output, options if tests else None)
        name = str(result.data.get("project", ""))
        target = self.output / name
        files = sorted(
            str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()
        )
        result.data["files"] = files if result.ok else []
        return result.to_dict()

    def run_tests(
        self, project: str, *, coverage: bool = False, coverage_runtime: bool = False
    ) -> dict[str, Any]:
        """Generate, build and run the generated tests (with `coverage` the coverage target).

        `coverage_runtime` adds the runtime headers to the coverage report. The results are written
        below the returned `build_dir`, which `lifecycle_docs` accepts as `results_dir`.
        """
        directory = self.project_dir(project)
        options = test_project.TestOptions(plugins=service.find_plugins(directory))
        build = service.BuildOptions(
            coverage=coverage or coverage_runtime, coverage_runtime=coverage_runtime
        )
        result = service.run_tests(directory, self.output, options, build)
        return result.to_dict()

    def wireshark_dissector(self, project: str) -> dict[str, Any]:
        """Write the Wireshark Lua dissector plugin of a project below the workspace output."""
        target = self.output / "wireshark" / f"{project}.lua"
        return service.wireshark_dissector(self.project_dir(project), target).to_dict()

    def lifecycle_docs(
        self, project: str, *, strict: bool = False, results_dir: str | None = None
    ) -> dict[str, Any]:
        """Write the requirements, design, test and traceability documents of a project.

        `results_dir` is the `build_dir` that `run_tests` returned; it must lie below the workspace
        output, so a tool call cannot read other places.
        """
        target = self.output.parent / "docs" / project
        results = None
        if results_dir is not None:
            results = Path(results_dir).resolve()
            if self.output.resolve() not in results.parents:
                message = "results_dir must be a build directory below the workspace output"
                raise ServiceError(message)
        return service.lifecycle_docs(
            self.project_dir(project), target, strict=strict, results=results
        ).to_dict()

    def analyze_layout(
        self, project: str, profile: str | None = None, export: str = "markdown"
    ) -> dict[str, Any]:
        """Return the layout report: sizes, offsets, bit maps, frame sizes and link RAM."""
        if export not in {"markdown", "csv", "json"}:
            message = "export must be markdown, csv or json"
            raise ServiceError(message)
        return service.analyze(self.project_dir(project), profile, export).to_dict()

    def compat_check(
        self, project: str, old_project: str | None = None, revision: str | None = None
    ) -> dict[str, Any]:
        """Compare an older version (another project or a git revision) with the project."""
        directory = self.project_dir(project)
        if old_project is not None:
            return service.compatibility(self.project_dir(old_project), directory).to_dict()
        if revision is None or revision.startswith("-"):
            message = "give old_project or a git revision"
            raise ServiceError(message)
        return service.compatibility_with_revision(directory, revision).to_dict()

    def _target(
        self, directory: Path, message: str | None, interface: str | None, profile: str | None
    ) -> CodecTarget:
        plugins = service.load_plugins(service.find_plugins(directory))
        return CodecTarget(message, interface, profile, plugins)

    def encode_sample(
        self,
        project: str,
        message: str,
        value: dict[str, Any] | None = None,
        interface: str | None = None,
        profile: str | None = None,
    ) -> dict[str, Any]:
        """Encode a message value; without `value` a sample with every field set is used."""
        directory = self.project_dir(project)
        target = self._target(directory, message, interface, profile)
        if value is None:
            model = load_project(directory).project
            if model is None or message not in model.messages:
                text = f"no message {message!r}"
                raise ServiceError(text)
            codec = ReferenceCodec(model, profile, target.plugins)
            value = golden.Sampler(codec).struct(model.messages[message].fields)
        result = service.encode(directory, value, target)
        result.data["value"] = value
        return result.to_dict()

    def decode_hex(
        self,
        project: str,
        hex_bytes: str,
        message: str | None = None,
        interface: str | None = None,
        profile: str | None = None,
    ) -> dict[str, Any]:
        """Decode hex bytes: a payload with `message`, otherwise a complete frame of `interface`."""
        directory = self.project_dir(project)
        try:
            data = bytes.fromhex(hex_bytes)
        except ValueError as error:
            text = f"{hex_bytes!r} is not hex"
            raise ServiceError(text) from error
        return service.decode(
            directory, data, self._target(directory, message, interface, profile)
        ).to_dict()


TOOL_NAMES = (
    "list_projects",
    "create_project",
    "list_messages",
    "get_message",
    "upsert_struct",
    "upsert_entry",
    "validate_project",
    "generate_code",
    "run_tests",
    "analyze_layout",
    "wireshark_dissector",
    "lifecycle_docs",
    "compat_check",
    "encode_sample",
    "decode_hex",
)


def _guarded(function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Report service errors (bad names, missing projects, ...) as tool errors."""

    @functools.wraps(function)
    def call(*args: object, **kwargs: object) -> dict[str, Any]:
        try:
            return function(*args, **kwargs)
        except (ServiceError, OSError, ValueError) as error:
            raise ToolError(str(error)) from error

    return call


def create_server(workspace: Path) -> MCPServer:
    """Return the MCP server with every tool bound to `workspace`."""
    server = MCPServer("wireforge", instructions=INSTRUCTIONS)
    tools = Tools(workspace)
    for name in TOOL_NAMES:
        server.add_tool(_guarded(getattr(tools, name)), name=name)
    return server


def main(workspace: Path) -> None:
    """Run the server on stdio."""
    create_server(workspace).run("stdio")
