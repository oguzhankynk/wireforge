"""The `wireforge` command line: a thin front end of `wireforge.service`.

Every command accepts `--json` (machine-readable result on stdout). Exit codes are stable:

- 0: success;
- 1: the project or check failed (validation errors, `fmt --check`/`migrate --check` differences,
  insufficient version bump, test failures, codec errors);
- 2: usage error (bad arguments);
- 3: the command could not run (missing file or tool, invalid bundle, ...).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wireforge import (
    ci_check,
    fmt,
    hlr_import,
    importers,
    resources,
    service,
    test_project,
    versioning,
)
from wireforge.plain import to_plain
from wireforge.service import BuildOptions, CodecTarget, EditOptions, Result, ServiceError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from wireforge.server.oidc import OidcConfig

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ERROR = 3


class UsageError(ServiceError):
    """Arguments that parse but do not fit together (exit code 2)."""


def _yaml_value(text: str) -> object:
    """Parse a value given on the command line (YAML flow syntax: 85, abc, { max: 3 })."""
    document = fmt.load(f"value: {text}\n")
    if not isinstance(document, dict):
        message = f"cannot parse {text!r}"
        raise ServiceError(message)
    return to_plain(document["value"])


def _value_source(args: argparse.Namespace) -> object:
    if args.file is not None:
        document = fmt.load(Path(args.file).read_text(encoding="utf-8"))
        return to_plain(document)
    if args.value is None:
        message = "give a value or --file"
        raise ServiceError(message)
    return _yaml_value(args.value)


def _tests(args: argparse.Namespace) -> test_project.TestOptions:
    return test_project.TestOptions(
        plugins=args.plugins,
        runtime=args.runtime,
        threshold=args.coverage_threshold,
    )


def _codec_target(args: argparse.Namespace) -> CodecTarget:
    return CodecTarget(
        message=args.message,
        interface=args.interface,
        profile=args.profile,
        plugins=service.load_plugins(args.plugins),
    )


def _edit_options(args: argparse.Namespace) -> EditOptions:
    return EditOptions(dry_run=args.dry_run, strict=args.strict)


# --- command handlers ---------------------------------------------------------------------------


def _validate(args: argparse.Namespace) -> Result:
    return service.validate(args.project)


def _fmt(args: argparse.Namespace) -> Result:
    return service.format_project(args.project, check=args.check)


def _migrate(args: argparse.Namespace) -> Result:
    return service.migrate(args.project, check=args.check)


def _init(args: argparse.Namespace) -> Result:
    if args.list:
        names = service.templates()
        return Result(ok=True, data={"templates": names}, text="\n".join(names) + "\n")
    return service.init_project(
        args.directory, args.template, args.name, args.namespace, args.version
    )


def _ci(args: argparse.Namespace) -> Result:
    build = BuildOptions(generator=args.generator, extra_cmake_args=tuple(args.cmake_args or ()))
    options = ci_check.CiOptions(args.base, args.test, args.output, build, _tests(args))
    result = ci_check.run(args.project, options)
    if args.summary is not None:
        args.summary.write_text(str(result.data["summary"]) + "\n", encoding="utf-8")
    return result


def _generate(args: argparse.Namespace) -> Result:
    return service.generate_code(args.project, args.output, _tests(args) if args.tests else None)


def _wireshark(args: argparse.Namespace) -> Result:
    return service.wireshark_dissector(args.project, args.output)


def _docs(args: argparse.Namespace) -> Result:
    if args.runtime:
        if args.project is not None:
            message = "give a project or --runtime, not both"
            raise UsageError(message)
        return service.runtime_docs(args.output, results=args.results, strict=args.strict)
    if args.project is None:
        message = "give a project, or --runtime for the documents of the runtime library"
        raise UsageError(message)
    options = service.DocsOptions(
        tuple(part for part in args.export.split(",") if part),
        args.source_url,
        args.allow_dirty,
        args.since,
        tuple(args.rule_findings or ()),
    )
    return service.lifecycle_docs(
        args.project, args.output, strict=args.strict, results=args.results, options=options
    )


def _reqs_import(args: argparse.Namespace) -> Result:
    options = hlr_import.ImportOptions(
        args.baseline,
        args.tool,
        args.id_attr,
        args.text_attr,
        args.csv_id_column,
        args.csv_text_column,
        args.force,
    )
    return service.requirements_import(args.project, args.file, options)


def _reqs_lock(args: argparse.Namespace) -> Result:
    return service.requirements_lock(args.project, args.format)


def _reqs_rename_key(args: argparse.Namespace) -> Result:
    return service.requirements_rename_key(args.project, args.old, args.new)


def _reqs_list(args: argparse.Namespace) -> Result:
    return service.requirements_list(args.project)


def _analyze(args: argparse.Namespace) -> Result:
    return service.analyze(args.project, args.profile, args.format)


def _compat(args: argparse.Namespace) -> Result:
    return service.compatibility(args.old, args.new, args.interfaces)


def _test(args: argparse.Namespace) -> Result:
    build = BuildOptions(
        build_dir=args.build_dir,
        coverage=args.coverage or args.coverage_runtime,
        coverage_runtime=args.coverage_runtime,
        generator=args.generator,
        extra_cmake_args=tuple(args.cmake_args or ()),
    )
    return service.run_tests(args.project, args.output, _tests(args), build)


def _export(args: argparse.Namespace) -> Result:
    return service.export_project(args.project, args.output)


def _import(args: argparse.Namespace) -> Result:
    return service.import_project(
        args.bundle, args.destination, verify=not args.no_verify, single_file=args.single_file
    )


def _import_icd(args: argparse.Namespace) -> Result:
    options = importers.Project(args.name, args.namespace, args.version, args.endian)
    return service.import_icd(args.source, args.destination, options, args.messages)


def _get(args: argparse.Namespace) -> Result:
    return service.get(args.project, args.pointer)


def _set(args: argparse.Namespace) -> Result:
    return service.set_value(args.project, args.pointer, _value_source(args), _edit_options(args))


def _upsert(args: argparse.Namespace) -> Result:
    definition = _value_source(args)
    if not isinstance(definition, dict):
        message = "a definition is a mapping"
        raise ServiceError(message)
    return service.upsert(args.project, args.section, args.name, definition, _edit_options(args))


def _remove(args: argparse.Namespace) -> Result:
    return service.remove(args.project, args.pointer, _edit_options(args))


def _encode(args: argparse.Namespace) -> Result:
    value = _value_source(args)
    if not isinstance(value, dict):
        message = "a message value is a mapping"
        raise ServiceError(message)
    return service.encode(args.project, value, _codec_target(args))


def _oidc_config(args: argparse.Namespace) -> OidcConfig | None:
    """Return the `OidcConfig` the serve options ask for, or None."""
    if not args.oidc_issuer:
        return None
    if args.token:
        message = "--token and --oidc-issuer are mutually exclusive"
        raise UsageError(message)
    if not args.oidc_audience:
        message = "--oidc-issuer needs --oidc-audience"
        raise UsageError(message)
    try:  # optional dependencies: pip install 'wireforge[server,oidc]'
        oidc = importlib.import_module("wireforge.server.oidc")
    except ImportError as error:
        message = f"OIDC needs the oidc extra: pip install 'wireforge[oidc]' ({error})"
        raise ServiceError(message) from error
    config: OidcConfig = oidc.OidcConfig(
        args.oidc_issuer, args.oidc_audience, args.oidc_jwks_cache_seconds
    )
    return config


def _serve(args: argparse.Namespace) -> Result:
    try:  # optional dependencies: pip install 'wireforge[server]'
        uvicorn = importlib.import_module("uvicorn")
        server = importlib.import_module("wireforge.server.app")
    except ImportError as error:
        message = f"the server needs the server extra: pip install 'wireforge[server]' ({error})"
        raise ServiceError(message) from error
    oidc = _oidc_config(args)
    app = server.create_app(args.workspace, args.token, oidc)
    if args.openapi is not None:
        args.openapi.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
        return Result(ok=True, data={"openapi": str(args.openapi)}, text=f"wrote {args.openapi}\n")
    uvicorn.run(app, host=args.host, port=args.port)
    return Result(ok=True, text="server stopped\n")


def _mcp(args: argparse.Namespace) -> Result:
    try:  # optional dependency: pip install 'wireforge[mcp]'
        server = importlib.import_module("wireforge.mcp_server")
    except ImportError as error:
        message = f"the MCP server needs the mcp extra: pip install 'wireforge[mcp]' ({error})"
        raise ServiceError(message) from error
    server.main(args.workspace)
    return Result(ok=True)


def _trace(args: argparse.Namespace) -> Result:
    result = service.trace_report(args.project, args.format)
    if args.output is not None and result.ok:
        args.output.write_text(result.text, encoding="utf-8")
        result.text = f"wrote {args.output}\n"
    return result


def _cmake_dir(args: argparse.Namespace) -> Result:
    del args
    path = resources.cmake_dir()
    return Result(ok=path.exists(), data={"path": str(path)}, text=f"{path}\n")


def _version(args: argparse.Namespace) -> Result:
    del args
    file_format = f"{versioning.FORMAT_MAJOR}.{versioning.FORMAT_MINOR}"
    text = f"wireforge {versioning.TOOL_VERSION} (file format {file_format})"
    return Result(ok=True, data={"version": versioning.TOOL_VERSION}, text=text + "\n")


def _decode(args: argparse.Namespace) -> Result:
    try:
        data = bytes.fromhex(args.hex)
    except ValueError as error:
        message = f"{args.hex!r} is not hex"
        raise ServiceError(message) from error
    return service.decode(args.project, data, _codec_target(args))


# --- parser -------------------------------------------------------------------------------------


def _project(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project", type=Path, help="project directory or root file")


def _test_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plugins", type=Path, help="Python file defining checksum PLUGINS")
    parser.add_argument(
        "--runtime",
        type=Path,
        default=test_project.RUNTIME_INCLUDE,
        help="WireForge runtime include directory for the test project",
    )
    parser.add_argument(
        "--coverage-threshold", type=int, default=test_project.DEFAULT_COVERAGE, metavar="PERCENT"
    )


def _edit(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="show the diff, write nothing")
    parser.add_argument("--strict", action="store_true", help="refuse results with errors")


def _value(parser: argparse.ArgumentParser, name: str = "value") -> None:
    parser.add_argument(name, nargs="?", help="YAML value, e.g. 85 or '{ max: 3 }'")
    parser.add_argument("--file", help="read the value from a YAML file")


def _codec_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--interface")
    parser.add_argument("--profile")
    parser.add_argument("--plugins", type=Path, help="Python file defining checksum PLUGINS")


Handler = Callable[[argparse.Namespace], Result]


def _build_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coverage", action="store_true", help="run the coverage target")
    parser.add_argument(
        "--coverage-runtime",
        action="store_true",
        help="also measure the runtime headers (implies --coverage)",
    )
    parser.add_argument("--generator", help="CMake generator, e.g. Ninja")
    parser.add_argument(
        "--cmake-arg", action="append", dest="cmake_args", help="extra argument for cmake"
    )


def _commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    def add(name: str, handler: Handler, text: str) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, help=text, description=text)
        parser.set_defaults(handler=handler)
        parser.add_argument("--json", action="store_true", help="print the result as JSON")
        return parser

    _project(add("validate", _validate, "Validate a project."))
    parser = add("fmt", _fmt, "Write every project file in the canonical layout.")
    _project(parser)
    parser.add_argument("--check", action="store_true", help="only report files that differ")
    parser = add("migrate", _migrate, "Upgrade the project files to the current format.")
    _project(parser)
    parser.add_argument("--check", action="store_true", help="only report files that differ")
    _init_command(add)
    _ci_command(add)
    parser = add("generate", _generate, "Generate C++ code (and the test project).")
    _project(parser)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--tests", action="store_true", help="also write generated_tests/")
    _test_options(parser)
    parser = add("wireshark", _wireshark, "Generate a Wireshark Lua dissector plugin.")
    _project(parser)
    parser.add_argument("-o", "--output", type=Path, required=True, help="the .lua file to write")
    _report_commands(add)
    parser = add("test", _test, "Generate, build and run the generated tests.")
    _project(parser)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path)
    _build_options(parser)
    _test_options(parser)
    parser = add("export", _export, "Write the single-file bundle of a project.")
    _project(parser)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser = add("import", _import, "Unpack a bundle into a project directory.")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--single-file", action="store_true")
    _import_commands(add)
    _edit_commands(add)
    add("cmake-dir", _cmake_dir, "Print the directory of the WireForge CMake package.")
    add("version", _version, "Print the WireForge version.")
    _codec_commands(add)
    parser = add("mcp", _mcp, "Run the MCP server (stdio) over a workspace of projects.")
    parser.add_argument("--workspace", type=Path, required=True, help="directory of projects")
    parser = add("serve", _serve, "Run the HTTP server over a workspace of projects.")
    parser.add_argument("--workspace", type=Path, required=True, help="directory of projects")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--token", help="require this bearer token")
    _oidc_options(parser)
    parser.add_argument("--openapi", type=Path, help="write the OpenAPI document and exit")


def _ci_command(add: Callable[[str, Handler, str], argparse.ArgumentParser]) -> None:
    parser = add("ci", _ci, "The check of a CI system: validate, compare with a base, test.")
    _project(parser)
    parser.add_argument("--base", help="git revision to compare with (the base of a pull request)")
    parser.add_argument(
        "--test", action="store_true", help="also generate, build and run the tests"
    )
    parser.add_argument("-o", "--output", type=Path, default=Path("wireforge-ci"))
    parser.add_argument("--summary", type=Path, help="write the Markdown summary to this file")
    parser.add_argument("--generator", help="CMake generator, e.g. Ninja")
    parser.add_argument(
        "--cmake-arg", action="append", dest="cmake_args", help="extra argument for cmake"
    )
    _test_options(parser)


def _init_command(add: Callable[[str, Handler, str], argparse.ArgumentParser]) -> None:
    parser = add("init", _init, "Create a project from a template (telemetry, command, raw).")
    parser.add_argument("directory", type=Path, nargs="?", default=Path())
    parser.add_argument("--template", default=service.DEFAULT_TEMPLATE, help="see --list")
    parser.add_argument("--name", help="project name (default: the directory name)")
    parser.add_argument("--namespace", help="C++ namespace (default: the project name)")
    parser.add_argument("--version", help="project version, for example 0.1.0")
    parser.add_argument("--list", action="store_true", help="list the templates and exit")


def _oidc_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--oidc-issuer", help="require an OIDC JWT of this issuer (not with --token)"
    )
    parser.add_argument("--oidc-audience", help="audience the JWT must carry (with --oidc-issuer)")
    parser.add_argument("--oidc-jwks-cache-seconds", type=int, default=3600)


def _report_commands(add: Callable[[str, Handler, str], argparse.ArgumentParser]) -> None:
    parser = add("analyze", _analyze, "Layout report (ICD) per profile.")
    _project(parser)
    parser.add_argument("--profile")
    parser.add_argument("--format", choices=("markdown", "csv", "json"), default="markdown")
    parser = add("trace", _trace, "Trace model elements to generated code and tests.")
    _project(parser)
    parser.add_argument("--format", choices=("markdown", "csv"), default="markdown")
    parser.add_argument("-o", "--output", type=Path)
    parser = add("docs", _docs, "Write requirements, design, test and traceability documents.")
    parser.add_argument("project", type=Path, nargs="?", help="project directory or root file")
    parser.add_argument(
        "--runtime", action="store_true", help="document the runtime library instead of a project"
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="directory to write")
    parser.add_argument("--strict", action="store_true", help="fail when a review would reject")
    parser.add_argument(
        "--results", type=Path, help="build directory of a test run (--coverage --coverage-runtime)"
    )
    parser.add_argument("--export", default="csv", help="formats besides Markdown: csv, reqif")
    parser.add_argument("--source-url", help="permalink template with {commit}, {path} and {line}")
    parser.add_argument(
        "--rule-findings",
        type=Path,
        action="append",
        help="SARIF or cppcheck XML report of your coding-rule checker (repeatable)",
    )
    parser.add_argument("--allow-dirty", action="store_true", help="link even with local changes")
    parser.add_argument("--since", metavar="REV", help="also write impact.md for a git revision")
    parser = add(
        "reqs-import", _reqs_import, "Import the high-level requirements of a ReqIF or CSV file."
    )
    _project(parser)
    parser.add_argument("file", type=Path, help=".reqif, .reqifz or .csv file of your tool")
    parser.add_argument("--baseline", required=True, help="name of the baseline, e.g. SRS-B12")
    parser.add_argument("--tool", help="name of your tool (default: from the ReqIF header)")
    parser.add_argument(
        "--id-attr", help="ReqIF attribute holding the id (default ReqIF.ForeignID)"
    )
    parser.add_argument("--text-attr", help="ReqIF attribute holding the text (default ReqIF.Text)")
    parser.add_argument("--csv-id-column", default="id")
    parser.add_argument("--csv-text-column", default="text")
    parser.add_argument("--force", action="store_true", help="overwrite a snapshot edited by hand")
    parser = add("reqs-lock", _reqs_lock, "Number the low-level requirements in llr_ids.lock.")
    _project(parser)
    parser.add_argument("--format", help="id format of a new lock file, e.g. SWLLR-{n:05d}")
    parser = add("reqs-rename-key", _reqs_rename_key, "Keep a requirement's number after a rename.")
    _project(parser)
    parser.add_argument("old", help="key of the requirement before the rename")
    parser.add_argument("new", help="key after the rename")
    parser = add("reqs-list", _reqs_list, "List the low-level requirements and their ids.")
    _project(parser)
    parser = add("compat", _compat, "Compare two versions of a project.")
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--interface", action="append", dest="interfaces")


def _import_commands(add: Callable[[str, Handler, str], argparse.ArgumentParser]) -> None:
    parser = add("import-icd", _import_icd, "Import an ICD table (.csv, .xlsx) or a C header.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--name", required=True, help="project name")
    parser.add_argument("--namespace")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--endian", choices=("big", "little"))
    parser.add_argument(
        "--message", action="append", dest="messages", help="C header: struct that is a message"
    )


def _edit_commands(add: Callable[[str, Handler, str], argparse.ArgumentParser]) -> None:
    parser = add("get", _get, "Print the value at a JSON pointer.")
    _project(parser)
    parser.add_argument("pointer", help="e.g. /messages/NavSolution/fields/0")
    parser = add("set", _set, "Set the value at a JSON pointer.")
    _project(parser)
    parser.add_argument("pointer")
    _value(parser)
    _edit(parser)
    parser = add("upsert", _upsert, "Create or update a section entry.")
    _project(parser)
    parser.add_argument("section", help="types, messages, interfaces, enums, ...")
    parser.add_argument("name")
    _value(parser)
    _edit(parser)
    parser = add("remove", _remove, "Delete the element at a JSON pointer.")
    _project(parser)
    parser.add_argument("pointer")
    _edit(parser)


def _codec_commands(add: Callable[[str, Handler, str], argparse.ArgumentParser]) -> None:
    parser = add("encode", _encode, "Encode a message value (payload and frame).")
    _project(parser)
    parser.add_argument("message")
    _value(parser)
    _codec_options(parser)
    parser = add("decode", _decode, "Decode a payload (with --message) or a frame (--interface).")
    _project(parser)
    parser.add_argument("hex")
    parser.add_argument("--message")
    _codec_options(parser)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the `wireforge` command."""
    parser = argparse.ArgumentParser(prog="wireforge", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    _commands(sub)
    return parser


def _emit(result: Result, *, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2, default=str) + "\n")
    else:
        sys.stdout.write(result.text)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return EXIT_OK if stop.code == 0 else EXIT_USAGE
    handler: Handler = args.handler
    try:
        result = handler(args)
    except UsageError as error:
        _emit(
            Result(ok=False, data={"error": str(error)}, text=f"error: {error}\n"),
            as_json=args.json,
        )
        return EXIT_USAGE
    except (ServiceError, OSError) as error:
        result = Result(ok=False, data={"error": str(error)}, text=f"error: {error}\n")
        _emit(result, as_json=args.json)
        return EXIT_ERROR
    _emit(result, as_json=args.json)
    return EXIT_OK if result.ok else EXIT_FAILED


def _entry() -> None:
    sys.exit(main())


def run(argv: Sequence[str]) -> dict[str, Any]:
    """Run one command in-process and return the exit code and the JSON result (for scripts)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main([*argv, "--json"])
    output = buffer.getvalue()
    return {"exit_code": code, "result": json.loads(output) if output.strip() else None}


if __name__ == "__main__":
    _entry()
