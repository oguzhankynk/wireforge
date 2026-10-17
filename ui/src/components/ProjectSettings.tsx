import { useEffect, useState } from "react";
import type { Api, ServiceResult } from "../api";
import { fromProject, PROJECT_KEYS, toProject, type ProjectForm } from "../model";
import { SettingsFields } from "./SettingsFields";

/** Editor of the project root: name, version, description, namespace, defaults and defines. */
export function ProjectSettings(props: {
  api: Api;
  project: string;
  onSaved: (result: ServiceResult) => void;
}) {
  const { api, project, onSaved } = props;
  const [form, setForm] = useState<ProjectForm | null>(null);
  const [original, setOriginal] = useState<Record<string, unknown>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api.value(project, "/").then((result) => {
      const value = (result.data as { value?: Record<string, unknown> }).value ?? {};
      if (!active) return;
      setForm(fromProject(value));
      setOriginal(value);
    });
    return () => {
      active = false;
    };
  }, [api, project]);

  if (form === null) return <p>Loading project settings…</p>;

  const set = (change: Partial<ProjectForm>) => {
    setForm({ ...form, ...change });
  };
  const changeDefine = (index: number, update: Partial<ProjectForm["defines"][number]>) => {
    set({ defines: form.defines.map((row, at) => (at === index ? { ...row, ...update } : row)) });
  };
  const nameValid = /^[A-Za-z_][A-Za-z0-9_]*$/.test(form.name.trim());

  // Writes only the root keys that changed; a cleared key is removed from the file.
  const save = async () => {
    try {
      const edited = toProject(form);
      let last: ServiceResult | null = null;
      for (const key of PROJECT_KEYS) {
        const value = edited[key];
        if (value === undefined) {
          if (key in original) last = await api.deleteValue(project, `/${key}`);
        } else if (JSON.stringify(value) !== JSON.stringify(original[key])) {
          last = await api.putValue(project, `/${key}`, value);
        }
      }
      const reloaded = await api.value(project, "/");
      setOriginal((reloaded.data as { value?: Record<string, unknown> }).value ?? {});
      onSaved(last ?? reloaded);
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };

  const text = (key: "name" | "version" | "description" | "namespace", label: string) => (
    <label>
      {label}{" "}
      <input
        value={form[key]}
        onChange={(event) => {
          set({ [key]: event.target.value });
        }}
      />
    </label>
  );

  return (
    <section aria-label="Project settings">
      <h2>Project settings</h2>
      {text("name", "Project name")}
      {text("version", "Project version")}
      {text("description", "Project description")}
      {text("namespace", "C++ namespace")}
      <SettingsFields
        settings={form.defaults}
        onChange={(defaults) => { set({ defaults }); }}
        inherit="(built-in default)"
      />
      <fieldset>
        <legend>Defines</legend>
        {form.defines.map((row, index) => (
          <div key={index}>
            <input
              aria-label={`Project define ${String(index + 1)} name`}
              value={row.name}
              onChange={(event) => { changeDefine(index, { name: event.target.value }); }}
            />
            <input
              aria-label={`Project define ${String(index + 1)} value`}
              value={row.value}
              onChange={(event) => { changeDefine(index, { value: event.target.value }); }}
            />
            <button
              type="button"
              aria-label={`Project define ${String(index + 1)} remove`}
              onClick={() => { set({ defines: form.defines.filter((_, at) => at !== index) }); }}
            >
              ✕
            </button>
          </div>
        ))}
        <button type="button" onClick={() => { set({ defines: [...form.defines, { name: "", value: "0" }] }); }}>
          Add project define
        </button>
      </fieldset>
      <button type="button" disabled={!nameValid} onClick={() => void save()}>
        Save project settings
      </button>
      {!nameValid && <p>The project name must be an identifier.</p>}
      {error !== "" && <p role="alert">{error}</p>}
    </section>
  );
}
