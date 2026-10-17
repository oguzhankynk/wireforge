# Web UI

React + TypeScript (strict), built with Vite (`ui/`). `npm run dev` serves it and proxies `/api` to
`wireforge serve` on port 8000; `npm run build` writes `ui/dist/`. Pass `?token=<T>` for a server started with
`--token`.

## Struct editor
- **Entries:** the project's enums, types, messages, interfaces, profiles and checksums, with "New ..." buttons.
- **Field table** of a type or message: name, type (builtins, enums, types), occurrence (single, optional 0..1,
  fixed array, counted array, fill payload) with capacity and count, bits, min, max, default, units, `if` condition,
  description, checksum (`use`) and covered scope; rows can be added, moved and removed. Message editors add the
  message id and the message's encoding settings: byte order, bit order and range checks on encode and decode, each
  either set or inherited from the project defaults.
- **Live validation:** problems the server would reject anyway (names, bits, capacities, counts) are listed while
  typing and block saving; after saving, the server's validation issues of the whole project are shown.
- **C++ preview:** the generated struct and its functions, from `GET /api/projects/{id}/preview`.
- **Checksums:** built-in CRC-32 or a parametric CRC (width, polynomial, initial value).

## Enums and project settings
- **Enum editor:** name, underlying type (`u8` to `i64`), description and members (name, value); "Add member" proposes
  the next value. Non-identifier names, duplicate names or values and non-integer values are listed and block saving.
- **Project settings** (header button): project name, version, description, C++ namespace, the default byte order,
  bit order and range checks, and the project defines. Each root key is written separately through
  `PUT`/`DELETE /api/projects/{id}/value?pointer=/<key>`, so only changed keys reach the file; a cleared field removes
  its key.

## Keys an editor does not show
Every editor owns a fixed set of keys and saves the loaded definition with only those replaced, so keys it does not
show (`if`, `since`, `bit_order` of an interface, ...) are kept unchanged. The following are edited in the YAML files
or through the CLI, not in the UI:
- `user_includes` and `constants`,
- custom checksum implementations (`impl`, `include`, `size_bytes`),
- an explicit covered range of a checksum field (`covers: {from, to}`; the UI offers the scope names only),
- `if` and `since` of whole entries.

## Interfaces, profiles, versions and tools
- **Interface editor:** version, description, direction, endian, header (or none: raw dispatch) and trailer fields
  with roles, constants and checksums, framing (kind, frame size, maximum frame size, length counts, resync), the
  message list with per-interface id overrides, buffer sizes and trailing-byte tolerance. A new interface of a project
  that keeps one interface per file gets its own file (`interfaces/<name>.yaml`).
- **Profile editor:** description and define values.
- **Tools** (tabs): C++ preview; **Layout** (offsets, sizes, bit positions and a byte bar per field of a message, link
  RAM per interface; per profile); **Playground** (encode a JSON value to payload/frame hex and decode hex, with
  interface and profile, using the reference codec); **Versions** (git history of the project directory and the
  compatibility of the working copy with any earlier commit).

Saving shows `Saved <section>/<name>.`; a save that finishes after the user moved to another entry does not change the
selection.

Saving uses `PUT /api/projects/{id}/entries/{section}/{name}` with the last project ETag, so concurrent edits are
detected (412) and files keep their comments and canonical layout.

## Types are generated, not written
`npm run gen:types` writes `src/generated/schema.ts` from `schema/project.schema.json` (json-schema-to-typescript) and
`src/generated/api.ts` from the server's OpenAPI document (openapi-typescript); `npm run check:types` fails when they
are outdated. The editor's conversions between table rows and schema fields live in `src/model.ts`.

## Tests
`npm test` (Vitest, jsdom): unit tests of `src/model.ts` and an end-to-end test that starts a real `wireforge serve` on
a temporary workspace (`tests/server.setup.ts`) and creates, through the UI, a checksum, a bit-field type and a message
with a nested struct, an optional field, a counted array and a CRC field; the project then validates and the preview
shows the generated struct. A second end-to-end test edits the three-interface example (interface framing and header
field, a profile define, a new interface, playground round trip, layout bit map, compatibility with the committed
baseline) and checks with `git diff` that exactly the edited lines changed. A third end-to-end test creates a project,
sets its settings and defines, adds an enum and a message with its own encoding settings, and checks that message and
interface keys the editors do not show survive a save. Gates: `npm run typecheck`, `npm run lint` (typescript-eslint strict type-checked, React
hooks), `npm run check:types`, `npm test`, `vite build` (`quality/run_all.sh`, CI job `ui`).
