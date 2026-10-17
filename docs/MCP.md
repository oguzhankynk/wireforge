# MCP server

`pip install -e ".[mcp]"`, then `wireforge mcp --workspace <dir>` serves the Model Context Protocol on stdio (official
Python SDK, `MCPServer`). Every subdirectory of the workspace with a `project.yaml` is a project. Generated code goes to
`<workspace>/.wireforge/generated/`. Code: `wireforge.mcp_server`.

Example client configuration:
```json
{ "mcpServers": { "wireforge": { "command": "wireforge", "args": ["mcp", "--workspace", "/path/to/projects"] } } }
```

| Tool | What it does | CLI equivalent |
|------|--------------|----------------|
| `list_projects` | projects of the workspace | `ls` |
| `create_project(project, name, namespace?, version?)` | new empty project | `mkdir` + `project.yaml` |
| `list_messages(project)` | messages (with ids), types, interfaces | `wireforge analyze` |
| `get_message(project, name)` | definition of a message or type as written | `wireforge get` |
| `upsert_struct(project, section, name, definition, dry_run?)` | create/update a type or message | `wireforge upsert` |
| `upsert_entry(project, section, name, definition, dry_run?)` | create/update a checksum, enum, interface, profile, define | `wireforge set` |
| `validate_project(project)` | issues with file, line and JSON pointer | `wireforge validate` |
| `generate_code(project, tests?)` | C++ code and the GoogleTest project; lists the files | `wireforge generate --tests` |
| `run_tests(project, coverage?)` | build and run the generated tests (or the coverage target) | `wireforge test` |
| `analyze_layout(project, profile?, export?)` | sizes, offsets, bit maps, frames, link RAM | `wireforge analyze` |
| `lifecycle_docs(project, strict)` | requirements, design, test and traceability documents in `<workspace>/.wireforge/docs/<project>/` | `wireforge docs` |
| `wireshark_dissector(project)` | Wireshark Lua plugin in `<workspace>/.wireforge/generated/wireshark/<project>.lua` | `wireforge wireshark` |
| `compat_check(project, old_project? \| revision?)` | compatibility with another project or a git revision | `wireforge compat` |
| `encode_sample(project, message, value?, interface?, profile?)` | payload/frame hex; without `value` a sample with every field set | `wireforge encode` |
| `decode_hex(project, hex_bytes, message? \| interface, profile?)` | decoded payload or frame | `wireforge decode` |

Every result has `ok`, `issues`, `data` and `text`; writes with `dry_run: true` return the unified diff and change
nothing. Bad arguments (unknown project, section or message, malformed hex) are tool errors. Custom checksum plugins
(`plugins/*.py`) are executed, so serve trusted workspaces only.

## Agent scenario
`quality/agent_scenario.py` plays an agent that reads a short ICD text (a deterministic parser stands in for the
language model), creates the project, checksum, messages and interface through the tools, validates, generates and
runs the generated tests with a real build (`quality/run_generated_tests.sh`); `tests/python/test_mcp.py` runs the same
scenario with the build replaced and covers every tool and error path.
