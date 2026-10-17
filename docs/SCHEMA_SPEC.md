# WireForge schema specification, `schema_version: 1`

Machine-readable form: `schema/project.schema.json` (root file) and `schema/fragment.schema.json`
(included files). This document is normative for behaviour that JSON Schema cannot express; the
semantic rules in section 13 are enforced by `wireforge validate`. Working examples: `examples/`.

## 1. Files and layout
- A project is a directory with a root file `project.yaml` plus optional fragment files.
- Root keys: `schema_version`, `schema_minor`, `generator`, `project`, `version`, `description`, `namespace`,
  `include`, `defaults`, `user_includes`, `bundle` (export only), and the definition sections below.
  `project`/`version` identify the user's own project and ICD set; `schema_version`/`schema_minor`/`generator`
  identify the file format (section 11).
- `include` lists globs relative to the root file. Fragments contain **only** definition sections
  (`defines, profiles, checksums, enums, types, messages, interfaces`). Sections are merged;
  a name defined twice is an error (SR-1).
- YAML 1.2 is used (`on`/`off`/`yes`/`no` stay strings; `0x..` integers allowed).
- Names (`identifier`): `[A-Za-z_][A-Za-z0-9_]*`, must not be C++ keywords (SR-2).

## 2. Defaults and resolution
Effective value for `endian`, `bit_order`, `range_check` of a message **as used on an interface**:
message value, else interface value, else `defaults`, else built-in (`big`, `msb_first`,
`encode: true, decode: true`). Endianness and bit order are **message-level only**; there is no
per-field override. A message used on two interfaces with different effective endianness gets one codec
instantiation per endianness.

## 3. Built-in types and wire widths
| Type | Wire width | Notes |
|------|-----------|-------|
| `bool` | 8 bits (1..8 with `bits`) | 0 = false, nonzero decodes to true only when value is 1; other values are `OutOfRange` |
| `u8 u16 u32 u64` | 8/16/32/64 | unsigned |
| `i8 i16 i32 i64` | 8/16/32/64 | two's complement |
| `f32`, `f64` | 32/64 | IEEE-754 binary32/binary64 only, bit pattern via memcpy; no scale/offset |
| `char` | 8 | unsigned byte holding ASCII/Latin-1; arrays of `char` map to fixed strings |
| enum name | width of its underlying integer type | decoded value must be one of the listed values (else `OutOfRange` when range check is on) |
| struct name | sum of its fields | serialized inline, **no padding, no alignment, no hidden bytes** |

Multi-byte values are written byte by byte using the effective message endianness; no host layout is assumed.

## 4. Bit-fields
- `bits: n` (1..64) narrows a field to n bits. Consecutive bit-fields form a **bit group**; a struct whose
  fields all have `bits` is an inline bit group when used as a field.
- Packing follows the message `bit_order`: `msb_first` fills each byte from bit 7 downwards, `lsb_first` from
  bit 0 upwards. Fields larger than 8 bits continue across bytes in the same direction; byte order of a multi-byte
  group follows the message endianness.
- A bit group must total a multiple of 8 bits (SR-10). Use `reserved: true` fields to pad explicitly.
- Signed bit-fields are two's complement of the declared width and sign-extended on decode.
- `occurs` on a bit-field is not allowed in v1 (SR-11). `min`/`max` apply to the logical (decoded) value.

## 5. Field attributes
`name`, `type`, `description`, `units` (documentation only), `bits`, `min`, `max`, `default`, `const`,
`reserved`, `occurs`, `checksum`, `role` (header/trailer only), `if`, `since`, `deprecated`.
- `default`: initial value of `init()`; fields without default are zero-initialized. Arrays accept a list.
  Enum defaults use the value name. A default must lie in `[min, max]` (SR-6).
- `min`/`max`: numbers or expressions; checked on encode and/or decode per `range_check`.
  With `range_check.decode: false`, decoding succeeds and the caller can run `validate()`.
- `const`: written as the declared value, decode fails with `ConstMismatch` on difference.
- `reserved`: written as `default` (or 0), ignored on decode unless also `const`.

## 6. Occurrence model: optional fields and arrays
`occurs: {min, max, count | fill}` unifies optional fields and arrays.
- `max` (required): capacity, integer or expression; drives static storage.
- `count`: expression giving the number present on the wire (a previous field, bool allowed as 0/1).
  Count 0 means the field is absent. The count field must precede the field (SR-7).
- No `count`/`fill`: fixed array of exactly `max` elements (e.g. 3 on platformA, 5 on platformB).
- `fill: true`: elements fill the remaining payload; allowed on the last field of a message only and requires a
  constant element size (SR-8). `count` and `fill` are mutually exclusive.
- Optional field = `occurs: {max: 1, count: hasX}`. `min` defaults to 0; count outside `[min, max]` is `CountExceedsMax`.
- C++ storage is count + static array (`StaticVector`-like). Nothing is allocated dynamically.

## 7. Expression language
Used in `min`, `max`, `occurs.*`, `buffers.*`, `frame_size`, `max_frame_size`, checksum-independent sizes.
- Literals: decimal, `0x` hex, `0b` binary, `true`/`false`.
- Names: defines, earlier fields of the same message (by name), enum members as `Enum.member`.
- Operators (C precedence): `?:  ||  &&  |  ^  &  == !=  < <= > >=  << >>  + -  * / %  unary - ~ !`.
- Functions: `defined(NAME)`, `min(a,b)`, `max(a,b)`, `ceil_div(a,b)`, `sizeof(Type)` (bytes; fixed-size types only).
- Arithmetic is 64-bit signed; booleans convert to 0/1; division by zero in a constant expression is an error.
- Exact semantics (`wireforge.expr`, D20): any intermediate result outside the int64 range is an error; `/` truncates toward
  zero and `%` takes the sign of the dividend (C rules); shift counts must be 0..63 and the shifted value non-negative;
  `&&`, `||` and `?:` short-circuit; literals with a leading zero (`012`) are rejected (no octal); `ceil_div` rounds toward
  positive infinity. Generated C++ widens every operand to `std::int64_t` and calls `wireforge::expr::{min,max,ceil_div}`
  (`runtime/include/wireforge/expr_support.hpp`); `if:` conditions are emitted verbatim as `#if` expressions.
- `if:` conditions use the **preprocessor subset**: integers, defines, `defined()`, comparisons, `&& || !`, parentheses.
  They must not reference fields.

## 8. Defines, profiles and conditional layout
- `defines`: named integer constants with default values. Generated headers emit
  `defines::kNAME` constants that take the value of the macro `NAME` when the build defines it
  (`-DNAME=value`) and the model default otherwise; in `#if` conditions a define is written
  `(defined(NAME) ? NAME : default)`. The generator itself defines no macros.
- `profiles`: named sets of defines (e.g. `platformA`). Profiles do not change the generated source;
  they tell tools which variants to analyze and test.
- `if:` on a field, struct, message or interface-message entry emits a real `#if` in C++. The wire layout may
  differ per profile; each profile is analyzed separately.

## 9. Checksums
`checksums` declares reusable algorithms:
- `{algo: crc32}`: CRC-32/ISO-HDLC (poly 0x04C11DB7 reflected, init/xorout 0xFFFFFFFF, check 0xCBF43926).
- `{algo: crc, width, poly, init, refin, refout, xorout, check?}`: parametric CRC (Rocksoft model).
- `{algo: custom, impl, include, size_bytes}`: user type `impl` from header `include` satisfying the runtime checksum
  contract (`wireforge/checksum.hpp`, D16): `static constexpr std::size_t kSize` (= `size_bytes`, 1..8), a copyable
  `State`, `static State init() noexcept`, `static void update(State&, wireforge::Span<const std::uint8_t>) noexcept`,
  `static std::uint64_t finalize(const State&) noexcept`; checked with `static_assert(wireforge::is_checksum_v<impl>)`.
A field uses it with `checksum: {use, covers}`. `covers` is `{from, to}` (inclusive field range in the same struct)
or `{scope: preceding | payload | header_and_payload}`; the latter two are for header/trailer fields.
The field is computed on encode and verified on decode (`ChecksumMismatch`). Its width must equal the algorithm
width (SR-12). Checksums are optional everywhere.

## 10. Interfaces
An interface is one communication endpoint with a remote party (`our_to_nodeA`, `nodeB_to_our`, ...).
- `version` (semver, required): ICD version; may be carried on the wire by a `role: protocol_version` header field.
- `direction`: `tx`, `rx`, `both`. `endian`, `bit_order`, `range_check`: defaults for its messages.
- `header`: `none` or `{fields}`. Header field roles: `sync` (const marker), `message_id`, `payload_length`,
  `protocol_version`, `sequence`. A header with `message_id` enables header-based dispatch.
- `header: none` means raw dispatch: the interface carries exactly one message type, or none (raw bytes) (SR-14).
- `trailer`: optional fields after the payload (typically a checksum).
- `framing`: `none` (each transport read is one frame), `fixed` (`frame_size`), `sync_length` (header has `sync` and
  `payload_length`), `length_prefixed` (header has `payload_length`). Non-`none` kinds need `max_frame_size`
  (drives static buffer sizes). `length_counts` says what the length field covers (default `payload`).
  `resync: true` rescans after garbage or bad frames.
- `messages`: names or `{name, id?, if?, since?}`; `id` overrides the message id on this interface.
- `buffers.rx_bytes/tx_bytes`: optional circular/linear buffer sizes; default derived from `max_frame_size`.
- `allow_trailing_bytes`: default false; leftover payload bytes give `TrailingBytes`.

## 11. Versioning and file identity
| Key | Meaning | Written by |
|-----|---------|------------|
| `schema_version` (int) | file-format **major**; any incompatible change bumps it | tool, required in every file |
| `schema_minor` (int, default 0) | additive format changes inside a major | tool |
| `generator {name, version}` | WireForge release that wrote the file (informational) | tool on save/export |
| `version` (semver, root) | the user's project / ICD set | user |
| `interface.version` (semver) | one interface's ICD version, may travel on the wire | user |

Compatibility policy (`wireforge.versioning`, rule SR-19):
- format major newer than the tool supports: **error**, only this problem is reported (content cannot be trusted);
- format major older: **error** in normal loading; the file goes through `wireforge import`, which applies the registered
  migration chain (`register_migration(from_major, step)`, one step per major, run in order, result stamped with the
  current format). A missing step is an error ("no migration path");
- same major, newer minor: **error** (the file uses features this tool does not know);
- `generator` newer than the tool: **warning** only.

Before the first official release the format is unstable (0.x tool versions may change it without migrations). From the
release on, format 1 is frozen and every later major ships migrations from all earlier majors, so old interface files stay
importable. Generated C++ carries the format version, tool version and model hash in its banner.

Interface compatibility (`python -m wireforge.compat OLD NEW [--interface NAME]`, D26) compares each interface per
profile and classifies every difference:
- **breaking** (requires a major bump): any wire layout change of a header, trailer or message field (type, bits,
  occurs, const, reserved, checksum, role), inserted/removed/reordered fields, changed `min`/`max` or enum values,
  message id, effective endian or bit order, framing kind/sizes/`length_counts`, direction, removed messages;
  fields appended to a message unless the old interface has `allow_trailing_bytes: true`;
- **compatible** (minor): renamed fields, changed defaults, added messages, `resync` or `allow_trailing_bytes` changes,
  appended fields that old receivers ignore;
- **doc** (patch): descriptions and units.
The report fails when the new `interface.version` is not at least the required step above the old one.

## 12. Error model
Codec calls return `Status` values (docs/contracts/runtime-error-codes.md). Validation messages produced by tools
carry file, line and JSON pointer.

## 13. Semantic rules (beyond JSON Schema)
- SR-1 No duplicate names within a section across root and fragments.
- SR-2 Names are not C++ keywords and do not collide after C++ mangling.
- SR-3 Every `type` resolves to a builtin, enum or struct; struct graphs are acyclic.
- SR-4 Every checksum `use`, interface message, enum default and `covers` name resolves.
- SR-5 Expressions parse; names resolve; `if` conditions reference no fields.
- SR-6 `default`, `const` lie in `[min, max]` and fit the type/bit width; `min <= max`.
- SR-7 Fields referenced by `occurs.count` precede the occurring field and are integer/bool.
- SR-8 `fill` only on the last message field with constant element size; `max` is positive.
- SR-9 Message ids are unique per interface (after overrides); ids fit the header id field.
- SR-10 Every bit group totals a multiple of 8 bits; `bits` does not exceed the type width.
- SR-11 `occurs` is not combined with `bits`.
- SR-12 Checksum field width equals algorithm width; `covers` ranges are ordered and in the same struct.
- SR-13 `role` is used only in header/trailer; `payload_length` and `message_id` appear at most once per header.
- SR-14 Raw interfaces (`header: none`) have at most one message; `sync_length` requires `sync` + `payload_length`
  header fields, `length_prefixed` requires `payload_length`.
- SR-15 Enum values are unique per enum and fit the underlying type.
- SR-16 Each profile evaluates every expression and `if` without error, producing consistent layouts.
- SR-17 The layout adds no bytes beyond declared fields (no implicit padding is ever generated).
- SR-18 User constants: names unique and not colliding with defines or field names; `profile_values` name existing profiles;
  without `value` either every profile has a value or the constant is *opaque*: opaque constants may appear only in value
  ranges (`min`/`max`), never in anything that changes layout or buffer sizes (`occurs`, framing, buffers).
- SR-19 Version stamps: every file passes the compatibility policy of section 11; fragments carry `schema_version`.
- SR-20 User header paths and include directories are relative, without `..` segments (portable builds).

Implementation notes (`wireforge.validate`, D21-D23): every issue carries `[SR-n]`, file, line and JSON pointer.
SR-10 also limits a bit group to 64 bits (D19) and checks pure bit-field types where they are used; SR-12 verifies the
`check` value of parametric CRCs and limits custom checksums to 8 bytes; SR-16 also requires every message to fit
`frame_size`/`max_frame_size` together with header and trailer (dynamic arrays at capacity) and sizes/buffers to be
positive; SR-17 rejects `if:` on a field inside a bit group. The negative corpus is `tests/python/semantic_cases.yaml`.
Run `python -m wireforge.validate <project>`.

## 14. User includes: the gateway to application code
`user_includes` (root file only) lets definitions use constants and headers that belong to the application.
```yaml
user_includes:
  include_dirs: [app/include]                 # added to include paths of generated build files
  headers:                                    # emitted as #include in the generated user prelude, in order
    - { path: app/limits.hpp, style: quote }  # quote | angle ; optional `if:` preprocessor condition
  constants:                                  # integer constants usable in any expression, by plain name
    APP_MAX_FRAME:
      cpp: app::kMaxFrameBytes                # how generated C++ spells it (constexpr, enumerator or macro)
      value: 128                              # what the tools use (analysis, docs, tests, reference codec)
      profile_values: { platformB: 192 }      # per-profile override
  prelude:                                    # verbatim C++ lines after the headers (e.g. extern declarations)
    - "extern const std::uint32_t kLinkSpeed;"
```
- **Tool value vs real value.** The generator never parses C++. `value` is what analysis uses; the generated
  `user_prelude.hpp` contains `static_assert(static_cast<std::int64_t>(<cpp>) == <value>)`, so drift between model and
  application fails at compile time (per profile in the generated per-profile test builds).
- **Static memory needs compile-time constants.** `cpp` must name a constexpr variable, enumerator or macro. A link-time
  `extern const` may only be declared through `prelude` for runtime checks, never as a capacity (SR-18).
- **Opaque constants** (no `value`) are allowed in `min`/`max`: the check is emitted against the symbol, tools skip evaluation.
- `cpp` accepts only qualified identifiers (no expressions); header paths forbid quotes, angle brackets and newlines.
  `prelude` lines are emitted verbatim, are not analysed, and are the user's responsibility under the zero-warning policy.
- Every generated header includes the single generated `user_prelude.hpp` first.
- Out of scope for v1: user-defined C++ *types* as field types (candidate for a later minor version: `user_types`).

## 15. Export and import
- `wireforge export <project> -o bundle.yaml` merges all fragments into **one ordinary single-file project** (no `include`),
  stamped with the current format, plus `bundle: {content_hash, source_files}`. The sha256 covers definition content only
  (not stamps, not key order). Export refuses projects with structural or semantic errors.
- `wireforge import <bundle> <dir>` verifies the hash (mismatch is an error; `--no-verify` overrides), applies migrations for
  older formats, validates structure and unpacks into the standard layout (`project.yaml`, `profiles/`, `types/`,
  `messages/`, one file per interface), every file stamped. `--single-file` keeps one file. A non-empty destination is refused.
- A bundle is itself a valid project and can be loaded directly. Comments are not preserved by export (canonical form).
- Standard layout written by import: `project.yaml` (project keys, `defines`, `checksums`, `user_includes`, `include`),
  `types/types.yaml` (`enums`, `types`), `profiles/profiles.yaml`, `messages/messages.yaml`, `interfaces/<name>.yaml`.
  The hash is verified before migrations run (it covers the content the exporter wrote). API: `wireforge.bundle`.

## 16. Encoding rules shared by the reference codec and generated code
`wireforge.codec.ReferenceCodec` (one instance per profile) is the executable form of these rules; generated C++ must
produce the same bytes and statuses (golden vectors in `tests/golden/`, regenerated with
`python -m wireforge.golden <project> <file> [--plugins file.py]`).
- Fields are written in declaration order; fields whose `if:` is false in the profile do not exist.
- `const` fields are written with their declared value; decoding another value is `ConstMismatch`. `reserved` fields are
  written as `default` (or zero) and accepted with any value on decode.
- Occurrences: when `count` is a plain field name the encoder writes the element count into that field (the caller's
  value is ignored); other count expressions must equal the element count. Counts outside `[min, max]` are
  `CountExceedsMax`, checked before the range of the count field. Fixed arrays need exactly `max` elements; fixed `char`
  arrays are zero padded (decode strips trailing zeros). `fill` consumes the rest of the payload, which must be a
  multiple of the element size (`InvalidLength`).
- Values that do not fit the type or bit width are always `OutOfRange`; `min`/`max` and enum membership are checked when
  `range_check` is on for that direction (opaque constants are checked only by generated code). A `bool` wire value
  other than 0 or 1 is `OutOfRange`. NaN is outside every `min`/`max` range.
- Bit groups follow section 4 (one container per group, written with the message endian).
- Checksums: `preceding` covers the struct bytes before the checksum field, `{from, to}` the bytes of that field range,
  `payload`/`header_and_payload` the frame parts; a mismatch is `ChecksumMismatch`. Custom algorithms need a plugin
  (Python) or the `impl` type (C++).
- Frames: header, payload, trailer. `message_id` and `payload_length` header fields are filled by the encoder; a length
  that does not match the frame is `InvalidLength`; `fixed` framing needs exactly `frame_size` bytes (no padding is ever
  added) and every frame must fit `max_frame_size`. Decoding verifies the trailer checksum before looking up the id.
- Leftover payload bytes are `TrailingBytes` unless the interface sets `allow_trailing_bytes`.
