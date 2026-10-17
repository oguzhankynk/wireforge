# C++ runtime (`runtime/include/wireforge/`)

Header-only C++17. No heap, no exceptions, no RTTI, no OS headers, no host-endianness or struct-layout assumptions.
Every fallible call returns `wireforge::Status`.

| Header | Content |
|--------|---------|
| `status.hpp` | `Status`, `is_ok()`, `to_string()` (see Status codes below) |
| `config.hpp` | build options: `WIREFORGE_CONFIG_HEADER`, `WIREFORGE_NO_STD_MUTEX`, `WIREFORGE_DEFAULT_LOCK`, `WIREFORGE_CRC_TABLELESS` |
| `version.hpp` | runtime version constants |
| `span.hpp` | `Span<T>` (dynamic extent, explicit constructors, `subspan`/`first`), `make_span` |
| `array_access.hpp` | `array_at()`: `std::array` element access without the exception path of `at()` |
| `static_vector.hpp` | `StaticVector<T, N>` (occurs storage: count + `std::array`), `FixedString<N>` (char arrays, always zero-terminated) |
| `lock.hpp` | lock policy contract `is_lock_policy_v`, `NullLock`, `StdMutexLock`, `DefaultLock`, `ScopedLock`, `RelaxedCounter` |
| `bit_utils.hpp` | `BitOrder`, masks, two's complement helpers, `reflect`, `narrow_to`, `BitPacker` / `BitUnpacker` |
| `byte_io.hpp` | `Endian`, `store_unsigned`/`load_unsigned`, `ByteWriter`, `ByteReader` (ints 1..8 bytes, f32/f64, bytes, bit groups, patching) |
| `ring_buffer.hpp` | `SpscByteRing` (lock-free), `StaticSpscByteRing<N>`, `LockedByteRing<L>` |
| `frame_layout.hpp` | `FieldLocation` shared by decoder and dispatcher |
| `frame_decoder.hpp` | `FrameFormat`, `FrameDecoder<Checksum>` (fixed, sync+length, length-prefixed; checksum verify; resync; counters) |
| `delegate.hpp` | `Delegate<R(Args...)>`: non-owning free/member/callable reference, no heap |
| `dispatcher.hpp` | `RawMessage` (payload and frame), `Dispatcher<N, L>` (header ids, fallback, frozen table), `RawDispatcher<L>` |
| `transport.hpp` | transport requirements `is_transport_v`, `ITransport` base, `LoopbackTransport<N>` |
| `link.hpp` | `Link<Transport, Decoder, Dispatch, MaxFrame, RxCapacity, L>`, `Unframed`, `PollResult` |
| `checksum.hpp` | checksum contract `is_checksum_v`, `compute_checksum`, `Crc<...>` (Rocksoft), `Crc32`, `Crc16CcittFalse` |
| `codec_support.hpp` | `Encoding`, `Encoder`/`Decoder` with a sticky first error (used by generated codecs) |
| `validation.hpp` | range, bit-width and count checks used by generated `validate()`; `status_if()`, `size_if_ok()` and `first_error()` keep generated code free of branches |
| `expr_support.hpp` | integer helpers used by generated size and count expressions |

## Status codes
`enum class Status : std::uint8_t`; every fallible call returns one, nothing throws.

| Code | Meaning |
|------|---------|
| `Ok` | Success |
| `BufferTooSmall` | The output buffer cannot hold the encoded data |
| `Truncated` | The input ended before the data was complete |
| `OutOfRange` | A value violates its min/max, enum or bit-width range |
| `CountExceedsMax` | An element count is above the capacity (or below the minimum) |
| `ConstMismatch` | A constant field differs from its declared value |
| `ChecksumMismatch` | A received checksum does not match the recomputed one |
| `UnknownMessageId` | A message id is not known on this interface |
| `InvalidLength` | A length field is inconsistent with the frame or buffer |
| `TrailingBytes` | The payload was not fully consumed |
| `InvalidState` | The API was misused (invalid argument or call sequence) |

## Byte and bit IO
- Integers are written byte by byte with shifts; widths 1..8 bytes; signed values as two's complement, decoded with
  explicit sign extension (no implementation-defined conversions). Floats go through their IEEE-754 bit pattern (D11).
- A failing call changes neither the position nor the output. `write_unsigned_at` patches an already written range
  (length fields, checksums); `peek_unsigned_at` inspects a header without consuming it.
- Bit groups (D19): `BitPacker(total_bits, order)` collects fields into one container integer, `write_bits` writes it with
  the message endianness; `read_bits` + `BitUnpacker` reverse it. Endianness is a function argument, so a constant
  argument in generated code is resolved at compile time (all IO except floats is `constexpr`).
- `Encoder::write_bytes(Span<const std::uint8_t>)` / `Decoder::read_bytes(Span<std::uint8_t>)` copy bytes onto/from
  the wire unchanged, no endian or bit-order transform: the only IO a user type's own `encode`/`decode` needs.

## Thread safety
- `SpscByteRing`: exactly one producer (`write`) and one consumer (`peek`, `read`, `consume`); indices run in
  `[0, 2*capacity)` so the whole storage is usable; release/acquire atomics publish data. `reset()` needs both sides idle.
- `LockedByteRing<L>` runs every operation under lock policy `L`; `write_all` keeps records from concurrent producers whole.
- Lock policies are template parameters. An RTOS port provides a default-constructible type with `lock() noexcept` and
  `unlock() noexcept` and sets `WIREFORGE_DEFAULT_LOCK` (and `WIREFORGE_NO_STD_MUTEX` to drop `<mutex>`), D17.

## Checksums (D4, D16)
`Crc<Width, Poly, Init, RefIn, RefOut, XorOut, UseTable>` covers widths 1..64 with a left-aligned 64-bit register; the
table mode shares one 2 KiB constexpr table per polynomial, the table-less mode loops over bits. Custom algorithms follow
the contract in `checksum.hpp`; generated code asserts `is_checksum_v<Impl>`.

## Frame decoding
`FrameDecoder<Checksum>` reads from any ring with `size/capacity/peek/consume`. Incomplete frames stay in the ring, so
results do not depend on chunking. `next()` returns `Ok` (frame copied out), `Truncated` (need more bytes) or a rejection
(`InvalidLength`, `ChecksumMismatch`, `BufferTooSmall`) after which decoding continues. With `resync` a rejection skips one
byte and rescans; without it every buffered byte is dropped. Bytes that cannot start a frame (wrong sync) are skipped
silently and counted in `discarded_bytes`. Framing `none` needs no decoder (each transport read is one frame).

## Dispatching
Handlers are registered (`add`, `set_fallback`, `set_handler`) before `start()`, under the lock policy. `start()` sorts and
freezes the table; afterwards `dispatch()`/`dispatch_frame()` take no lock and can run on several threads, and
registration returns `InvalidState`. Unknown ids go to the fallback handler or return `UnknownMessageId`.
Handlers receive a `RawMessage` with the payload and the whole frame. `dispatch(id, payload)` passes the payload as
both spans; `dispatch_frame()` slices the payload out of the frame. Both spans are valid only during the call.

## Transport and link
A transport has `send(Span<const uint8_t>) -> size_t` and `receive(Span<uint8_t>) -> size_t`, both non-blocking. An
existing driver interface is connected by a small adapter class, either a plain type or derived from
`ITransport`. `Link::poll()` (one receiving thread) moves transport bytes into its ring, decodes and dispatches every
complete frame and returns counts plus the last error. `Link::send(encode)` runs the encoder on the preallocated tx
buffer under the tx lock and hands the frame to the transport; a transport that stops accepting bytes mid-frame gives
`BufferTooSmall` (the receiver resynchronises). With `Unframed` each transport read is one frame; a read longer than `MaxFrame` is rejected as `InvalidLength` and
whatever the transport still holds is dropped.

## Tests
`tests/io_test.cpp`, `containers_test.cpp`, `ring_buffer_test.cpp` (model fuzz, SPSC and MPMC stress under TSan),
`frame_decoder_test.cpp` (chunking equivalence, garbage, bad checksum/length, all framings), `dispatcher_test.cpp` (delegates, lookup, life cycle, concurrent dispatch and registration), `link_test.cpp` (loopback, garbage, full transport, ITransport adapter, unframed, sender/poller threads), `checksum_test.cpp` (CRC catalogue check values in both modes, custom Fletcher-16), `lock_config_test.cpp`
(built with `WIREFORGE_NO_STD_MUTEX`). Each suite proves zero heap use with the allocation trap.

## Portability
The runtime is verified on big-endian (s390x) and 32-bit (i686) targets: `quality/run_portability.sh` cross compiles
every test suite with the full warning set and runs it under qemu (docs/DECISIONS.md D34). Conversions whose source
width depends on the platform use `narrow_to<T>(value)` (`bit_utils.hpp`); counters accept any unsigned amount.

## Requirements
`runtime/requirements.yaml` holds the requirements of the runtime (`RTR-<header>-NNN`): the text, the header and the
symbols that implement it (checked against the header), the kinds of generated low-level requirements it realizes
and how it is verified (`test`, `analysis` or `review`). The file is written and reviewed by people; the first
version was drafted from the Doxygen comments and the tests, so it describes the code and does not yet stand apart from
it. Every unit test records the requirements it verifies as the first statement of its body
(`::testing::Test::RecordProperty("requirements", "RTR-...")`); only the tests of the allocation trap, which check the
test tooling, record none. Ids are never reused: a dropped requirement stays with `retired: true`.

Claims about several threads (the standard mutex lock, the single-producer ring, the locked ring, concurrent dispatch)
have `verification: analysis`: the evidence is for single-threaded use with the null lock, and the rest is the
integrator's to verify on the target. `LoopbackTransport` is a test helper and not part of that claim.
`array_access.hpp`, `expr_support.hpp`, `frame_layout.hpp` and `config.hpp` have no requirement yet.

`wireforge docs --runtime -o <dir> [--results <build>] [--strict]` writes the dossier (requirements, design, tests,
report, traceability, environment) of the runtime; `quality/run_coverage.sh` writes it to `build/evidence-runtime`
and CI publishes it. A project's documents name the runtime requirements that realize their low-level requirements
and the runtime version, and do not copy the dossier (docs/LIFECYCLE.md).

The `coverage` job of CI measures the runtime headers with the runtime tests and the tests of the generated code of the
examples (the differential harness, the fuzz targets replayed with golden inputs mutated at every byte and cut at
every length, and all read lengths of a link) and fails below the line coverage it states in `ci.yml`. Template
instances are counted one by one, as gcovr reports them, so a `Link` or `FrameDecoder` instance that is only compiled
lowers the figure. Gaps are closed with tests; there are no exclusions.

