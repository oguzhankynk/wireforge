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
