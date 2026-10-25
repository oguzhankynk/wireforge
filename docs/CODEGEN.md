# Generated C++ code

`python -m wireforge.generate <project> -o <dir> [--tests]` validates the project and writes, below `<dir>/<project>/`:

| File | Content |
|------|---------|
| `user_prelude.hpp` | `user_includes` headers and prelude lines; `static_assert`s that application constants match the model (per profile via `WIREFORGE_PROFILE_<name>`) |
| `interfaces/<name>.hpp` | per interface, in `namespace <ns>::<interface>::inline v<major>_<minor>`: version constants, message ids and per-interface encodings, frame/buffer sizes, `FrameFormat`/`HeaderLayout`, `Decoder`/`Dispatcher`/`Link` types, `Header`, `encode_frame()`, `decode_header()`, `Handler` (`on<Message>Raw`, `on<Message>Received`), `Receiver`, `Endpoint` |
| `model.hpp` | define constants, checksum types, enums with `is_valid()`, types and messages, `validate()`, `min_wire_size()`, `max_wire_size()`, `encode()`/`decode()`, `serialize()`/`deserialize()` |

Include directories: `<dir>`, `runtime/include` and the project's `user_includes.include_dirs`. Generated files are
regenerated only when their content changes. The output does not depend on the profile: build a profile by passing its
defines (`-DPLATFORM_B=1 -DMAX_TRACKS=5 ...`) and `-DWIREFORGE_PROFILE_<name>`.

## Data types (decision D28)
```cpp
namespace demo::telemetry {
struct SensorReading {
    std::uint8_t sensorId{1U};
    SensorState state{SensorState::ready};
    float temperature{20.0F};
    Vec3 accel{};
    wireforge::StaticVector<std::uint16_t, 4U> samples;   // count field sampleCount is wire-only
};
constexpr wireforge::Status validate(const SensorReading& value) noexcept;
constexpr std::size_t min_wire_size(wireforge::TypeTag<SensorReading>) noexcept;
constexpr std::size_t max_wire_size(wireforge::TypeTag<SensorReading>) noexcept;
}
```
- Plain aggregates: `SensorReading{}` holds the model defaults. Each struct is documented with `Defined in <file>:<line>
  (<JSON pointer>)` and each member with its `file:line` (traceability, docs/SAFETY.md). A project with a
  `requirements` catalog adds `Requirements: <HLR ids>; <LLR ids>` to every struct, enum, checksum and interface; a
  project without one generates exactly the same code as before.
- Fixed arrays are `std::array<T, N>`, counted or `fill` arrays `wireforge::StaticVector<T, N>` (an optional field is
  `StaticVector<T, 1>`), `char` arrays `wireforge::FixedString<N>`.
- `const`, `reserved`, checksum fields and count fields used only as `occurs.count: name` exist only on the wire.
- `validate()` checks min/max (also opaque user constants), enum membership, bit widths, element counts and nested
  values; it returns the first error.
- Sizes are `constexpr` and follow the build's defines and `#if` blocks; call them as
  `max_wire_size(wireforge::type_tag<SensorReading>)`.

## Requirement vectors
With a `vectors` section the test project also has `hlr_vector_test.cpp` (`TEST(HlrVector, <name>)`, one per profile
a vector applies to) and the self-test checks the same vectors without a test framework. Each test decodes or encodes
the vector's bytes and compares with the values written in the model, never with the reference codec, and records the
requirement ids of the vector (docs/SCHEMA_SPEC.md section 18). A project without vectors gets neither file.

## User types
A `user_types` entry declares a field type WireForge never looks inside — only its fixed `size_bytes`:
```yaml
user_types:
  Fixed16:
    cpp: app::Fixed16
    size_bytes: 2
    include: app/fixed16.hpp
```
WireForge emits only the size query, into `user_prelude.hpp`:
```cpp
namespace wireforge {
constexpr std::size_t max_wire_size(TypeTag<app::Fixed16> /*tag*/) noexcept { return 2U; }
constexpr std::size_t min_wire_size(TypeTag<app::Fixed16> /*tag*/) noexcept { return 2U; }
} // namespace wireforge
```
The user writes the type itself (`app/fixed16.hpp`, included by `user_prelude.hpp`) plus its `encode`/`decode`,
found by ADL exactly like a generated struct's:
```cpp
namespace app {
struct Fixed16 { std::int16_t raw{}; };
void encode(const Fixed16& value, wireforge::Encoder& encoder) noexcept {
    const auto bytes = std::bit_cast<std::array<std::uint8_t, 2U>>(value.raw);
    encoder.write_bytes(wireforge::Span<const std::uint8_t>(bytes));
}
void decode(Fixed16& value, wireforge::Decoder& decoder) noexcept {
    std::array<std::uint8_t, 2U> bytes{};
    decoder.read_bytes(wireforge::Span<std::uint8_t>(bytes));
    value.raw = std::bit_cast<std::int16_t>(bytes);
}
}
```
A field of this type (`occurs`, arrays and nesting inside other `types` all work) is used exactly like a struct
field; `Encoder::write_bytes`/`Decoder::read_bytes` (docs/RUNTIME.md) copy bytes onto/from the wire unchanged,
leaving the type's own encoding entirely up to these two functions. `Fixed16` must be default-constructible.

## C99 code (`--language c`)
`wireforge generate <project> -o <dir> --language c` writes `<project>/model.h` and `<project>/model.c` instead of the
C++ headers; `wireforge test ... --language c` also writes, builds and runs a GoogleTest project that calls the C
functions (the tests are C++, the code under test is C99), and the `coverage` target requires 100 % of the lines,
branches and functions of `model.c`. The rules are those of the C++ code: the same layout on the wire, the same checks
in the same order, the same statuses (`wireforge/c/`, docs/RUNTIME.md). What differs is the shape:
- Everything global starts with the namespace of the project, `::` replaced by `_` (`gnss::ubx` gives `gnss_ubx_`):
  types `<prefix>_<Type>`, functions `<prefix>_<Type>_init/validate/encode/decode`, macros `<prefix>_<NAME>`.
- An enum is an integer type with one macro per member (`<prefix>_<Enum>_<member>`) and `<prefix>_<Enum>_is_valid()`.
- An array of variable size (a counted array, a fill array, an optional field, a string) has a member
  `<name>_count` next to the array `<name>[capacity]`; a fixed array has none.
- `<prefix>_<Type>_init()` sets the defaults, `_validate()` returns the first error, a message has
  `_serialize()`/`_deserialize()` (its own encoding) and `_serialize_with()`/`_deserialize_with()` (an explicit
  `wf_encoding`). `<prefix>_<Type>_MIN_WIRE_SIZE` and `_MAX_WIRE_SIZE` are macros.
- A checksum is the function `<prefix>_checksum_<name>(data, length)`; a custom one calls the `c_impl` of the model.
- A field with `scale` has `<prefix>_physical_<Type>_<field>()` and `<prefix>_set_physical_<Type>_<field>()`.
- Each interface has `<project>/interfaces/<interface>.h` and `.c`, prefix `<prefix>_<interface>_`: the id and size
  macros (`_HEADER_SIZE`, `_TRAILER_SIZE`, `_MAX_FRAME_SIZE`, `_<Message>_ID`), `_header` and `_header_init()`,
  `_encode_<Message>()` (a complete frame), `_decode_header()`, a `_handler` struct of callbacks, `_dispatch_frame()`
  and a `_link` that owns its buffers (`_link_init()`, `_link_poll()`, `_link_send_<Message>()`, `_link_send_frame()`).
  A handler has, per message, `on_<Message>_received` (decoded) and `on_<Message>_raw` (the bytes before decoding;
  `<prefix>_<interface>_<Message>_default_raw()` decodes and forwards). A null callback means the default; a handler
  without callbacks ignores the messages. The link reads and writes through a `wf_transport` (two functions) and is
  used by one thread at a time (docs/RUNTIME.md).
Only the types and enums that a message or an interface uses get functions. User types are not supported yet.

In CMake, `wireforge_generate(<target> PROJECT <dir> LANGUAGE c)` generates the C code at configure time and makes
`<target>` a static C99 library of it, with the C runtime headers; it needs no C++ compiler, so it also builds for a
microcontroller (`examples/tutorial/drone_firmware`).

### C++17 and C99 compared
| | C++17 (default) | C99 (`--language c`) |
|---|---|---|
| Wire layout, checks, statuses, checksums, physical values | yes | the same rules |
| Framing, dispatch, link over a transport (interfaces) | yes | yes |
| Generated GoogleTest project, 100 % coverage gate | yes | yes (tests are C++, the code under test is C99) |
| User types (`user_types`) | yes | no: generation stops with "user type X is not supported in generated C" |
| Locks and atomics in the link | yes | no: a link belongs to one thread at a time (docs/RUNTIME.md) |
| Generated by the command line, Python API, server and web UI | yes | yes; MCP generates C++ only |
| CMake (`wireforge_generate`) | yes | yes, `LANGUAGE c` |
| Wireshark dissector | yes | the same (it depends on the model, not on the language) |

## Physical values
A field with `scale` and `offset` keeps its raw value in the struct (that is what the wire carries). Two functions next
to the struct convert (SR-24 limits them to plain integer and floating-point fields):
```cpp
double celsius = physical_Climate_temperature(climate);        // raw * scale + offset
bool fits = set_physical_Climate_temperature(climate, 21.5);   // false, field unchanged, when the raw value does not fit
```
The setter rounds to the nearest raw value (halves away from zero) and refuses a value outside the range of the type or
bit width, and NaN or infinity. `min` and `max` are not applied by the setter: they are checked by `validate()` and on
encode, as for every field. The generated tests set both ends of the raw range through the setter, a value just below
zero, values outside each end, and for floating-point fields a normal value and both infinities. The reference codec
adds the physical values of the scaled fields to its decode result (`physical`).

## Codec
```cpp
std::array<std::uint8_t, max_wire_size(wireforge::type_tag<SensorReading>)> buffer{};
std::size_t size = 0U;
wireforge::Status status = serialize(reading, wireforge::Span<std::uint8_t>(buffer), size);
status = deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size), reading);
```
- `serialize`/`deserialize` take an optional `wireforge::Encoding` (byte order, bit order, encode/decode range checks,
  trailing bytes); the default `k<Message>Encoding` uses the message and project settings. Interfaces pass their
  own encodings.
- Per type, `encode(const T&, wireforge::Encoder&)` and `decode(T&, wireforge::Decoder&)` hold the field sequence. The
  encoder/decoder keep the first error, so the functions are straight-line code; statuses and their order match the
  reference codec (SPEC section 16).
- Wire-only fields are produced by the encoder: consts, reserved values, checksums (`checksums::<name>` types) and count
  fields (from the array size); the decoder verifies them (`ConstMismatch`, `ChecksumMismatch`, count ranges).
- Every golden vector is a test of the generated test project (see below): the C++ value built from the JSON value
  must encode to the golden payload, and the payload must decode and re-encode unchanged, for every profile.

## Interfaces
```cpp
namespace node_c = demo::avionics::our_to_nodeC;   // inline namespace v1_0 holds the version
class App final : public node_c::Handler {
    void onCommandReceived(const demo::avionics::Command& command) noexcept override { /* ... */ }
};
App app;
node_c::Endpoint endpoint(app);                      // frame decoder + dispatcher + receiver
node_c::Link<MyTransport> link(transport, endpoint.decoder(), endpoint.dispatcher());
link.send([&](wireforge::Span<std::uint8_t> buffer, std::size_t& size) {
    return node_c::encode_frame(command, buffer, size, node_c::Header{/* seq */});
});
link.poll();                                         // calls app.onCommandReceived(...)
```
- Header fields with roles `sync`, `message_id`, `payload_length`, `protocol_version` and constants are written and
  checked automatically; other header fields are members of `Header`. A `protocol_version` field without `const` carries
  the interface major version.
- Supported subset (D30): header fields are byte-aligned integers; the trailer holds at most one checksum plus constants;
  checksums in headers are not supported. Unsupported layouts are reported by `wireforge.generate`. A header bit-field
  is supported when it is unsigned, not a constant, a whole number of bytes wide and the header bit order follows its
  byte order (`little` with `lsb_first`, `big` with `msb_first`): each such field then has bytes of its own and is
  written with `Encoder::write_width` (a 24-bit message id after an 8-bit component id, as in MAVLink 2).
- With `trim_trailing_zeros` the frame writer sends the payload without its trailing zero bytes
  (`wireforge::trimmed_size`, the first byte stays), and the default `on<Message>Raw` decodes the payload extended with
  zeros to the size of the message (`wireforge::ZeroExtended`); an override of `on<Message>Raw` gets the bytes as they
  were received. The generated tests add `TrimsTrailingZeros` (the frame of every default message has the trimmed
  size); the dispatch and golden tests send frames whose payloads were trimmed by the reference codec.
- Every message has a virtual `on<Message>Raw(const wireforge::RawMessage& raw)` in `Handler`. Its default decodes
  `raw.payload` and calls `on<Message>Received` when the decode succeeds, and returns the decode status. Override it to
  take the bytes without decoding: framing, the frame checksum and the message id were checked, the payload was not
  (no size, constant, range or payload checksum check). `raw.frame` is the whole frame. Call
  `Handler::on<Message>Raw(raw)` from an override to get both. The spans point into the link's receive buffer and are
  valid only during the call.

```cpp
class Relay final : public node_c::Handler {
    wireforge::Status onCommandRaw(const wireforge::RawMessage& raw) noexcept override {
        return forward(raw.frame);   // the frame as received, without decoding
    }
    void onCommandReceived(const demo::avionics::Command& /*command*/) noexcept override {}
};
```
- Both directions are generated for every interface (`direction` is documentation), so any interface can be looped
  back in tests. The golden tests encode every golden frame with `encode_frame`, compare it byte for byte and send it
  through a loopback `Link` to the generated `Handler`.

## Quality
Generated code compiles with the full warning set (`-Werror`), passes clang-tidy, cppcheck and the GCC analyzer and is
completely documented for doxygen. Snapshots of the generated examples live in `tests/python/snapshots/`; after an
intended change run `WIREFORGE_UPDATE_SNAPSHOTS=1 pytest tests/python/test_generate.py` and review the diff.
The generated code contains no branch that the generated tests cannot reach: checks that cannot fail are written with
`wireforge::status_if()` and `wireforge::size_if_ok()` (runtime, D46). The generated tests cover every enum member and
the decode errors and raw hooks of every message. The generated test project gates line, branch and function coverage
of the generated headers; for the examples each is 100 %.

## Generated test project
`python -m wireforge.generate <project> -o <dir> --tests [--plugins plugins.py] [--runtime runtime/include]
[--coverage-threshold 100]` also writes `<dir>/<project>/generated_tests/`, a standalone CMake project:

| File | Content |
|------|---------|
| `CMakeLists.txt` | one GoogleTest executable per profile (profile defines + `WIREFORGE_PROFILE_<name>`), the quality-policy warning flags as errors, `WIREFORGE_SANITIZE`, `WIREFORGE_CLANG_TIDY`, `WIREFORGE_COVERAGE` with a `coverage` target (gcovr, fails below `WIREFORGE_COVERAGE_MIN_LINE` percent line coverage, or below 100 percent branch and function coverage (`WIREFORGE_COVERAGE_MIN_BRANCH`, `WIREFORGE_COVERAGE_MIN_FUNCTION`), of the generated headers) |
| `CMakePresets.json` | `dev`, `asan-ubsan`, `tsan`, `coverage`, `clang-tidy` |
| `.clang-tidy` | the repository's check set, warnings as errors |
| `golden_test.cpp` | golden vectors: payload round trip, `encode_frame` equals the golden frame, `decode_header`, loopback through `Link`/`Endpoint`/`Handler` |
| `message_test.cpp` | per message: defaults (`T{}`), every scalar member and the first element of every array at its minimum and maximum and one step outside (when the member type can hold it), an invalid enum value, every variable-size array emptied (the pattern value fills arrays to capacity), and single-byte mutations of the encoded pattern (the first one per distinct decode error and field: checksum, count above the maximum, constant, range), truncation and an extra byte |
| `interface_test.cpp` | per interface: every message dispatched to its handler, byte-wise delivery, bytes before the sync, a corrupted frame followed by a good one, `encode_frame` into too small buffers, raw delivery without decoding, raw delivery forwarded to the decoder, decode errors returned by the raw hook |
| `handlers.hpp`, `test_support.hpp`, `wire_support.hpp` | recording handlers with a loopback link per interface; assertion and byte helpers |
| `diff_harness.cpp` | `<project>_diff[_<profile>]`: line-based harness of the differential test (see below) |
| `selftest.cpp` | `<project>_selftest[_<profile>]` (CTest): framework-free self-test without exceptions, RTTI or heap (golden vectors: payload, frame, loopback; decode cases), for embedded and cross targets |
| `fuzz_target.cpp` | `<project>_fuzz[_<profile>]` (with `-DWIREFORGE_FUZZ=ON`, Clang): libFuzzer target; the first byte selects a message (deserialize, then a value that encodes must decode and encode to the same bytes) or an interface (bytes streamed in input-defined chunks through the loopback link) |

Every expectation (payload bytes or the status) is computed by the reference codec (`wireforge.test_cases`), so the
generated code is checked against the reference semantics; `validate()` must report the status the reference encoder
reports with range checks forced on. Custom checksums need their Python plugin (`--plugins`) to compute expectations.
After resynchronisation (`resync: true` without sync bytes, or sync bytes inside the corrupted frame) the number of
rejected frames is not predictable byte by byte; the test then checks only that the corrupted frame is rejected.

The CMake paths to the runtime headers and the project's `user_includes.include_dirs` are relative to the test project
when they share a directory with it, otherwise absolute; both are cache variables (`WIREFORGE_RUNTIME_INCLUDE_DIR`,
`WIREFORGE_MODEL_DIR`). GoogleTest 1.14 is found or downloaded (`FETCHCONTENT_SOURCE_DIR_GOOGLETEST` reuses a local
copy). In this repository `tests/generated` builds the test sources of every example with all gates (warnings,
sanitizers, clang-tidy, cppcheck), and `quality/run_generated_tests.sh` builds each project standalone with its own
CMake files and runs its `coverage` target (line, branch and function threshold 100 %, the default of `--coverage-threshold`; the examples and the models of `tests/models/` reach 100 % in all three).

## Coverage of the generated code for any model
The 100 % gate of the generated headers is not shown on four examples alone; three checks stand behind it for every
model a user can write:
- `quality/feature_matrix.py` lists the features of the schema (every field type, bit-field form, occurrence form,
  header role, framing kind, checksum form, profile and condition) and fails when one is used by no built and measured
  model, or when a property of the schema is not mapped to a feature. The models are the examples, `tests/models/` and the
  random models below.
- `quality/random_models.py` writes valid random models (a fixed seed always gives the same model), generates their
  code and tests, builds them with the warning set as errors, runs them and measures the generated headers; any warning,
  failing test or coverage below 100 % of lines, branches and functions fails and leaves the model in
  `build/random-models/failures/<seed>/`. CI runs seeds 0 to 23 on every pull request and the workflow
  `random-models-nightly` runs 1000 more each night. A failure is a defect of the generator: fix it, reduce the
  model and add it to `tests/models/`.
- `SR-23`: an enum field without a default needs a member with the value 0, because `init()` starts it at zero.
- `SR-24`: `scale` and `offset` are allowed on plain integer and floating-point fields only.

A fourth check is made while the tests are written (`wireforge.coverage_points`): the model decides which points the
tests must exercise (the frame writer of every message with too little room, the receiver check of every message,
serialize without room, the decode error and the edit of every field, ...), every test records the points it
covers, and a point that no test covers stops the generation with "defect of the generator". A generator that tests only
the first message of an interface can no longer produce a project that misses the 100 % gate in a user's hands.

The checks found, and the generator now handles: the frame writer of every message of an interface (not only the first)
is tried with too little room; a raw-decode error test uses the byte order of the interface; a message that is on no
interface has a golden round trip of its own; serialize into a buffer without room is tested for every message; a message
that exists only in some profiles is left out of the profiles without it; an unused pattern helper is no longer emitted.
This is evidence over many models, not a proof; the user's own gate (`coverage` target, `wireforge docs --strict`) stays
the last line.

## Results of a test run
The `coverage` target also writes machine-readable results to `<build>/results/`, which `wireforge docs --results
<build>` turns into the test report, the resource document and the toolchain section (docs/LIFECYCLE.md):

On Windows the JUnit file of each test is named `<suite>.<test>.xml`; a path over 260 characters makes GoogleTest abort that test (exit code `0xc0000409`), so keep the project and build directories short or enable long paths.

| File | Content |
|------|---------|
| `junit/<executable>/*.xml` | GoogleTest result of every test, with the requirement ids it recorded |
| `ctest.xml` | CTest result, which includes the self-tests |
| `coverage.json`, `coverage_summary.json`, `coverage.cobertura.xml` | statement and branch coverage of the generated headers (gcovr; the Cobertura file is for other tools) |
| `coverage_runtime.json` | statement coverage of the runtime headers, only with `WIREFORGE_COVERAGE_RUNTIME=ON` |
| `toolchain.json` | compiler, CMake and gcovr versions, build type and the warning flags (no path) |

`WIREFORGE_COVERAGE_RUNTIME` adds a second report for the runtime headers and fails below
`WIREFORGE_COVERAGE_RUNTIME_MIN_LINE` percent of the statements (default 100). Only code that reaches the binary is
counted, which is the code that flies; `wireforge test --coverage-runtime` sets the option. With coverage on, the
tests are also built with `-fstack-usage`; the `.su` files next to the objects give the stack use of the generated
and runtime functions. The coverage build is `-O0`, so these values are indicative; measure on the target.

A line the tests do not reach is either closed with a test or justified in an optional `deviations.yaml` in the
project directory (not part of the model, no effect on the model hash):

```yaml
schema_version: 1
deviations:
  - kind: coverage
    file: wireforge/ring_buffer.hpp          # runtime header relative to the include directory
    function: "wireforge::StaticSpscByteRing<*>::push"   # as gcov names it; * matches anything
    lines: [118, 119]                        # optional; absent: every uncovered line of the function
    category: defensive                      # deactivated | defensive | unreachable-by-design
    rationale: The ring cannot be full with this link.
    reviewer: J. Doe
```
A deviation with `lines` stops matching when a runtime update moves them, which makes the gap unjustified again so
that the reason is reviewed. `kind: rule` entries are accepted by the schema and ignored for now.

## Differential and fuzz testing
`python -m wireforge.differential <project> --harness <dir>/<project>_diff[_<profile>] [--profile P] [--plugins F]
[--seed N] [--values N] [--mutations N] [--frames N] [--regressions DIR]` sends inputs to the generated harness and
compares every answer with the reference codec:

- payloads (`M <message> <hex>`): random valid values of every message (every field random inside its range, random
  array lengths), the boundary and decode cases of the generated tests, and random mutations (replaced byte, flipped
  bit, truncation, appended bytes); the decode status, the status of encoding the decoded value again and the
  re-encoded bytes must be equal;
- frames (`F <interface> <hex>`): random valid frames and their mutations through a loopback link; a frame the
  reference accepts must be dispatched with the same payload, a frame it rejects must not be dispatched cleanly
  (the stream decoder may split a rejected frame differently). Interfaces that resynchronise without sync bytes get
  only valid frames, and appended bytes are not used on framed interfaces (they start the next frame of a stream);
- stored inputs in `--regressions` are replayed first and new mismatches are appended to `found.txt` there
  (`tests/regressions/<project>/<profile or default>/`).

`--fuzz-seeds DIR` writes the golden payloads and frames as seeds of the generated fuzz target. The random generator is
a deterministic xorshift, so a seed reproduces a run. The runtime has its own fuzz targets (`tests/fuzz/`, preset
`fuzz`): `checksum_fuzz` (table and bitwise CRC engines agree, split updates equal one-shot updates, six CRC
variants) and `frame_decoder_fuzz` (three framings streamed in random chunks; every delivered frame is complete, has
the sync value and a correct checksum, the ring always gets free space).

Gates: `quality/run_generated_tests.sh` runs the differential test for every example and profile;
`quality/run_fuzz.sh` builds all fuzz targets with Clang and runs each for `WIREFORGE_FUZZ_SECONDS` (20 locally, 60 in
CI, 900 in the weekly `fuzz-long` workflow, whose four jobs take every fourth target each through
`WIREFORGE_FUZZ_SHARD=k/4`), `WIREFORGE_FUZZ_JOBS` targets at a time (default: the number of CPUs), seeded with `tests/regressions/fuzz/<target>/` and the golden vectors; a crash leaves its input in
`build/fuzz/artifacts/`.

