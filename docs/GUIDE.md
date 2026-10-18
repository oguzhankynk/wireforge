# WireForge user guide

WireForge turns a description of binary messages (YAML, edited by hand, in the web UI or by an AI agent) into C++17
code that serializes, validates, frames and dispatches them without heap, exceptions or RTTI, plus a GoogleTest
project that proves the code against a reference codec. The wire format is the one you describe: the generator never
adds bytes.

## Install
Python 3.10 to 3.13.
```sh
pip install "wireforge[server,mcp,import]"   # or, in a checkout: pip install -e ".[server,mcp,import]"
pip install "wireforge[oidc]"                # OIDC login for the server (docs/SERVER.md)
```
A C++17 compiler, CMake 3.21+ and (for the tests) Ninja or Make are needed to build generated code. Docker:
`docker build -t wireforge . && docker run -p 8000:8000 -v "$PWD/projects:/workspace" wireforge` serves the web UI and
API on port 8000 with the toolchain inside; it has no token by default, so read
[Deployment and operation](SERVER.md#deployment-and-operation) before exposing it.

## In CI
`wireforge ci <project> --base <revision> --test` validates the project, compares it with the base of a pull request
(wire compatibility and the version bump each changed interface needs) and builds and runs the generated tests, with one
Markdown summary. The repository has a GitHub action for it and a GitLab example (docs/integration/github_actions.md).

## Quickstart (about 5 minutes)
0. **Start from a template or a published protocol** (or skip to step 1 with your own ICD):
   ```sh
   wireforge init demo --template telemetry      # also: command, raw; --list shows them
   wireforge init gps --protocol ubx             # a published protocol, see "Published protocols"
   wireforge validate demo && wireforge test demo -o build/demo
   ```
   The template is a working project with comments; change the names, fields and limits to match your protocol.
   The web UI has the same start under "New project".
1. **Describe messages.** Write a `project.yaml` (docs/SCHEMA_SPEC.md, `examples/`), or import an ICD table, a C header or a CAN database (`wireforge import-dbc`, docs/IMPORT.md):
   ```sh
   wireforge import-icd examples/icd/telemetry_icd.csv demo --name telemetry --namespace demo::imu --endian little
   ```
2. **Add an interface** (header, framing, the messages it carries):
   ```sh
   wireforge upsert demo interfaces imu_link '{ version: "1.0.0", direction: rx,
     header: { fields: [{ name: msgId, type: u16, role: message_id }, { name: length, type: u8, role: payload_length }] },
     framing: { kind: length_prefixed, max_frame_size: 64 }, messages: [ImuSample, Heartbeat] }'
   ```
3. **Validate and inspect:** `wireforge validate demo`, `wireforge analyze demo` (sizes, offsets, bit maps, frame and
   buffer sizes per profile), `wireforge encode demo ImuSample '{ sensorId: 3 }' --interface imu_link`.
4. **Generate and test:** `wireforge test demo -o generated` generates the code and the test project, builds it and
   runs every test; `--coverage` runs the coverage target instead.
5. **Use it in your build:**
   ```cmake
   find_package(WireForge 0.1 REQUIRED)        # -DWireForge_DIR=$(wireforge cmake-dir), or an installed prefix
   wireforge_generate(imu_model PROJECT ${CMAKE_SOURCE_DIR}/demo)
   target_link_libraries(my_app PRIVATE imu_model)
   ```
   See `examples/cmake_consumer/` (`quality/run_quickstart.sh` runs all of the above from a fresh wheel and checks that
   it takes less than 15 minutes).

## Working with projects
- **Files are the source of truth** (docs/CLI.md): edit YAML by hand, with `wireforge set/upsert/remove`, in the web UI
  or through MCP; `wireforge fmt` keeps one canonical layout so every way of editing gives minimal diffs.
- **Profiles** select platform variants (`defines`, `if:` conditions); every tool works per profile.
- **Versions:** `interface.version` follows semver; `wireforge compat old/ new/` (or the UI's Versions tab) tells
  whether a change is breaking, compatible or documentation only.
- **Team server:** `wireforge serve --workspace projects/` (docs/SERVER.md, docs/UI.md).
- **AI agents:** `wireforge mcp --workspace projects/` (docs/MCP.md).
- **Wireshark:** `wireforge wireshark demo -o demo.lua` writes a Lua dissector plugin for every interface
  (docs/WIRESHARK.md).

## Generated code
`model.hpp` (types with defaults, `validate()`, sizes, `serialize()`/`deserialize()`), one header per interface (ids,
`encode_frame()`, `Handler`, `Endpoint`, `Link`) and `user_prelude.hpp`; see docs/CODEGEN.md. The runtime is
header-only (`runtime/include/wireforge/`, docs/RUNTIME.md). Bare-metal builds define `WIREFORGE_NO_STD_MUTEX`.

## Quality and safety
Zero warnings from every compiler and analyzer, a 95 % line-coverage gate on generated code (the examples reach
100 %), differential and fuzz testing
against the reference codec, big-endian and 32-bit test runs, no-heap evidence and a trace report from model rows to
code and tests (quality/QUALITY_POLICY.md, docs/SAFETY.md).

## Published protocols
`wireforge init <dir> --protocol <name>` (or the same choice under "New project" in the web UI) copies the model of a
published protocol that is written from its public documents and checked against an independent implementation of it
(byte for byte, in `tests/python/`). Each model has a README with its source, what it covers and what it does not.

| Protocol | Covers | Independent check |
|----------|--------|-------------------|
| `modbus_rtu` | Modbus RTU: read holding registers, write single register, exceptions; CRC-16/MODBUS sent low byte first; vectors from the examples of the specification | `pymodbus` |
| `modbus_tcp` | Modbus TCP: the MBAP header with a length, one message per interface | (vectors of the specification) |
| `j1939` | SAE J1939: the identifier word (priority, PGN, source address) and the groups EEC1, CCVS, ET1 | `cantools` |
| `ubx` | u-blox UBX: the frame, ACK-ACK, ACK-NAK, CFG-RATE, NAV-PVT, MON-VER; Fletcher-8 that skips the sync characters | `pyubx2` |

## Examples
| Example | Shows |
|---------|-------|
| `examples/01_basic_telemetry` | one interface: sync/id/length header, CRC-32 trailer, enum, nested struct, counted array |
| `examples/02_multi_interface_avionics` | avionics-style node: three interfaces (sync+length with CRC-16, raw, length-prefixed), platform profiles with conditional fields, lsb-first bit-fields, optional field, fill array, user-defined checksum, application constants |
| `examples/03_edge_cases` | corner cases: zero counts, nested optional, signed bit-fields, u64, wire constants |
| `examples/04_user_type` | an opaque field type backed by application code |
| `examples/07_can_dbc` | a CAN network imported from a DBC file: Intel and Motorola signals, factor and offset, value tables as enums, an extended identifier (`wireforge import-dbc`) |
| `examples/icd/` | ICD sources for the importers (CSV table, C header) |
| `examples/cmake_consumer/` | an application built with `find_package(WireForge)` |
