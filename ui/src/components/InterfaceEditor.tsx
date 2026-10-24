import { useEffect, useState } from "react";
import type { Api, ServiceResult } from "../api";
import type { InterfaceDef } from "../generated/schema";
import {
  emptyInterface,
  fromInterface,
  INTERFACE_KEYS,
  rowProblems,
  toInterface,
  withEdits,
  type InterfaceForm,
} from "../model";
import { FieldTable } from "./FieldTable";

const FRAMINGS: readonly InterfaceForm["framing"][] = ["none", "fixed", "sync_length", "length_prefixed"];
const LENGTH_COUNTS: readonly InterfaceForm["lengthCounts"][] = ["", "payload", "payload_and_trailer", "frame"];

/** Props of the interface editor. */
export interface InterfaceEditorProps {
  api: Api;
  project: string;
  /** Interface to edit; null creates a new one. */
  name: string | null;
  messageOptions: readonly string[];
  typeOptions: readonly string[];
  checksumOptions: readonly string[];
  onSaved: (name: string, result: ServiceResult) => void;
}

/** Editor of one interface: version, header and trailer fields, framing, messages, buffers. */
export function InterfaceEditor(props: InterfaceEditorProps) {
  const { api, project, name, messageOptions, typeOptions, checksumOptions, onSaved } = props;
  const [entryName, setEntryName] = useState(name ?? "");
  const [form, setForm] = useState<InterfaceForm>(emptyInterface);
  // The loaded definition: keys this editor does not show (bit_order, range_check) stay unchanged.
  const [original, setOriginal] = useState<Record<string, unknown>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    if (name === null) return;
    let active = true;
    void api.entry(project, "interfaces", name).then((result) => {
      const value = (result.data as { value?: InterfaceDef }).value;
      if (active && value !== undefined) {
        setForm(fromInterface(value));
        setOriginal(value as unknown as Record<string, unknown>);
      }
    });
    return () => {
      active = false;
    };
  }, [api, project, name]);

  const set = (change: Partial<InterfaceForm>) => {
    setForm((current) => ({ ...current, ...change }));
  };
  const problems = [...rowProblems(form.header), ...rowProblems(form.trailer)];
  if (form.messages.every((row) => row.name.trim() === "")) problems.push("the interface needs a message");

  const save = async () => {
    try {
      const edited = toInterface(form) as unknown as Record<string, unknown>;
      const definition = withEdits(original, edited, INTERFACE_KEYS);
      onSaved(entryName.trim(), await api.putEntry(project, "interfaces", entryName.trim(), { definition }));
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };

  const text = (key: "version" | "description" | "frameSize" | "maxFrameSize" | "rxBytes" | "txBytes", label: string) => (
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
    <section aria-label="Interface editor">
      <h2>Interface</h2>
      <label>
        Interface name{" "}
        <input
          value={entryName}
          readOnly={name !== null}
          onChange={(event) => {
            setEntryName(event.target.value);
          }}
        />
      </label>
      {text("version", "Version")}
      {text("description", "Description")}
      <label>
        Direction
        <select value={form.direction} onChange={(event) => { set({ direction: event.target.value as InterfaceForm["direction"] }); }}>
          <option value="tx">tx</option>
          <option value="rx">rx</option>
          <option value="both">both</option>
        </select>
      </label>
      <label>
        Endian
        <select value={form.endian} onChange={(event) => { set({ endian: event.target.value as InterfaceForm["endian"] }); }}>
          <option value="">(project default)</option>
          <option value="big">big</option>
          <option value="little">little</option>
        </select>
      </label>
      <fieldset>
        <legend>Header</legend>
        <label>
          <input
            type="checkbox"
            checked={form.rawHeader}
            onChange={(event) => { set({ rawHeader: event.target.checked }); }}
          />{" "}
          No header (raw dispatch)
        </label>
        {!form.rawHeader && (
          <FieldTable
            label="Header field"
            rows={form.header}
            onChange={(rows) => { set({ header: rows }); }}
            typeOptions={typeOptions}
            checksumOptions={checksumOptions}
            frame
          />
        )}
      </fieldset>
      <fieldset>
        <legend>Trailer</legend>
        <FieldTable
          label="Trailer field"
          rows={form.trailer}
          onChange={(rows) => { set({ trailer: rows }); }}
          typeOptions={typeOptions}
          checksumOptions={checksumOptions}
          frame
        />
      </fieldset>
      <fieldset>
        <legend>Framing</legend>
        <label>
          Framing kind
          <select value={form.framing} onChange={(event) => { set({ framing: event.target.value as InterfaceForm["framing"] }); }}>
            {FRAMINGS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </label>
        {text("frameSize", "Frame size")}
        {text("maxFrameSize", "Max frame size")}
        <label>
          Length counts
          <select
            value={form.lengthCounts}
            onChange={(event) => { set({ lengthCounts: event.target.value as InterfaceForm["lengthCounts"] }); }}
          >
            {LENGTH_COUNTS.map((kind) => (
              <option key={kind} value={kind}>
                {kind === "" ? "(default)" : kind}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input type="checkbox" checked={form.resync} onChange={(event) => { set({ resync: event.target.checked }); }} /> Resync
        </label>
      </fieldset>
      <fieldset>
        <legend>Messages</legend>
        {form.messages.map((row, index) => (
          <div key={index}>
            <select
              aria-label={`Message ${String(index + 1)} name`}
              value={row.name}
              onChange={(event) => {
                set({ messages: form.messages.map((item, at) => (at === index ? { ...item, name: event.target.value } : item)) });
              }}
            >
              <option value="">-</option>
              {messageOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
            <input
              aria-label={`Message ${String(index + 1)} id`}
              placeholder="id override"
              value={row.id}
              onChange={(event) => {
                set({ messages: form.messages.map((item, at) => (at === index ? { ...item, id: event.target.value } : item)) });
              }}
            />
            <button
              type="button"
              aria-label={`Message ${String(index + 1)} remove`}
              onClick={() => { set({ messages: form.messages.filter((_, at) => at !== index) }); }}
            >
              ✕
            </button>
          </div>
        ))}
        <button type="button" onClick={() => { set({ messages: [...form.messages, { name: "", id: "" }] }); }}>
          Add message
        </button>
      </fieldset>
      <fieldset>
        <legend>Buffers</legend>
        {text("rxBytes", "Receive buffer bytes")}
        {text("txBytes", "Transmit buffer bytes")}
        <label>
          <input
            type="checkbox"
            checked={form.allowTrailingBytes}
            onChange={(event) => { set({ allowTrailingBytes: event.target.checked }); }}
          />{" "}
          Allow trailing bytes
        </label>
        <label>
          <input
            type="checkbox"
            checked={form.trimTrailingZeros}
            onChange={(event) => { set({ trimTrailingZeros: event.target.checked }); }}
          />{" "}
          Trim trailing zeros
        </label>
      </fieldset>
      <button type="button" className="primary" disabled={entryName.trim() === "" || problems.length > 0} onClick={() => void save()}>
        Save interface
      </button>
      {problems.length > 0 && (
        <ul aria-label="Interface problems">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {error !== "" && <p role="alert">{error}</p>}
    </section>
  );
}
