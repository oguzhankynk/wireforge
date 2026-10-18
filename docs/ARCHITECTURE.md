# Architecture

```
 Web UI (React) --HTTP--> Server (FastAPI) --> Service layer <-- MCP server (AI agents)
 wireforge CLI / Python API -----------------> |-- Model (loader, validator, expressions)
                                               |-- Reference codec (Python)
                                               |-- Code generator --> C++17 or C99 code + test project
                                               |-- Wireshark dissector --> Lua plugin
                                               |-- Analysis (layout/ICD, trace, compatibility)
 Project files (YAML, git) <------------------ storage (files are the source of truth)

 Target software:  generated headers  +  runtime/include/wireforge/*.hpp
   Transport -> ring buffer -> FrameDecoder -> Dispatcher -> Receiver -> Handler::on<Message>Raw
                                                                  -> (default: decode) Handler::on<Message>Received
   Message struct -> serialize -> Encoder -> transmit buffer -> Transport
```

Implementation (Python package `wireforge`, `codegen/wireforge/`): `loader`, `validate`, `expr` (model); `codec`,
`golden` (reference codec); `generate`, `cpp*`, `test_project`, `test_cases` (code and test generation); `wireshark`
(Lua dissector plugin); `layout`,
`trace`, `compat`, `differential` (analysis); `fmt`, `service`, `cli` (service layer and CLI); `server/` (FastAPI),
`mcp_server` (MCP), `importers`; `resources` locates the schemas, runtime headers, CMake package and UI shipped in the
wheel. Web UI: `ui/` (React, Vite). C++ runtime: `runtime/include/wireforge/` (header-only).

## Boundaries
- The service layer is the single API; the CLI, the HTTP server and the MCP server are thin adapters, so every
  operation is available in all of them.
- The runtime does not depend on the Python tooling; generated code depends only on the runtime
  (`runtime/include/wireforge/` for C++17, `.../wireforge/c/` for C99) and the standard library of its language.
- Project layout: `project.yaml` and optional fragments (`types/*.yaml`, `messages/*.yaml`, `interfaces/*.yaml`,
  `profiles/*.yaml`) listed by `include`.

## Authoring
Files are the source of truth. Two equal ways to work: the web editor reads and writes the project files through the
server; or edit YAML in any editor (the JSON Schema gives completion and validation) and use the `wireforge` CLI or the
Python API. The canonical formatter (`wireforge fmt`) keeps a stable key order and preserves comments, so GUI edits and
hand edits produce clean git diffs. The server sees external file changes immediately and detects conflicting edits.

## Thread safety
- Generated `serialize`, `deserialize` and `validate` are pure and reentrant: no globals, no static mutable state.
- Dispatch tables are `const` after initialization; registration happens before `start()` or is guarded by the lock
  policy.
- Ring buffers: single-producer/single-consumer is lock-free; other topologies use a lock policy.
- `Link`: the transmit path is serialized by the lock policy; `poll()` has a single consumer per link.
- The default lock policy is `std::mutex`-based; it is a template parameter, so bare-metal and RTOS ports need no
  `<mutex>` (`WIREFORGE_NO_STD_MUTEX`, `WIREFORGE_DEFAULT_LOCK`).
- ThreadSanitizer stress tests run in every gate run.

## Versioning
- `schema_version`: file-format version; `wireforge migrate` upgrades files; the loader refuses unknown newer versions.
- `interface.version` (semver): may appear in the wire header (`role: protocol_version`), in the generated banner and
  in the inline namespace, so several versions of an interface can coexist; `wireforge compat` reports breaking
  (layout, id, size, range), compatible and documentation-only changes and checks the version step.

## Portability
Fixed-width types only; serialization by shifts and masks per byte (no `reinterpret_cast`, no packed structs, no
unaligned access); no OS headers in the runtime; optional features behind configuration macros. Verified with GCC,
Clang and MSVC, on 32- and 64-bit and little- and big-endian targets.
