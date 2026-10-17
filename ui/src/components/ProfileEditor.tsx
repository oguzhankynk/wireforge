import { useEffect, useState } from "react";
import type { Api, ServiceResult } from "../api";
import type { ProfileDef } from "../generated/schema";
import { fromProfile, toProfile, type DefineRow } from "../model";

/** Editor of a platform profile: description and define values. */
export function ProfileEditor(props: {
  api: Api;
  project: string;
  name: string | null;
  onSaved: (name: string, result: ServiceResult) => void;
}) {
  const { api, project, name, onSaved } = props;
  const [entryName, setEntryName] = useState(name ?? "");
  const [description, setDescription] = useState("");
  const [rows, setRows] = useState<DefineRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (name === null) return;
    let active = true;
    void api.entry(project, "profiles", name).then((result) => {
      const value = (result.data as { value?: ProfileDef }).value;
      if (!active || value === undefined) return;
      setDescription(value.description ?? "");
      setRows(fromProfile(value));
    });
    return () => {
      active = false;
    };
  }, [api, project, name]);

  const save = async () => {
    try {
      const definition = toProfile(description, rows) as unknown as Record<string, unknown>;
      onSaved(entryName.trim(), await api.putEntry(project, "profiles", entryName.trim(), { definition }));
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };
  const change = (index: number, update: Partial<DefineRow>) => {
    setRows((current) => current.map((row, at) => (at === index ? { ...row, ...update } : row)));
  };

  return (
    <section aria-label="Profile editor">
      <h2>Profile</h2>
      <label>
        Profile name{" "}
        <input value={entryName} readOnly={name !== null} onChange={(event) => { setEntryName(event.target.value); }} />
      </label>
      <label>
        Description <input value={description} onChange={(event) => { setDescription(event.target.value); }} />
      </label>
      {rows.map((row, index) => (
        <div key={index}>
          <input
            aria-label={`Define ${String(index + 1)} name`}
            value={row.name}
            onChange={(event) => { change(index, { name: event.target.value }); }}
          />
          <input
            aria-label={`Define ${String(index + 1)} value`}
            value={row.value}
            onChange={(event) => { change(index, { value: event.target.value }); }}
          />
        </div>
      ))}
      <button type="button" onClick={() => { setRows((current) => [...current, { name: "", value: "0" }]); }}>
        Add define
      </button>
      <button type="button" disabled={entryName.trim() === ""} onClick={() => void save()}>
        Save profile
      </button>
      {error !== "" && <p role="alert">{error}</p>}
    </section>
  );
}
