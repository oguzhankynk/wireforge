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
  checksums in headers are not supported. Unsupported layouts are reported by `wireforge.generate`.
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
[--coverage-threshold 90]` also writes `<dir>/<project>/generated_tests/`, a standalone CMake project:

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
CMake files and runs its `coverage` target (line threshold 95 %, branch and function 100 %; the examples reach 100 % in all three).

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
CI), seeded with `tests/regressions/fuzz/<target>/` and the golden vectors; a crash leaves its input in
`build/fuzz/artifacts/`.

