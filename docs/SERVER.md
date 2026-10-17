# Server

`pip install -e ".[server]"`, then `wireforge serve --workspace <dir> [--host 127.0.0.1] [--port 8000] [--token T]`.
Every subdirectory of the workspace with a `project.yaml` is a project; its id is the directory name. The OpenAPI
document is served at `/openapi.json` (`wireforge serve --workspace <dir> --openapi openapi.json` writes it); the UI
types are generated from it. Code: `wireforge.server` (`storage.py`, `app.py`).

## Storage
- The files are the single source of truth and stay git-friendly: the server keeps no copy, every request reads the
  files, so edits made by hand, by `git checkout` or by the CLI are visible immediately.
- Entry and value edits go through the service layer (`wireforge.service`): comments, styles and the canonical layout
  are kept, so GUI edits produce minimal diffs (docs/CLI.md).
- Optimistic locking: every file has an ETag (sha256 of its bytes) and every project an ETag over all its files.
  File writes need `If-Match: <file ETag>` (`*` creates a new file); entry/value edits need `If-Match: <project ETag>`.
  A missing `If-Match` is `428`, a stale one `412` (reload and retry). Writes of one project are serialized by a lock,
  so of several concurrent writers with the same ETag exactly one wins.
- External changes: `GET /api/projects/{id}/wait?etag=E&timeout=S` long-polls until the project ETag differs from `E`
  (`200` with the new ETags) or the timeout passes (`304`); clients reload the changed files.
- Authentication: with `--token T` every `/api` route except `/api/health` needs `Authorization: Bearer T` (`401`
  otherwise). With `--oidc-issuer`/`--oidc-audience` it needs a JWT of that issuer instead (signature from the issuer's
  JWKS, `iss`, `aud`, `exp`/`nbf` with 60 s skew; `401` otherwise, `503` when the issuer cannot be reached); the
  `sub` and `email` claims live only for the request and are never stored. The JWKS is cached in memory for
  `--oidc-jwks-cache-seconds` (default 3600). See [Deployment and operation](#deployment-and-operation) for the limits.

## Routes
| Route | Meaning | CLI equivalent |
|-------|---------|----------------|
| `GET /api/projects`, `POST /api/projects` | list projects / create an empty project | `ls`, `mkdir` + `project.yaml` |
| `GET /api/projects/{id}` | entries per section, files, ETags, validation result | `wireforge validate` |
| `GET /api/projects/{id}/state`, `GET .../wait` | ETags; wait for a change | - |
| `POST /api/projects/{id}/validate` | validation result | `wireforge validate` |
| `GET/PUT /api/projects/{id}/files/{path}` | read/replace a file (text + ETag) | editor |
| `GET/PUT/DELETE /api/projects/{id}/entries/{section}/{name}` | read/create-or-update/delete an entry | `wireforge get/upsert/remove` |
| `GET/PUT/DELETE /api/projects/{id}/value?pointer=P` | read/set/delete any element | `wireforge get/set/remove` |
| `GET /api/projects/{id}/generate[?tests=false]` | zip of the generated code (see below) | `wireforge generate --tests` |
| `GET /api/projects/{id}/preview` | generated C++ files as text (nothing written) | `wireforge generate` |
| `GET /api/projects/{id}/analyze?profile=P&export=markdown\|csv\|json` | layout report (data) and ICD export (text) | `wireforge analyze` |
| `GET /api/projects/{id}/wireshark` | the Wireshark Lua dissector plugin (`<project>.lua`) | `wireforge wireshark` |
| `POST /api/compat` `{old, new, interfaces?}` | compatibility of two projects of the workspace | `wireforge compat` |
| `POST /api/projects/{id}/encode` `{message, value, interface?, profile?}` | payload and frame as hex | `wireforge encode` |
| `POST /api/projects/{id}/decode` `{hex, message? \| interface, profile?}` | decoded payload or frame | `wireforge decode` |
| `GET /api/projects/{id}/history` | git commits that changed the project | `git log` |
| `POST /api/projects/{id}/compat` `{revision, interfaces?}` | the project at a git revision vs the working copy | `wireforge compat` |

Edit bodies accept `dry_run` (return the diff, write nothing) and `strict` (refuse results with validation errors).
Responses carry the service `Result` (`ok`, `issues`, `data`, `text`) plus the new `etag`.

## Downloaded code
The zip of `generate` is self-contained: `<project>/include/<project>/` (generated headers and, unless
`tests=false`, `generated_tests/`), `<project>/runtime/include/wireforge/` (the runtime), `<project>/model/` (the
project definition with its `user_includes` directories) and a README. The CMake paths of the test project are
relative inside the zip, so `cmake -S include/<project>/generated_tests -B build && cmake --build build && ctest
--test-dir build` works after unpacking; `quality/run_generated_tests.sh` does exactly that for example 02.

Custom checksums: the reference codec (golden vectors, playground) loads the project's `plugins/*.py` file that defines
`PLUGINS`. The server runs that Python code, so it must only serve workspaces whose content is trusted.

## Deployment and operation
The server is a team tool for a trusted network, not a hardened multi-tenant service. Before exposing it:

- **Bind address.** `wireforge serve` listens on `127.0.0.1` by default. The Docker image listens on `0.0.0.0`
  **without a token**; anyone who reaches the port can read and change every project. Outside a single-user machine
  start it with a token:
  `docker run -p 8000:8000 -v "$PWD/projects:/workspace" wireforge wireforge serve --workspace /workspace --host 0.0.0.0 --token "$WIREFORGE_TOKEN"`.
- **Shared token or OIDC, no roles.** `--token` is a single bearer secret compared in constant time; rotate it by
  restarting the server with a new value. `--oidc-issuer URL --oidc-audience A` instead requires a JWT signed by that
  issuer (`pip install "wireforge[server,oidc]"`). The two options exclude each other. Either way there are no roles,
  per-project permissions or audit log (git history is the audit trail): every authenticated caller may read and
  write every project.
- **TLS.** The server speaks plain HTTP. Put it behind a reverse proxy that terminates TLS and limits request sizes
  (imports and file writes carry whole documents).
- **Token in the UI URL.** The web UI reads the token from `?token=<T>`, so it can end up in browser history and proxy
  logs; prefer a proxy that adds the `Authorization` header, or treat the token as low value and rotate it.
- **What the API can write.** File writes are limited to `.yaml`/`.yml` files inside a project directory (paths that
  leave it, including through symbolic links, are refused); include directories and headers must be relative without
  `..`. The API cannot create Python files.
- **Code that runs on the server.** Custom checksum plugins (`plugins/*.py`) are imported by the reference codec
  (encode, decode, playground, golden vectors). They can only come from the file system, so whoever may write the
  workspace directory (volume, git checkout) may run code in the server process: give that access only to trusted
  people.
- **Code that runs in your build.** A custom checksum's C++ implementation (`impl`, `include`) and `user_includes`
  headers are compiled into the generated code and its tests. Review them like any other source in the repository.
- **MCP server.** `wireforge mcp` talks over stdio to one local client; `run_tests` configures, builds and runs the
  generated test project with the local toolchain. Point it only at trusted workspaces (docs/MCP.md).
- **Importers.** Excel files are parsed with openpyxl; for files from outside the team, install `defusedxml` as
  openpyxl recommends.
- **Backups and history.** The server keeps no state of its own and does not commit: the workspace directory is
  everything. Keep it in git and back it up like any repository.

