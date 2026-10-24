import { useEffect, useState } from "react";
import type { Api, ServiceResult } from "../api";
import { ENUM_TYPES, enumProblems, fromEnum, toEnum, withEdits, type EnumForm } from "../model";

/** Editor of an enumeration: underlying type, description and members. */
export function EnumEditor(props: {
  api: Api;
  project: string;
  /** Enum to edit; null creates a new one. */
  name: string | null;
  onSaved: (name: string, result: ServiceResult) => void;
}) {
  const { api, project, name, onSaved } = props;
  const [entryName, setEntryName] = useState(name ?? "");
  const [form, setForm] = useState<EnumForm>({ type: "u8", description: "", values: [{ name: "", value: "0" }] });
  const [original, setOriginal] = useState<Record<string, unknown>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    if (name === null) return;
    let active = true;
    void api.entry(project, "enums", name).then((result) => {
      const value = (result.data as { value?: Record<string, unknown> }).value;
      if (!active || value === undefined) return;
      setForm(fromEnum(value));
      setOriginal(value);
    });
    return () => {
      active = false;
    };
  }, [api, project, name]);

  const change = (index: number, update: Partial<EnumForm["values"][number]>) => {
    setForm((current) => ({
      ...current,
      values: current.values.map((row, at) => (at === index ? { ...row, ...update } : row)),
    }));
  };
  const nextValue = () => {
    const numbers = form.values.map((row) => Number(row.value)).filter(Number.isInteger);
    return String(numbers.length === 0 ? 0 : Math.max(...numbers) + 1);
  };
  const problems = enumProblems(form);

  const save = async () => {
    try {
      const definition = withEdits(original, toEnum(form), ["type", "description", "values"]);
      onSaved(entryName.trim(), await api.putEntry(project, "enums", entryName.trim(), { definition }));
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };

  return (
    <section aria-label="Enum editor">
      <h2>Enum</h2>
      <label>
        Enum name{" "}
        <input value={entryName} readOnly={name !== null} onChange={(event) => { setEntryName(event.target.value); }} />
      </label>
      <label>
        Underlying type
        <select value={form.type} onChange={(event) => { setForm({ ...form, type: event.target.value }); }}>
          {ENUM_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </label>
      <label>
        Description{" "}
        <input value={form.description} onChange={(event) => { setForm({ ...form, description: event.target.value }); }} />
      </label>
      {form.values.map((row, index) => (
        <div key={index}>
          <input
            aria-label={`Member ${String(index + 1)} name`}
            value={row.name}
            onChange={(event) => { change(index, { name: event.target.value }); }}
          />
          <input
            aria-label={`Member ${String(index + 1)} value`}
            value={row.value}
            onChange={(event) => { change(index, { value: event.target.value }); }}
          />
          <button
            type="button"
            aria-label={`Member ${String(index + 1)} remove`}
            onClick={() => { setForm({ ...form, values: form.values.filter((_, at) => at !== index) }); }}
          >
            ✕
          </button>
        </div>
      ))}
      <button type="button" onClick={() => { setForm({ ...form, values: [...form.values, { name: "", value: nextValue() }] }); }}>
        Add member
      </button>
      <button type="button" className="primary" disabled={entryName.trim() === "" || problems.length > 0} onClick={() => void save()}>
        Save enum
      </button>
      {problems.length > 0 && (
        <ul aria-label="Enum problems">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {error !== "" && <p role="alert">{error}</p>}
    </section>
  );
}
