// Client of the WireForge server API; request and response shapes come from the OpenAPI types.
import type { components } from "./generated/api";

/** A validation issue of the server. */
export type Issue = components["schemas"]["IssueModel"];
/** The result of a server operation. */
export type ServiceResult = components["schemas"]["ResultResponse"];
/** Body of an entry write. */
export type EntryWrite = components["schemas"]["EntryWrite"];
/** Body of an encode request. */
export type EncodeRequest = components["schemas"]["EncodeRequest"];
/** Body of a decode request. */
export type DecodeRequest = components["schemas"]["DecodeRequest"];

/** Summary of a project of the workspace. */
export interface ProjectSummary {
  id: string;
  name: string | null;
  version: string | null;
  etag: string;
}

/** Error answer of the server. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Calls the server; keeps the last project ETag for optimistic locking. */
export class Api {
  private readonly etags = new Map<string, string>();

  constructor(
    private readonly base: string,
    private readonly token?: string,
  ) {}

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    const headers = new Headers(init.headers);
    headers.set("Content-Type", "application/json");
    if (this.token !== undefined) headers.set("Authorization", `Bearer ${this.token}`);
    const response = await fetch(`${this.base}${path}`, { ...init, headers });
    const body: unknown = await response.json();
    if (!response.ok) {
      const detail = (body as { detail?: unknown }).detail;
      throw new ApiError(response.status, typeof detail === "string" ? detail : response.statusText);
    }
    return body;
  }

  private remember(project: string, result: ServiceResult): ServiceResult {
    if (typeof result.etag === "string") this.etags.set(project, result.etag);
    return result;
  }

  /** Projects of the workspace. */
  async projects(): Promise<ProjectSummary[]> {
    return (await this.request("/api/projects")) as ProjectSummary[];
  }

  /** A project: validation result, files and entry names per section. */
  async project(id: string): Promise<ServiceResult> {
    return this.remember(id, (await this.request(`/api/projects/${id}`)) as ServiceResult);
  }

  /** One section entry as written in the files. */
  async entry(id: string, section: string, name: string): Promise<ServiceResult> {
    const path = `/api/projects/${id}/entries/${section}/${encodeURIComponent(name)}`;
    return this.remember(id, (await this.request(path)) as ServiceResult);
  }

  /** Create or update a section entry (uses the last known project ETag). */
  async putEntry(id: string, section: string, name: string, body: EntryWrite): Promise<ServiceResult> {
    if (!this.etags.has(id)) await this.project(id);
    const path = `/api/projects/${id}/entries/${section}/${encodeURIComponent(name)}`;
    const result = (await this.request(path, {
      method: "PUT",
      body: JSON.stringify(body),
      headers: { "If-Match": this.etags.get(id) ?? "" },
    })) as ServiceResult;
    return this.remember(id, result);
  }

  /** The value at a JSON pointer as written in the files (`/` is the root document). */
  async value(id: string, pointer: string): Promise<ServiceResult> {
    const path = `/api/projects/${id}/value?pointer=${encodeURIComponent(pointer)}`;
    return this.remember(id, (await this.request(path)) as ServiceResult);
  }

  /** Set the value at a JSON pointer (uses the last known project ETag). */
  async putValue(id: string, pointer: string, value: unknown): Promise<ServiceResult> {
    if (!this.etags.has(id)) await this.project(id);
    const path = `/api/projects/${id}/value?pointer=${encodeURIComponent(pointer)}`;
    const result = (await this.request(path, {
      method: "PUT",
      body: JSON.stringify({ value }),
      headers: { "If-Match": this.etags.get(id) ?? "" },
    })) as ServiceResult;
    return this.remember(id, result);
  }

  /** Delete the element at a JSON pointer (uses the last known project ETag). */
  async deleteValue(id: string, pointer: string): Promise<ServiceResult> {
    if (!this.etags.has(id)) await this.project(id);
    const path = `/api/projects/${id}/value?pointer=${encodeURIComponent(pointer)}`;
    const result = (await this.request(path, {
      method: "DELETE",
      headers: { "If-Match": this.etags.get(id) ?? "" },
    })) as ServiceResult;
    return this.remember(id, result);
  }

  /** Layout report of a project (`data`) for one profile or all. */
  async analyze(id: string, profile?: string): Promise<ServiceResult> {
    const query = profile === undefined ? "" : `?profile=${encodeURIComponent(profile)}`;
    return (await this.request(`/api/projects/${id}/analyze${query}`)) as ServiceResult;
  }

  /** Encode a message value. */
  async encode(id: string, body: EncodeRequest): Promise<ServiceResult> {
    return (await this.request(`/api/projects/${id}/encode`, { method: "POST", body: JSON.stringify(body) })) as ServiceResult;
  }

  /** Decode hex bytes. */
  async decode(id: string, body: DecodeRequest): Promise<ServiceResult> {
    return (await this.request(`/api/projects/${id}/decode`, { method: "POST", body: JSON.stringify(body) })) as ServiceResult;
  }

  /** Commits that changed the project. */
  async history(id: string): Promise<ServiceResult> {
    return (await this.request(`/api/projects/${id}/history`)) as ServiceResult;
  }

  /** Compatibility of the project at `revision` (old) with the working copy (new). */
  async compatWithRevision(id: string, revision: string): Promise<ServiceResult> {
    return (await this.request(`/api/projects/${id}/compat`, {
      method: "POST",
      body: JSON.stringify({ revision }),
    })) as ServiceResult;
  }

  /** Generated C++ files of a project. */
  async preview(id: string): Promise<ServiceResult> {
    return (await this.request(`/api/projects/${id}/preview`)) as ServiceResult;
  }
}
