"""REST API over a workspace of projects (FastAPI).

Reads go to the files on disk; writes use optimistic locking with ETags (`If-Match`) and are
serialized per project. Entry and value edits use the service layer, so they keep comments and
the canonical layout. `GET /api/projects/{id}/wait` long-polls until a project changes, which
lets clients reload after edits made outside the server. Every operation has a CLI equivalent
(docs/CLI.md); the OpenAPI document is served at `/openapi.json`.
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from wireforge import importers, resources, service, versioning
from wireforge.loader import SECTIONS, load_project
from wireforge.server.oidc import OidcConfig, OidcUnavailableError, OidcVerifier
from wireforge.server.storage import StorageError, Workspace, check_match
from wireforge.service import EditOptions, Result, ServiceError

if TYPE_CHECKING:
    from collections.abc import Callable

PROJECT_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
MAX_WAIT_SECONDS = 60.0


class FileWrite(BaseModel):
    """New contents of a project file."""

    text: str


class EntryWrite(BaseModel):
    """Definition of a section entry (type, message, interface, ...)."""

    definition: dict[str, Any]
    dry_run: bool = False
    strict: bool = False


class ValueWrite(BaseModel):
    """Value for a JSON pointer."""

    value: Any = None
    dry_run: bool = False
    strict: bool = False


class EditFlags(BaseModel):
    """Options of a delete."""

    dry_run: bool = False
    strict: bool = False


class ProjectCreate(BaseModel):
    """A new, empty project."""

    id: str = Field(pattern=PROJECT_ID.pattern)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    namespace: str | None = None
    version: str = "0.1.0"


class CodecRequest(BaseModel):
    """Message, interface and profile of an encode or decode request."""

    message: str | None = None
    interface: str | None = None
    profile: str | None = None


class EncodeRequest(CodecRequest):
    """A message value to encode."""

    value: dict[str, Any]


class DecodeRequest(CodecRequest):
    """Hex bytes to decode (a payload with `message`, else a frame of `interface`)."""

    hex: str


class CompatRequest(BaseModel):
    """Two projects of the workspace to compare (old and new version)."""

    old: str
    new: str
    interfaces: list[str] | None = None


class RevisionCompat(BaseModel):
    """A git revision to compare the working copy of a project with."""

    revision: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.^~/-]*$")
    interfaces: list[str] | None = None


class ImportRequest(BaseModel):
    """An ICD to import as a new project: CSV or C header text, or a base64 Excel workbook."""

    id: str = Field(pattern=PROJECT_ID.pattern)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    format: Literal["csv", "xlsx", "h"]
    content: str
    namespace: str | None = None
    version: str = "0.1.0"
    endian: Literal["big", "little"] | None = None
    messages: list[str] | None = None


def workspace(request: Request) -> Workspace:
    """Return the workspace of the running application."""
    store: Workspace = request.app.state.workspace
    return store


def authorize(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    """Check the OIDC bearer JWT or the shared bearer token, whichever the application has."""
    verifier: OidcVerifier | None = request.app.state.oidc_verifier
    token: str | None = request.app.state.token
    if verifier is None and token is None:
        return
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if verifier is not None:
        try:
            request.state.user = verifier.verify(authorization.removeprefix("Bearer "))
        except ValueError as error:
            raise HTTPException(401, str(error)) from error
        except OidcUnavailableError as error:
            raise HTTPException(503, str(error)) from error
        return
    if not secrets.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(401, "missing or wrong bearer token")


@dataclass(frozen=True)
class WriteContext:
    """Workspace and If-Match header of a write request."""

    store: Workspace
    if_match: str | None


def write_context(
    store: Annotated[Workspace, Depends(workspace)],
    if_match: Annotated[str | None, Header()] = None,
) -> WriteContext:
    """Collect what every write needs."""
    return WriteContext(store, if_match)


Store = Annotated[Workspace, Depends(workspace)]
Write = Annotated[WriteContext, Depends(write_context)]
router = APIRouter(prefix="/api", dependencies=[Depends(authorize)])


class IssueModel(BaseModel):
    """A validation issue: file, line, JSON pointer and message."""

    file: str
    line: int | None
    pointer: str
    message: str
    severity: str = "error"


class ResultResponse(BaseModel):
    """Result of a service operation; some routes add fields such as `etag`."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    issues: list[IssueModel]
    data: dict[str, Any]
    text: str
    etag: str | None = None


def _result(result: Result, **extra: object) -> ResultResponse:
    return ResultResponse.model_validate({**result.to_dict(), **extra})


def _service_error(error: ServiceError) -> HTTPException:
    status = 404 if "not found" in str(error) or "no such" in str(error) else 400
    return HTTPException(status, str(error))


def _edit(context: WriteContext, project: str, action: Callable[[Path], Result]) -> ResultResponse:
    store = context.store
    with store.lock(project):
        check_match(store.state(project).etag, context.if_match)
        try:
            result = action(store.project_dir(project))
        except ServiceError as error:
            raise _service_error(error) from error
    return _result(result, etag=store.state(project).etag)


def _read(store: Workspace, project: str, pointer: str) -> ResultResponse:
    try:
        result = service.get(store.project_dir(project), pointer)
    except ServiceError as error:
        raise _service_error(error) from error
    return _result(result, etag=store.state(project).etag)


# --- projects -----------------------------------------------------------------------------------


@router.get("/projects")
def list_projects(store: Store) -> list[dict[str, Any]]:
    """List the projects of the workspace."""
    result = []
    for identifier in store.projects():
        model = load_project(store.project_dir(identifier)).project
        result.append(
            {
                "id": identifier,
                "name": model.name if model else None,
                "version": model.version if model else None,
                "etag": store.state(identifier).etag,
            }
        )
    return result


@router.post("/import", status_code=201)
def import_icd(store: Store, body: ImportRequest) -> ResultResponse:
    """Import an ICD table or C header as a new project of the workspace."""
    destination = store.root / body.id
    if destination.exists():
        raise HTTPException(409, f"{body.id} already exists")
    options = importers.Project(body.name, body.namespace, body.version, body.endian)
    with tempfile.TemporaryDirectory(prefix="wireforge-import-") as temporary:
        source = Path(temporary) / f"icd.{body.format}"
        try:
            data = base64.b64decode(body.content, validate=True) if body.format == "xlsx" else None
        except binascii.Error as error:
            raise HTTPException(400, "content is not base64") from error
        if data is None:
            source.write_text(body.content, encoding="utf-8")
        else:
            source.write_bytes(data)
        try:
            result = service.import_icd(source, destination, options, body.messages)
        except ServiceError as error:
            raise _service_error(error) from error
    if not result.ok and destination.exists() and not any(destination.iterdir()):
        destination.rmdir()
    return _result(result)


@router.post("/projects", status_code=201)
def create_project(store: Store, body: ProjectCreate) -> dict[str, Any]:
    """Create an empty project directory."""
    directory = store.root / body.id
    if directory.exists():
        raise HTTPException(409, f"{body.id} already exists")
    directory.mkdir(parents=True)
    lines = ["schema_version: 1", f"project: {body.name}", f'version: "{body.version}"']
    if body.namespace:
        lines.append(f"namespace: {body.namespace}")
    (directory / "project.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"id": body.id, "etag": store.state(body.id).etag}


@router.get("/projects/{project}")
def get_project(store: Store, project: str) -> ResultResponse:
    """Return a project: entries per section, files, ETags and validation issues."""
    state = store.state(project)
    model = load_project(store.project_dir(project)).project
    sections = {name: list(getattr(model, name)) for name in SECTIONS} if model else {}
    checked = service.validate(store.project_dir(project))
    return _result(checked, id=project, etag=state.etag, files=state.files, sections=sections)


@router.get("/projects/{project}/state")
def project_state(store: Store, project: str) -> dict[str, Any]:
    """Return the ETags of a project and its files."""
    current = store.state(project)
    return {"etag": current.etag, "files": current.files}


@router.get("/projects/{project}/wait")
def wait_for_change(
    store: Store,
    project: str,
    etag: str,
    timeout: Annotated[float, Query(ge=0.0, le=MAX_WAIT_SECONDS)] = 30.0,
) -> Response:
    """Return the new state once the project ETag differs from `etag` (304 after the timeout)."""
    changed = store.wait(project, etag, timeout)
    if changed is None:
        return Response(status_code=304)
    return JSONResponse({"etag": changed.etag, "files": changed.files})


@router.post("/projects/{project}/validate")
def validate_project(store: Store, project: str) -> ResultResponse:
    """Validate a project."""
    return _result(service.validate(store.project_dir(project)))


# --- files --------------------------------------------------------------------------------------


@router.get("/projects/{project}/files/{path:path}")
def read_file(store: Store, project: str, path: str, response: Response) -> dict[str, str]:
    """Return the text and ETag of a project file."""
    text, tag = store.read(project, path)
    response.headers["ETag"] = f'"{tag}"'
    return {"path": path, "text": text, "etag": tag}


@router.put("/projects/{project}/files/{path:path}")
def write_file(
    context: Write, project: str, path: str, body: FileWrite, response: Response
) -> ResultResponse:
    """Replace a project file (If-Match: its ETag, or `*` for a new file)."""
    store = context.store
    tag = store.write(project, path, body.text, context.if_match)
    response.headers["ETag"] = f'"{tag}"'
    checked = service.validate(store.project_dir(project))
    return _result(checked, etag=tag, project_etag=store.state(project).etag)


# --- entries and values -------------------------------------------------------------------------


@router.get("/projects/{project}/entries/{section}/{name}")
def get_entry(store: Store, project: str, section: str, name: str) -> ResultResponse:
    """Return a section entry as written in the project files."""
    return _read(store, project, f"/{section}/{name}")


@router.put("/projects/{project}/entries/{section}/{name}")
def put_entry(
    context: Write, project: str, section: str, name: str, body: EntryWrite
) -> ResultResponse:
    """Create or update a section entry (If-Match: the project ETag)."""
    options = EditOptions(dry_run=body.dry_run, strict=body.strict)

    def action(location: Path) -> Result:
        return service.upsert(location, section, name, body.definition, options)

    return _edit(context, project, action)


@router.delete("/projects/{project}/entries/{section}/{name}")
def delete_entry(
    context: Write,
    project: str,
    section: str,
    name: str,
    body: Annotated[EditFlags | None, Body()] = None,
) -> ResultResponse:
    """Delete a section entry (If-Match: the project ETag)."""
    flags = body or EditFlags()
    options = EditOptions(dry_run=flags.dry_run, strict=flags.strict)

    def action(location: Path) -> Result:
        return service.remove(location, f"/{section}/{name}", options)

    return _edit(context, project, action)


@router.get("/projects/{project}/value")
def get_value(store: Store, project: str, pointer: str) -> ResultResponse:
    """Return the value at a JSON pointer."""
    return _read(store, project, pointer)


@router.put("/projects/{project}/value")
def put_value(context: Write, project: str, pointer: str, body: ValueWrite) -> ResultResponse:
    """Set the value at a JSON pointer (If-Match: the project ETag)."""
    options = EditOptions(dry_run=body.dry_run, strict=body.strict)

    def action(location: Path) -> Result:
        return service.set_value(location, pointer, body.value, options)

    return _edit(context, project, action)


@router.delete("/projects/{project}/value")
def delete_value(context: Write, project: str, pointer: str) -> ResultResponse:
    """Delete the element at a JSON pointer (If-Match: the project ETag)."""

    def action(location: Path) -> Result:
        return service.remove(location, pointer)

    return _edit(context, project, action)


# --- generate, analyze, compat, playground -----------------------------------------------------


@router.get("/projects/{project}/generate")
def generate_zip(store: Store, project: str, *, tests: bool = True) -> Response:
    """Download the generated code as a self-contained zip (with the test project)."""
    result, data = service.generate_archive(store.project_dir(project), tests=tests)
    if not result.ok:
        return JSONResponse(result.to_dict(), status_code=422)
    name = f"{result.data['project']}-generated.zip"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    return Response(data, media_type="application/zip", headers=headers)


@router.get("/projects/{project}/preview")
def preview(store: Store, project: str) -> ResultResponse:
    """Return the generated C++ files without writing them."""
    return _result(service.preview(store.project_dir(project)))


@router.get("/projects/{project}/wireshark")
def wireshark_plugin(store: Store, project: str) -> Response:
    """Download the Wireshark Lua dissector plugin of the project."""
    result, text = service.wireshark_plugin(store.project_dir(project))
    if not result.ok:
        return JSONResponse(result.to_dict(), status_code=422)
    name = f"{result.data['project']}.lua"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    return Response(text, media_type="text/x-lua; charset=utf-8", headers=headers)


@router.get("/projects/{project}/analyze")
def analyze(
    store: Store, project: str, profile: str | None = None, export: str = "markdown"
) -> ResultResponse:
    """Return the layout report (data) and its export (`text`: markdown, csv or json)."""
    if export not in {"markdown", "csv", "json"}:
        raise HTTPException(400, f"unknown export {export!r}")
    try:
        return _result(service.analyze(store.project_dir(project), profile, export))
    except ServiceError as error:
        raise _service_error(error) from error


@router.post("/compat")
def compatibility(store: Store, body: CompatRequest) -> ResultResponse:
    """Compare two projects of the workspace (old and new version of the interfaces)."""
    old, new = store.project_dir(body.old), store.project_dir(body.new)
    return _result(service.compatibility(old, new, body.interfaces))


@router.get("/projects/{project}/history")
def project_history(store: Store, project: str) -> ResultResponse:
    """Return the git commits that changed the project (none outside a repository)."""
    try:
        return _result(service.history(store.project_dir(project)))
    except ServiceError as error:
        raise _service_error(error) from error


@router.post("/projects/{project}/compat")
def compatibility_with_revision(store: Store, project: str, body: RevisionCompat) -> ResultResponse:
    """Compare the project at a git revision (old) with the working copy (new)."""
    try:
        location = store.project_dir(project)
        return _result(
            service.compatibility_with_revision(location, body.revision, body.interfaces)
        )
    except ServiceError as error:
        raise _service_error(error) from error


def _target(store: Workspace, project: str, body: CodecRequest) -> service.CodecTarget:
    plugins = service.load_plugins(service.find_plugins(store.project_dir(project)))
    return service.CodecTarget(body.message, body.interface, body.profile, plugins)


@router.post("/projects/{project}/encode")
def encode(store: Store, project: str, body: EncodeRequest) -> ResultResponse:
    """Encode a message value (payload, and the frame when an interface is given)."""
    try:
        target = _target(store, project, body)
        return _result(service.encode(store.project_dir(project), body.value, target))
    except ServiceError as error:
        raise _service_error(error) from error


@router.post("/projects/{project}/decode")
def decode(store: Store, project: str, body: DecodeRequest) -> ResultResponse:
    """Decode hex bytes: a payload with `message`, otherwise a frame of `interface`."""
    try:
        data = bytes.fromhex(body.hex)
    except ValueError as error:
        raise HTTPException(400, f"{body.hex!r} is not hex") from error
    try:
        target = _target(store, project, body)
        return _result(service.decode(store.project_dir(project), data, target))
    except ServiceError as error:
        raise _service_error(error) from error


# --- application --------------------------------------------------------------------------------


def _storage_error(request: Request, error: Exception) -> Response:
    del request
    status = error.status if isinstance(error, StorageError) else 500
    return JSONResponse({"detail": str(error)}, status_code=status)


def create_app(root: Path, token: str | None = None, oidc: OidcConfig | None = None) -> FastAPI:
    """Return the application serving the projects below `root`.

    Requests need the bearer `token` or, with `oidc`, a JWT of that issuer and audience.
    """
    app = FastAPI(
        title="WireForge",
        version=versioning.TOOL_VERSION,
        description="Projects, validation and editing of WireForge wire-format definitions.",
    )
    app.state.workspace = Workspace(root)
    app.state.token = token
    app.state.oidc_verifier = OidcVerifier(oidc) if oidc is not None else None
    app.add_exception_handler(StorageError, _storage_error)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": versioning.TOOL_VERSION}

    app.include_router(router)
    ui = resources.ui_dir()
    if ui is not None:
        app.mount("/", StaticFiles(directory=ui, html=True), name="ui")
    return app
