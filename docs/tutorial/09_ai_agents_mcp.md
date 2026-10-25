# 9. AI agents (MCP)

**What we will do:** connect an AI agent to the workspace through the Model Context Protocol (MCP), so that it can
read, change, validate and test the model with the same rules as the command line.

## Connect
`wireforge mcp --workspace <dir>` serves MCP on standard input and output. With Claude Code, in the tutorial directory:
```sh
claude mcp add wireforge -- wireforge mcp --workspace "$PWD"
```
Other MCP clients (Claude Desktop, IDE assistants) take the same command in their configuration:
```json
{ "mcpServers": { "wireforge": { "command": "wireforge", "args": ["mcp", "--workspace", "/path/to/wireforge-tutorial"] } } }
```
<!-- tutorial: run -->
```sh
wireforge mcp --help
```

## Ask
Some requests that work well:
- "List the messages of drone and their sizes."
- "Add MAVLink's SYS_STATUS message to drone: these are its fields ... Show me the diff before you write it."
- "Encode a VFR_HUD frame with alt 12.5 and decode it again."
- "Make throttle of VfrHud a u8 and tell me what that breaks."
- "Generate the code and run the tests of drone."

The agent works through tools such as `list_messages`, `upsert_struct`, `validate_project`, `encode_sample`,
`compat_check`, `generate_code` and `run_tests`. Every write can be a dry run that returns the diff first, every write is
validated, and the files keep their canonical layout, so the change you review in git is exactly what the agent did.

**Good to know:** the agent edits the model, not the generated code, and WireForge checks every result, so a wrong
field size is a validation error or a failing compatibility check, not a silent bug. For a new MAVLink message, ask the
agent to add its CRC_EXTRA line too (chapter 2).

**More:** [MCP server](../MCP.md).

Next: [10. Team server](10_team_server.md)
