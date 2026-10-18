import { useEffect, useState } from "react";
import { ApiError, type Api, type TemplateSummary } from "../api";

const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;
const PROJECT_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;

/** Start of a project: pick a template, name it, and the first working model appears. */
export function NewProject(props: { api: Api; onCreated: (id: string) => void; onCancel: () => void }) {
  const { api, onCreated, onCancel } = props;
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [template, setTemplate] = useState("telemetry");
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [namespace, setNamespace] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api.templates().then((list) => {
      if (active) setTemplates(list);
    });
    return () => {
      active = false;
    };
  }, [api]);

  const valid = PROJECT_ID.test(id) && IDENTIFIER.test(name);

  const create = async () => {
    setError("");
    try {
      await api.createProject({
        id,
        name,
        template,
        ...(namespace.trim() === "" ? {} : { namespace: namespace.trim() }),
      });
      onCreated(id);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : String(failure));
    }
  };

  return (
    <section aria-label="New project">
      <h2>New project</h2>
      <p>Start from a working model and change it to match your protocol.</p>
      <label>
        Template
        <select value={template} onChange={(event) => { setTemplate(event.target.value); }}>
          {templates.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <p>{templates.find((item) => item.name === template)?.summary ?? ""}</p>
      <label>
        Project id (directory)
        <input value={id} onChange={(event) => { setId(event.target.value); }} />
      </label>
      <label>
        Project name (C++ identifier)
        <input value={name} onChange={(event) => { setName(event.target.value); }} />
      </label>
      <label>
        C++ namespace (optional)
        <input value={namespace} onChange={(event) => { setNamespace(event.target.value); }} />
      </label>
      {error !== "" && <p role="alert">{error}</p>}
      <button type="button" disabled={!valid} onClick={() => { void create(); }}>
        Create project
      </button>
      <button type="button" onClick={onCancel}>
        Cancel
      </button>
    </section>
  );
}
