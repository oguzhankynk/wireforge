import { useEffect, useState } from "react";
import type { Api, ServiceResult } from "../api";
import type { Field } from "../generated/schema";
import {
  defaultSettings,
  emptyRow,
  fromField,
  fromSettings,
  MESSAGE_KEYS,
  numberOrExpression,
  rowProblems,
  toSettings,
  toStruct,
  TYPE_KEYS,
  withEdits,
  type FieldRow,
  type MessageSettings,
} from "../model";
import { FieldTable } from "./FieldTable";
import { SettingsFields } from "./SettingsFields";

/** Props of the struct editor. */
export interface StructEditorProps {
  api: Api;
  project: string;
  section: "types" | "messages";
  /** Entry to edit; null creates a new one. */
  name: string | null;
  typeOptions: readonly string[];
  checksumOptions: readonly string[];
  onSaved: (name: string, result: ServiceResult) => void;
}

interface Definition {
  id?: number;
  description?: string;
  fields?: Field[];
  [key: string]: unknown;
}

/** Table editor of a type or message: one row per field, saved through the server. */
export function StructEditor(props: StructEditorProps) {
  const { api, project, section, name, typeOptions, checksumOptions, onSaved } = props;
  const [entryName, setEntryName] = useState(name ?? "");
  const [description, setDescription] = useState("");
  const [messageId, setMessageId] = useState("");
  const [rows, setRows] = useState<FieldRow[]>([emptyRow("field1")]);
  const [settings, setSettings] = useState<MessageSettings>(defaultSettings);
  // The loaded definition: keys this editor does not show (if, since, ...) are saved unchanged.
  const [original, setOriginal] = useState<Definition>({});
  const [error, setError] = useState("");

  useEffect(() => {
    if (name === null) return;
    let active = true;
    void api.entry(project, section, name).then((result) => {
      if (!active) return;
      const value = (result.data as { value?: Definition }).value ?? {};
      setEntryName(name);
      setDescription(value.description ?? "");
      setMessageId(value.id === undefined ? "" : String(value.id));
      setRows((value.fields ?? []).map(fromField));
      setSettings(fromSettings(value));
      setOriginal(value);
    });
    return () => {
      active = false;
    };
  }, [api, project, section, name]);

  const problems = rowProblems(rows);

  const save = async () => {
    const edited: Record<string, unknown> = { ...toStruct(rows, description) };
    if (section === "messages") {
      Object.assign(edited, toSettings(settings));
      if (messageId.trim() !== "") edited.id = numberOrExpression(messageId);
    }
    const managed = section === "messages" ? MESSAGE_KEYS : TYPE_KEYS;
    const definition = withEdits(original, edited, managed);
    try {
      onSaved(entryName.trim(), await api.putEntry(project, section, entryName.trim(), { definition }));
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };

  const kind = section === "types" ? "Type" : "Message";
  return (
    <section aria-label={`${kind} editor`}>
      <h2>{kind}</h2>
      <label>
        {kind} name{" "}
        <input
          value={entryName}
          readOnly={name !== null}
          onChange={(event) => {
            setEntryName(event.target.value);
          }}
        />
      </label>
      {section === "messages" && (
        <label>
          Message id <input value={messageId} onChange={(event) => { setMessageId(event.target.value); }} />
        </label>
      )}
      <label>
        Description <input value={description} onChange={(event) => { setDescription(event.target.value); }} />
      </label>
      {section === "messages" && (
        <SettingsFields settings={settings} onChange={setSettings} inherit="(project default)" />
      )}
      <FieldTable
        label="Field"
        rows={rows}
        onChange={setRows}
        typeOptions={typeOptions}
        checksumOptions={checksumOptions}
      />
      <button
        type="button"
        className="primary"
        disabled={entryName.trim() === "" || problems.length > 0}
        onClick={() => void save()}
      >
        Save {kind.toLowerCase()}
      </button>
      {problems.length > 0 && (
        <ul aria-label="Field problems">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {error !== "" && <p role="alert">{error}</p>}
    </section>
  );
}
