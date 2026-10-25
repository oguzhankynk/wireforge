<img src="https://raw.githubusercontent.com/oguzhankynk/wireforge/master/docs/images/logo.png" alt="" width="80">

# WireForge

[![PyPI](https://img.shields.io/pypi/v/wireforge)](https://pypi.org/project/wireforge/)
[![Python](https://img.shields.io/pypi/pyversions/wireforge)](https://pypi.org/project/wireforge/)
[![CI](https://github.com/oguzhankynk/wireforge/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/oguzhankynk/wireforge/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-BUSL--1.1%20%2B%20MIT%20runtime-blue)](#license)

Schema-driven wire-format toolkit for C++17 and C99: describe binary messages in YAML (by hand, in the web UI, through
the CLI or an AI agent) and generate allocation-free serializers, validators, framers and dispatchers (C++17, or C99
with `--language c`) together with a GoogleTest project that proves them against a reference codec and gates the
generated code at 100 % line, branch and function coverage.

- The wire format is imposed by the remote party: generated code never adds bytes.
- Runtime and generated code use only pre-allocated storage: no heap, no exceptions, no RTTI required.
- One service layer behind the `wireforge` CLI, the Python API, the REST server with its web UI and the MCP server.
- A generated Wireshark Lua dissector shows every interface's frames, with the same checks as the decoder.
- Zero warnings from every compiler and analyzer; tested on 32/64 bit, little and big endian, x86 and ARM, on Linux,
  Windows, macOS, Android and FreeRTOS; differential testing and fuzzing.

The way in is short: `wireforge init` gives a working first project, you change it to your protocol, generate the C++
code, run the tests and read the results. Nothing about requirements or standards is needed for that.

[![WireForge in one minute (video)](https://raw.githubusercontent.com/oguzhankynk/wireforge/master/docs/images/video-cover.png)](https://youtu.be/o5CbrNqxPiI)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/oguzhankynk/wireforge/master/docs/images/web-ui-dark.png">
  <img alt="The web UI right after New project: the first message open in the editor, the next steps, and the Generate tab with the generated C++ and the download of code and tests" src="https://raw.githubusercontent.com/oguzhankynk/wireforge/master/docs/images/web-ui.png">
</picture>

## For teams with a development or assurance process
Optionally, `wireforge docs` turns the same model into requirements, design, test and traceability documents with test
and coverage results, change impact between revisions and ReqIF/CSV exchange with requirements tools (docs/LIFECYCLE.md,
docs/integration/). For projects that apply standards such as DO-178C, ISO 26262, IEC 61508 or IEC 62304, this evidence
supports your processes and your tool assessment; it does not by itself give conformity, certification or tool
qualification, which depend on the whole project and its assessor (docs/DO178C.md).

**Start with the [tutorial](docs/tutorial/README.md)** (a MAVLink 2 drone link from the model to the application, tests,
CI, Wireshark and AI agents), **docs/GUIDE.md** (install, 5-minute quickstart, examples), or the
[wiki](https://github.com/oguzhankynk/wireforge/wiki) for a short tour. Reference:

| Topic | Document |
|-------|----------|
| How it compares (protobuf, nanopb, Kaitai Struct, mavgen, cantools, ...) | docs/COMPARISON.md |
| Definition format | docs/SCHEMA_SPEC.md, `schema/`, `examples/` |
| Command line and Python API | docs/CLI.md |
| Generated C++ and test project | docs/CODEGEN.md, docs/RUNTIME.md |
| Layout analysis / ICD export | docs/LAYOUT.md |
| Wireshark dissector (Lua plugin) | docs/WIRESHARK.md |
| Requirements, design and test documents | docs/LIFECYCLE.md |
| Fitting into a DO-178C process, tool integration | docs/DO178C.md, docs/integration/ |
| Server, web UI, MCP | docs/SERVER.md, docs/UI.md, docs/MCP.md |
| Secure deployment and operation | docs/SERVER.md, "Deployment and operation" |
| Importers (CSV/Excel ICD, C headers, CAN databases, MAVLink XML dialects) | docs/IMPORT.md |
| Published protocols (Modbus, CRSF, J1939, MAVLink 1 and 2, UBX; `wireforge init --protocol`) | docs/GUIDE.md, `protocols/` |
| Quality, safety, traceability | quality/QUALITY_POLICY.md, docs/SAFETY.md, docs/CODING_RULES.md |
| Architecture and design decisions | docs/ARCHITECTURE.md, docs/DECISIONS.md |

Development: see CONTRIBUTING.md (`pip install -e ".[dev]"`, `cmake --preset dev && cmake --build --preset dev && ctest
--preset dev`, all gates: `quality/run_all.sh`).

## GitHub Action
`oguzhankynk/wireforge` is also a GitHub Action that validates a project, checks wire compatibility with the base of a
pull request and runs the generated tests (docs/integration/github_actions.md):

```yaml
- uses: oguzhankynk/wireforge@v0.4.1
  with: { project: icd }
```

## License
The `runtime/` library is MIT-licensed (see runtime/LICENSE): code generated by WireForge belongs to you and depends
only on this MIT-licensed runtime, so shipping it in your product carries no restriction.

Everything else (the CLI, code generator, server, web UI, and MCP server) is licensed under the Business Source
License 1.1 (see LICENSE): free for development, testing, and evaluation; a commercial license is required for
production use, except that running it in continuous integration (validation, comparison with earlier
revisions, generated tests) is free, also in commercial product repositories. It converts to MIT on 2036-10-17. Contact the Licensor for commercial licensing: kaynak.oguzhan0@gmail.com.
