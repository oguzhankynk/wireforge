# Security policy

## Supported versions
Security fixes go into the latest release. Older versions are not patched; update to the latest release.

| Version | Supported |
|---------|-----------|
| latest release (0.4.x) | yes |
| older | no |

## Reporting a vulnerability
Please do not open a public issue. Report it privately through GitHub: **Security → Report a vulnerability** on this
repository (private vulnerability reporting). Describe what is affected (generated code, runtime, CLI, server, MCP
server, web UI), the version, and the steps or the model that show the problem.

You get an answer within a week. When the report is confirmed, the fix is released and the advisory published with
credit to you, unless you ask otherwise.

## Scope notes
- The generated code and the runtime (`runtime/`) run in your product: a defect there that a peer can trigger with
  bytes on the wire (out-of-bounds access, a crash, an endless loop) is a vulnerability.
- The server and the MCP server are meant for a trusted internal network (docs/SERVER.md, "Deployment and operation").
  The server runs the Python checksum plugins of the workspaces it serves, so it must only serve trusted workspaces;
  that is documented behaviour, not a vulnerability.
