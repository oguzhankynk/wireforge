# Importers

`wireforge import-icd <source> <directory> --name <project> [--namespace NS] [--version V] [--endian big|little]
[--message Struct ...]` creates a new single-file project (`<directory>/project.yaml`, canonical layout) and validates
it. The server offers the same as `POST /api/import` (`format`: `csv`, `h`, or `xlsx` with base64 content); Python:
`wireforge.service.import_icd`, `wireforge.importers.from_csv/from_xlsx/from_c_header`.

## ICD tables (CSV, Excel)
One row per field; Excel reads the first sheet (`pip install 'wireforge[import]'` for openpyxl). Columns (header row,
any order, only `name`, `field` and `type` are required):

| Column | Meaning |
|--------|---------|
| `section` | `message` (default) or `type`; consecutive rows of one name form one entry |
| `name` | message or type name |
| `id` | message id (first row of the message; hex like `0x0110` stays hex) |
| `field`, `type` | field name and type (builtin, enum or type name) |
| `bits` | bit-field width |
| `occurs` | empty: one value; `N`: fixed array; `0..N`: counted array (`0..1` optional field); `M..N` with a minimum; `fill..N`: fills the payload |
| `count` | count field or expression of a counted array |
| `min`, `max`, `default` | numbers or expressions |
| `units`, `description` | documentation |

Sample: `examples/icd/telemetry_icd.csv` (a bit-field type, a nested struct, an optional field, a counted array).

## C headers
`#define NAME <integer>` becomes a define (defines without a value, such as include guards, are skipped), `enum` an enum
(`u8`, `u16` or `u32` by the largest value, `i32` with negative values), every `struct` (also `typedef struct`) whose
members are `uint8_t` ... `int64_t`, `bool`, `char`, `float`, `double`, earlier structs or enums becomes a type: arrays
`T name[N]` are fixed arrays (`N` may be a define), bit-fields `T name : n` keep their width. Structs named with
`--message` become messages (ids 1, 2, ... in header order); without `--message` every struct that no other struct
uses is a message. Pointers, unions, unknown types and non-integer defines are reported with file and line; nothing
is written then. Sample: `examples/icd/sensors.h`.

The importers write the definition only; byte order, interfaces, framing and checksums are added afterwards in the
editor, the CLI (`wireforge set/upsert`) or by hand.

## CAN databases (DBC)
`wireforge import-dbc <file.dbc> <directory> --name <project> [--namespace NS] [--version V] [--node NODE]
[--skip-unsupported]` writes a complete project, including the interfaces (`POST /api/import` with `format: dbc`,
`node` and `skip_unsupported`; Python: `wireforge.service.import_dbc`). The example is `examples/07_can_dbc`.

| DBC | WireForge |
|-----|-----------|
| `BO_` message | message with the CAN identifier as `id`; the frame is padded with reserved bits to the data length |
| `SG_` signal | field with `bits`; the type is the smallest one that holds the signal (`u8`..`u64`, `i8`..`i64`) |
| Intel signals (`@1`) | little-endian, LSB-first message |
| Motorola signals (`@0`) | big-endian, MSB-first message; the start bit (the most significant bit, counted in the usual DBC way) becomes a position in the bit stream |
| factor, offset | `scale`, `offset` (omitted when 1 and 0) |
| `[min\|max]` (physical) | raw `min`, `max`, narrowed to whole raw values with a note; omitted when the signal has no limits or the limits are the range of the type |
| `VAL_` | an enum `<Message>_<Signal>` (a member without a value 0 makes the first member the default); a table that cannot be an enum (equal member names, factor, offset, values outside the width) is kept in the description |
| `SIG_VALTYPE_` 1, 2 | `f32`, `f64` (the signal must fill 4 or 8 whole bytes) |
| `CM_` | `description` of the message or the signal |
| `GenMsgCycleTime` | sentence in the description of the message |
| `GenSigStartValue` | `default` |
| transmitter, receivers | with `--node N`: interface `N_tx` (messages N sends) and `N_rx` (messages N receives); without: one interface `can` (`both`) |

The interfaces have a header of `canId` (`u32`, the message id; bit 31 is the extended-frame flag, as in the DBC file
and in SocketCAN) and `dlc` (`u8`, the payload length) and the `length_prefixed` framing with a frame of at most
13 bytes. The importer describes the data of a CAN frame; the transport that delivers `canId` and `dlc` is the
application's.

What cannot be expressed is an error with file and line, or, with `--skip-unsupported`, a note and the message left out:
multiplexed signals (`M`, `mN`), extended multiplexing (`SG_MUL_VAL_`), frames of more than 8 bytes (CAN FD), signals of
both byte orders in one message, overlapping signals and signals outside the data length, a factor of 0, and floating
point signals that do not fill whole bytes. Statements that do not change a frame (`VERSION`, `BU_`, the other `BA_`
attributes, environment variables, signal groups) are counted in the notes.

## MAVLink dialects (XML)
`wireforge import-mavlink <dialect.xml> <directory> --name <project> [--namespace NS] [--version V]
[--message NAME ...]` writes a complete MAVLink 2 project from a MAVLink XML message definition file, such as
`common.xml`, `ardupilotmega.xml` or your own dialect (Python: `wireforge.service.import_mavlink`). The files the
dialect includes (`<include>`) are read from its directory, each once. `--message` (repeatable, MAVLink names such as
`HEARTBEAT`) takes only those messages and the enums they use.

| XML | WireForge |
|-----|-----------|
| `<message>` | message `HEARTBEAT` → `Heartbeat` with the message `id`; fields `time_boot_ms` → `timeBootMs` (a C++ keyword gets the suffix `Value`, with a note) |
| field order | the order MAVLink sends: the base fields by the size of their type, largest first (equal sizes keep their order), then the `<extensions/>` fields as written |
| `uint8_t` .. `int64_t`, `float`, `double` | `u8` .. `i64`, `f32`, `f64` |
| `char[N]`, `T[N]` | `char` with `occurs: {max: N}` (zero padded, as MAVLink strings), a fixed array |
| `uint8_t_mavlink_version` | `u8` with `const: 3` |
| `units`, description | `units`, `description`; an `enum` attribute and extension fields are named in the description |
| `<enum>` | an enum of the project with the smallest unsigned type of its values; an entry without a value takes the next one, `2**N` values are read; an entry with the value of an earlier one is left out with a note |

The interface `link` is the one of the published MAVLink 2 model (`wireforge init --protocol mavlink2`): the 24-bit
message id, payloads without their trailing zero bytes (`trim_trailing_zeros`) and the checksum with the CRC_EXTRA
byte of each message. The importer computes that byte from the definition, as the MAVLink generators do (the name of
the message and the type, name and array length of its base fields), and writes the list to
`app/include/mav2sum/crc_extra.h` and the Wireshark hook `wireshark/mav2sum.lua`; the checksum code itself is copied
from the published model. Fields keep their integer types even when they name an enum, because a MAVLink receiver must
accept values a newer dialect added. Signed frames are refused, as in the published model.

`tests/python/test_mavlink_xml.py` imports every dialect pymavlink ships and compares the CRC_EXTRA byte and the field
order of every message with pymavlink, and encodes every message of `common.xml` to the bytes pymavlink packs.
`tests/models/mavlink_import` (ten messages of `common.xml`) runs through all generated-code gates.

The file is read without a document type: a dialect with `<!DOCTYPE` or `<!ENTITY` is refused. The importer is a
command-line and Python function only; the server and the MCP server do not offer it.
