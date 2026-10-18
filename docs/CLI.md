# Command line and Python API

`pip install -e .` installs the `wireforge` command. Every command is a thin front end of `wireforge.service`, which
the server and the MCP server use as well, so every GUI or agent operation has a command line and a Python
equivalent.

| Command | What it does |
|---------|--------------|
| `wireforge init [dir] [--template telemetry\|command\|raw] [--name N] [--namespace NS] [--version V] [--list]` | a new project from a working template (never overwrites; the directory must be empty) |
| `wireforge ci <project> [--base REV] [--test] [-o DIR] [--summary FILE]` | the check of a CI system: validate, compare with the project at a git revision (wire compatibility and version bump), optionally build and run the tests; one Markdown summary (docs/integration/github_actions.md) |
| `wireforge import-icd <file> <dir> --name N` / `wireforge import-dbc <file.dbc> <dir> --name N [--node NODE] [--skip-unsupported]` | a project from an ICD table, a C header or a CAN database (docs/IMPORT.md) |
| `wireforge validate <project>` | structural and semantic validation (file, line, JSON pointer per issue) |
| `wireforge fmt <project> [--check]` | canonical layout of every project file (comments kept) |
| `wireforge migrate <project> [--check]` | upgrade the file format and refresh the version stamps |
| `wireforge generate <project> -o <dir> [--tests] [--plugins F]` | C++ code, with `--tests` the GoogleTest project |
| `wireforge wireshark <project> -o <file>.lua` | Wireshark Lua dissector plugin (docs/WIRESHARK.md) |
| `wireforge analyze <project> [--profile P] [--format markdown\|csv\|json]` | layout report / ICD (docs/LAYOUT.md) |
| `wireforge compat <old> <new> [--interface I]` | interface compatibility and version bump check |
| `wireforge reqs-import <project> <file> --baseline B` | take a baseline of high-level requirements (ReqIF or CSV) as `requirements/hlr_snapshot.yaml` |
| `wireforge reqs-lock <project>`, `reqs-rename-key`, `reqs-list` | number the low-level requirements in `llr_ids.lock`, keep a number across a rename, list them (docs/DO178C.md) |
| `wireforge docs <project> -o <dir> [--strict] [--results <build>] [--export reqif,csv] [--source-url T] [--since REV] [--rule-findings FILE]` | requirements, design, test and traceability documents (docs/LIFECYCLE.md) |
| `wireforge trace <project> [--format markdown\|csv] [-o file]` | requirements, model elements, generated code and tests (docs/SAFETY.md) |
| `wireforge test <project> -o <dir> [--coverage] [--coverage-runtime] [--generator Ninja]` | generate, build and run the generated tests; the coverage build writes `results/` for `docs --results` |
| `wireforge export <project> -o bundle.yaml` / `wireforge import <bundle> <dir>` | single-file bundles |
| `wireforge get <project> <pointer>` | value at a JSON pointer, e.g. `/messages/NavSolution/fields/2` |
| `wireforge set <project> <pointer> <value> [--dry-run] [--strict]` | set a value (`-` as last index appends) |
| `wireforge upsert <project> <section> <name> <definition>` | create or update a type, message, interface, ... |
| `wireforge remove <project> <pointer>` | delete an element |
| `wireforge encode <project> <message> <value> [--interface I] [--profile P]` | payload (and frame) as hex |
| `wireforge decode <project> <hex> (--message M \| --interface I)` | decoded value (payload or frame), with the physical values of fields that have `scale` or `offset` |

Values are YAML flow syntax (`85`, `deg`, `'{ name: age, type: u8 }'`) or a YAML file (`--file`). `--json` prints
`{"ok", "issues", "data", "text"}`. Exit codes: `0` success, `1` the project or check failed (validation errors,
`--check` differences, insufficient version bump, test failures, codec errors), `2` usage error, `3` the command could
not run (missing file or tool, invalid bundle).

## Editing keeps files diff-friendly
`set`, `upsert` and `remove` find the file that defines the entry (root file or fragment; new entries go to the file
that already holds that section, or to a new `<directory>/<name>.yaml` when the section keeps one entry per file) and edit it in place: comments, quoting, flow/block style and the order of named
entries stay as written, updated entries keep the nodes (and comments) of unchanged parts, new mappings in a list of
flow-style mappings are written in flow style. The file is then written in the canonical layout of `wireforge fmt`, so
a hand edit, a GUI edit and another hand edit give a diff of exactly the changed lines
(`tests/python/test_cli.py::test_hand_gui_hand_edits_give_minimal_diffs`). `--dry-run` returns the diff without
writing; `--strict` refuses (rolls back) an edit whose result has validation errors.

## Canonical layout (`wireforge fmt`)
- keys of known mappings in schema order (project root, defaults, profiles, checksums, enums, types, messages, fields,
  occurs, checksum use, interfaces, framing, interface messages, user includes); user-named entries keep their order;
- mappings indented by 2, sequences by 4 with the dash at 2, flow mappings written as `{ key: value }`;
- comments move with the key they precede; a comment above the first key of a mapping stays at the top.

## Python API
```python
from pathlib import Path
from wireforge import service

result = service.validate(Path("examples/01_basic_telemetry"))
assert result.ok, result.text
service.set_value(Path("my_project"), "/messages/Status/fields/0/max", 10)
report = service.analyze(Path("my_project"), export="json").data
```
`wireforge.cli.run(["validate", "my_project"])` runs a command in-process and returns its exit code and JSON result.
